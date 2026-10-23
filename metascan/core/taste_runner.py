"""Server-side driver for the taste scorer worker.

Builds the training labels from the DB, hands them to the one-shot
``metascan.workers.taste_worker`` subprocess (numpy + faiss, never in the
FastAPI process), and persists the resulting model row and scores.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from metascan.core.taste_labels import LabelSet, build_label_set, missing_for_training
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

LABELS_VERSION = 1
KEEP_PARAMS = 5
WORKER_TIMEOUT_S = 1800.0
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _labels_doc(ls: LabelSet) -> Dict[str, Any]:
    return {
        "version": LABELS_VERSION,
        "as_of": ls.as_of,
        "rows": [
            {
                "key": r.key,
                "file_path": r.file_path,
                "label": r.label,
                "weight": r.weight,
                "categories": list(r.categories),
                "batch_id": r.batch_id,
                "embedding": (
                    base64.b64encode(r.embedding).decode("ascii")
                    if r.embedding
                    else None
                ),
                "embedding_model": r.embedding_model,
            }
            for r in ls.rows
        ],
        "pairs": [[a, b, w] for a, b, w in ls.pairs],
    }


def _read_result(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = json.loads(path.read_text())
    return result


def _write_json(path: Path, doc: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


class TasteRunner:
    def __init__(
        self,
        db: Any,
        index_dir: Path,
        work_dir: Path,
        python: str = sys.executable,
    ) -> None:
        self.db = db
        self.index_dir = Path(index_dir)
        self.work_dir = Path(work_dir)
        self.python = python
        self.models_dir = self.work_dir / "models"
        self.timeout_s = WORKER_TIMEOUT_S
        self._training = False
        self._rescoring = False

    @property
    def training(self) -> bool:
        return self._training

    # ------------------------------------------------------------------
    # Index metadata
    # ------------------------------------------------------------------

    def _index_meta(self) -> Optional[Dict[str, Any]]:
        try:
            meta = json.loads((self.index_dir / "index_meta.json").read_text())
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) else None

    def is_stale(self, model: Dict[str, Any]) -> bool:
        """True when the index on disk was built with a different CLIP model
        or dimension than ``model`` (or there is no readable index)."""
        meta = self._index_meta()
        if meta is None:
            return True
        return str(meta.get("model_key") or "") != str(
            model.get("embedding_model") or ""
        ) or int(meta.get("embedding_dim") or 0) != int(model.get("dim") or 0)

    def index_fingerprint(self) -> Optional[Tuple[str, int, float]]:
        """``(model_key, embedding_dim, index_meta.json mtime)`` or None when
        unreadable. Changes whenever the index is rewritten. Blocking."""
        meta = self._index_meta()
        if meta is None:
            return None
        try:
            mtime = (self.index_dir / "index_meta.json").stat().st_mtime
        except OSError:
            return None
        return (
            str(meta.get("model_key") or ""),
            int(meta.get("embedding_dim") or 0),
            float(mtime),
        )

    def _indexed_paths(self) -> set:
        try:
            with open(self.index_dir / "id_mapping.json", "r") as f:
                return set(json.load(f))
        except (OSError, ValueError):
            return set()

    def _scoring_targets(
        self, raw: Dict[str, Any]
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """Every live image (``raw`` already excludes videos) that has a
        vector in the index, plus its categories."""
        indexed = self._indexed_paths()
        paths: List[str] = []
        cats: Dict[str, List[str]] = {}
        for m in raw.get("media", []):
            p = m["file_path"]
            if p in indexed or to_native_path(p) in indexed:
                paths.append(p)
                if m.get("categories"):
                    cats[p] = list(m["categories"])
        return paths, cats

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _drain(self, stream: Optional[asyncio.StreamReader]) -> None:
        if stream is None:
            return
        while True:
            try:
                line = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # A line over the reader's limit (64 KiB default): readline
                # has already dropped it from the buffer. Keep draining, or
                # the worker blocks on a full stderr pipe until the timeout.
                logger.info("taste_worker: <overlong stderr line dropped>")
                continue
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info("taste_worker: %s", text)

    async def _run_worker(self, task: Dict[str, Any]) -> Dict[str, Any]:
        out_dir = Path(task["out_dir"])
        task_path = out_dir / "taste_task.json"
        await asyncio.to_thread(_write_json, task_path, task)
        env = dict(os.environ)
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(_PROJECT_ROOT), env.get("PYTHONPATH")) if p
        )
        proc = await asyncio.create_subprocess_exec(
            self.python,
            "-m",
            "metascan.workers.taste_worker",
            str(task_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
            env=env,
        )
        drain = asyncio.create_task(self._drain(proc.stderr))
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            return {"status": "error", "error": "taste worker timed out"}
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            await drain
        try:
            # A full-library score map: parse it off the event loop.
            result = await asyncio.to_thread(
                _read_result, out_dir / "taste_result.json"
            )
        except (OSError, ValueError):
            return {
                "status": "error",
                "error": f"taste worker exited {code} without a result",
            }
        if code != 0 and result.get("status") == "ok":
            return {"status": "error", "error": f"taste worker exited {code}"}
        return result

    def _new_run_dir(self, mode: str) -> Path:
        run_dir = self.work_dir / "runs" / f"{mode}-{uuid.uuid4().hex}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _install_params(self, src: Path) -> Path:
        """Move the worker's params file into ``models_dir``. Blocking."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        params_path = self.models_dir / src.name
        os.replace(src, params_path)
        return params_path

    def _prune_params(self, keep: Path) -> None:
        files = sorted(self.models_dir.glob("taste_*.npz"), key=lambda p: p.name)
        for old in files[:-KEEP_PARAMS]:
            if old != keep:
                old.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Train / rescore
    # ------------------------------------------------------------------

    async def train(
        self, half_life_days: float, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        if self._training:
            raise RuntimeError("already_training")
        self._training = True
        try:
            return await self._train(half_life_days, now)
        finally:
            self._training = False

    async def _train(
        self, half_life_days: float, now: Optional[datetime]
    ) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)

        def _snapshot() -> Tuple[Dict[str, Any], LabelSet]:
            raw = self.db.taste_training_raw()
            return raw, build_label_set(raw, now, half_life_days)

        raw, ls = await asyncio.to_thread(_snapshot)
        missing = missing_for_training(ls.n_pos, ls.n_neg)
        if missing is not None:
            return {
                "status": "not_enough_data",
                "missing": missing,
                "n_pos": ls.n_pos,
                "n_neg": ls.n_neg,
            }

        run_dir = self._new_run_dir("train")
        try:
            try:
                image_paths, image_cats = await asyncio.to_thread(
                    self._scoring_targets, raw
                )
                labels_path = run_dir / "labels.json"
                await asyncio.to_thread(_write_json, labels_path, _labels_doc(ls))
                result = await self._run_worker(
                    {
                        "mode": "train",
                        "index_dir": str(self.index_dir),
                        "labels_path": str(labels_path),
                        "model_path": None,
                        "out_dir": str(run_dir),
                        "image_paths": image_paths,
                        "image_categories": image_cats,
                    }
                )
            except Exception as e:
                logger.exception("taste training failed")
                result = {"status": "error", "error": str(e) or repr(e)}
            if result.get("status") == "not_enough_data":
                # Enough labels, too few with a vector (a partial index):
                # not a failure -- no failed row, nothing activated.
                logger.info(
                    "taste training skipped: labels lack vectors (%s)",
                    result.get("missing"),
                )
                return result
            if result.get("status") != "ok":
                return await self._record_failure(ls, half_life_days, result)
            return await self._record_success(ls, half_life_days, result)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    async def _record_failure(
        self, ls: LabelSet, half_life_days: float, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        error = str(result.get("error") or "taste worker failed")
        meta = self._index_meta() or {}
        row = {
            "status": "failed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "labels_as_of": ls.as_of,
            "embedding_model": str(meta.get("model_key") or ""),
            "dim": int(meta.get("embedding_dim") or 0),
            "categories": [],
            "n_pos": ls.n_pos,
            "n_neg": ls.n_neg,
            "n_pairs": len(ls.pairs),
            "holdout_auc": None,
            "half_life_days": half_life_days,
            "params_path": "",
            "error": error,
        }
        try:
            await asyncio.to_thread(self.db.record_taste_model, row)
        except Exception:
            logger.exception("failed to record failed taste model row")
        logger.warning("taste training failed: %s", error)
        return {"status": "error", "error": error}

    async def _record_success(
        self, ls: LabelSet, half_life_days: float, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        params_path = await asyncio.to_thread(
            self._install_params, Path(result["params_path"])
        )
        row = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "labels_as_of": ls.as_of,
            "embedding_model": result["embedding_model"],
            "dim": int(result["dim"]),
            "categories": list(result.get("categories") or []),
            "n_pos": int(result["n_pos"]),
            "n_neg": int(result["n_neg"]),
            "n_pairs": int(result["n_pairs"]),
            "holdout_auc": result.get("holdout_auc"),
            "half_life_days": half_life_days,
            "params_path": str(params_path),
            "error": None,
            "n_no_vector": int(result.get("n_no_vector") or 0),
        }
        model_id = await asyncio.to_thread(self.db.record_taste_model, row)
        scores: Dict[str, float] = result.get("scores") or {}
        n_scored: Optional[int] = None
        try:
            n_scored = await asyncio.to_thread(
                self.db.replace_taste_scores, model_id, scores
            )
        except Exception:
            # The model is recorded and active; its scores just did not
            # land. The service schedules a rescore to write them.
            logger.exception("writing taste scores for model %s failed", model_id)
        try:
            await asyncio.to_thread(self._prune_params, params_path)
        except Exception:
            logger.warning("pruning old taste params failed", exc_info=True)
        return {
            "status": "ok",
            "rescore_needed": n_scored is None,
            "model_id": model_id,
            "holdout_auc": row["holdout_auc"],
            "n_pos": row["n_pos"],
            "n_neg": row["n_neg"],
            "n_pairs": row["n_pairs"],
            "n_no_vector": row["n_no_vector"],
            "dim": row["dim"],
            "embedding_model": row["embedding_model"],
            "n_scored": n_scored,
        }

    async def rescore(self) -> Optional[int]:
        """Score every current image with the active model. None when there
        is no usable model (none, stale, or a training run owns the scores)
        or the worker failed; else the number of scores written."""
        if self._training or self._rescoring:
            return None
        self._rescoring = True
        try:
            return await self._rescore()
        finally:
            self._rescoring = False

    async def _rescore(self) -> Optional[int]:
        model = await asyncio.to_thread(self.db.get_active_taste_model)
        if model is None or await asyncio.to_thread(self.is_stale, model):
            return None
        # Paths + categories only: no judgments, events or tombstones.
        raw = await asyncio.to_thread(self.db.taste_scoring_raw)
        image_paths, image_cats = await asyncio.to_thread(self._scoring_targets, raw)
        run_dir = self._new_run_dir("score")
        try:
            result = await self._run_worker(
                {
                    "mode": "score",
                    "index_dir": str(self.index_dir),
                    "labels_path": None,
                    "model_path": model["params_path"],
                    "out_dir": str(run_dir),
                    "image_paths": image_paths,
                    "image_categories": image_cats,
                }
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        if result.get("status") != "ok":
            logger.warning("taste rescore failed: %s", result.get("error"))
            return None
        # A training run that started meanwhile owns the score table now.
        current = await asyncio.to_thread(self.db.get_active_taste_model)
        if self._training or current is None or current["id"] != model["id"]:
            return None
        n: int = await asyncio.to_thread(
            self.db.replace_taste_scores, int(model["id"]), result.get("scores") or {}
        )
        return n
