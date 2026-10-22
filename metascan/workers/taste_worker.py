"""One-shot taste scorer worker: trains the taste model and/or scores images.

Usage:
    python -m metascan.workers.taste_worker <task.json>

Task (written by ``metascan.core.taste_runner.TasteRunner``)::

    {"mode": "train" | "score", "index_dir": str, "labels_path": str | null,
     "model_path": str | null, "out_dir": str, "image_paths": [str],
     "image_categories": {path: [name, ...]}}   # optional

Writes ``<out_dir>/taste_result.json`` and exits 0, or writes
``{"status": "error", "error": str}`` and exits 1.

Reads the FAISS index read-only (the embedding worker may replace it via
tmp+rename underneath us; we read each file once). numpy + faiss only --
never torch / open_clip.
"""

import base64
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from metascan.core.taste_model import (
    TasteModelParams,
    auc,
    features,
    fit,
    predict,
    split_holdout,
    train_pairs,
)
from metascan.utils.path_utils import to_native_path, to_posix_path

LABELS_VERSION = 1
RESULT_NAME = "taste_result.json"
_RECONSTRUCT_CHUNK = 4096

logger = logging.getLogger("taste_worker")


class TasteWorkerError(Exception):
    pass


class _Index:
    """Read-only view of the on-disk FAISS index: meta, mapping, vectors."""

    def __init__(self, index_dir: Path) -> None:
        meta_path = index_dir / "index_meta.json"
        if not meta_path.exists():
            raise TasteWorkerError("similarity index has no index_meta.json")
        meta = json.loads(meta_path.read_text())
        self.model_key = str(meta.get("model_key") or "")
        self.dim = int(meta.get("embedding_dim") or 0)
        if not self.model_key or self.dim <= 0:
            raise TasteWorkerError("index_meta.json lacks model_key/embedding_dim")
        with open(index_dir / "id_mapping.json", "r") as f:
            id_to_path: List[str] = json.load(f)
        import faiss  # deferred: keep `--help`-style imports cheap

        try:
            self._index = faiss.read_index(str(index_dir / "faiss_index.bin"))
        except Exception as e:  # faiss raises RuntimeError on garbage
            raise TasteWorkerError(f"cannot read FAISS index: {e}") from e
        if int(self._index.d) != self.dim:
            raise TasteWorkerError(
                f"index dim {self._index.d} != index_meta dim {self.dim}"
            )
        ntotal = int(self._index.ntotal)
        self._path_to_id = {p: i for i, p in enumerate(id_to_path) if i < ntotal}

    def id_for(self, path: str) -> Optional[int]:
        for key in (path, to_native_path(path), to_posix_path(path)):
            idx = self._path_to_id.get(key)
            if idx is not None:
                return idx
        return None

    def vectors(self, ids: Sequence[int]) -> np.ndarray:
        out = np.zeros((len(ids), self.dim), dtype=np.float32)
        for start in range(0, len(ids), _RECONSTRUCT_CHUNK):
            chunk = np.asarray(ids[start : start + _RECONSTRUCT_CHUNK], dtype=np.int64)
            try:
                out[start : start + len(chunk)] = self._index.reconstruct_batch(chunk)
            except Exception:
                for j, idx in enumerate(chunk):
                    out[start + j] = self._index.reconstruct(int(idx))
        return out


def _training_matrix(
    labels: Dict[str, Any], index: _Index
) -> Tuple[
    np.ndarray, np.ndarray, np.ndarray, List[List[str]], List[Tuple[int, int, float]]
]:
    """Resolve each label row to a vector (tombstone embedding when its model
    matches, else the index); rows with no vector are dropped and the pairs
    remapped to the surviving rows."""
    rows = labels["rows"]
    vecs: List[Optional[np.ndarray]] = [None] * len(rows)
    need: List[Tuple[int, int]] = []
    for i, r in enumerate(rows):
        emb = r.get("embedding")
        if emb and r.get("embedding_model") == index.model_key:
            v = np.frombuffer(base64.b64decode(emb), dtype=np.float32)
            if v.size == index.dim:
                vecs[i] = v
                continue
        idx = index.id_for(r["file_path"])
        if idx is not None:
            need.append((i, idx))
    if need:
        got = index.vectors([idx for _, idx in need])
        for (i, _), v in zip(need, got):
            vecs[i] = v
    kept = [(i, v) for i, v in enumerate(vecs) if v is not None]
    keep = [i for i, _ in kept]
    skipped = len(rows) - len(keep)
    if skipped:
        logger.warning("taste worker: %d label row(s) have no vector", skipped)
    remap = {old: new for new, old in enumerate(keep)}
    pairs = [
        (remap[a], remap[b], float(w))
        for a, b, w in labels.get("pairs", [])
        if a in remap and b in remap
    ]
    emb = np.stack([v for _, v in kept]) if kept else np.zeros((0, index.dim))
    y = np.array([float(rows[i]["label"]) for i in keep], dtype=np.float64)
    w = np.array([float(rows[i]["weight"]) for i in keep], dtype=np.float64)
    cats = [list(rows[i].get("categories") or []) for i in keep]
    return emb, y, w, cats, pairs


def _score(
    params: TasteModelParams,
    index: _Index,
    image_paths: Sequence[str],
    image_categories: Dict[str, List[str]],
) -> Dict[str, float]:
    found = [(p, idx) for p in image_paths if (idx := index.id_for(p)) is not None]
    if not found:
        return {}
    emb = index.vectors([idx for _, idx in found])
    cats = [image_categories.get(p, []) for p, _ in found]
    s = predict(params, features(emb, cats, params.categories))
    return {p: float(v) for (p, _), v in zip(found, s)}


def _train(task: Dict[str, Any], index: _Index, out_dir: Path) -> Dict[str, Any]:
    if not task.get("labels_path"):
        raise TasteWorkerError("train task has no labels_path")
    labels = json.loads(Path(task["labels_path"]).read_text())
    if labels.get("version") != LABELS_VERSION:
        raise TasteWorkerError(f"unsupported labels version {labels.get('version')}")
    emb, y, w, row_cats, pairs = _training_matrix(labels, index)
    n_pos = int((y > 0.5).sum())
    n_neg = int(y.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        raise TasteWorkerError(
            f"need both classes with vectors (positives={n_pos}, negatives={n_neg})"
        )
    categories = sorted({c for cs in row_cats for c in cs})
    X = features(emb, row_cats, categories)

    train_idx, hold_idx = split_holdout(len(y))
    wt, bt = fit(
        X[train_idx], y[train_idx], w[train_idx], train_pairs(pairs, train_idx)
    )
    holdout_params = TasteModelParams(wt, bt, categories, index.dim, index.model_key)
    holdout_auc = (
        auc(y[hold_idx], predict(holdout_params, X[hold_idx]))
        if hold_idx.size
        else None
    )

    w_all, b_all = fit(X, y, w, pairs)
    params = TasteModelParams(w_all, b_all, categories, index.dim, index.model_key)
    # Names sort chronologically; the runner prunes by name.
    params_path = out_dir / f"taste_{time.time_ns():020d}_{uuid.uuid4().hex[:8]}.npz"
    params.save(params_path)

    scores = _score(
        params, index, task.get("image_paths") or [], task.get("image_categories") or {}
    )
    return {
        "status": "ok",
        "params_path": str(params_path),
        "holdout_auc": holdout_auc,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "n_pairs": len(pairs),
        "dim": index.dim,
        "embedding_model": index.model_key,
        "categories": categories,
        "scores": scores,
    }


def _rescore(task: Dict[str, Any], index: _Index) -> Dict[str, Any]:
    if not task.get("model_path"):
        raise TasteWorkerError("score task has no model_path")
    params = TasteModelParams.load(Path(task["model_path"]))
    if params.embedding_model != index.model_key or params.dim != index.dim:
        raise TasteWorkerError(
            f"model is stale: trained on {params.embedding_model}/{params.dim}, "
            f"index is {index.model_key}/{index.dim}"
        )
    scores = _score(
        params, index, task.get("image_paths") or [], task.get("image_categories") or {}
    )
    return {"status": "ok", "scores": scores}


def run(task: Dict[str, Any]) -> Dict[str, Any]:
    out_dir = Path(task["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = task.get("mode")
    if mode not in ("train", "score"):
        raise TasteWorkerError(f"unknown mode {mode!r}")
    index = _Index(Path(task["index_dir"]))
    if mode == "train":
        return _train(task, index, out_dir)
    return _rescore(task, index)


def _write_result(out_dir: Path, result: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RESULT_NAME
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(result, f)
    os.replace(tmp, path)


def main(argv: List[str]) -> int:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    if len(argv) < 2:
        print(
            "Usage: python -m metascan.workers.taste_worker <task.json>",
            file=sys.stderr,
        )
        return 2
    task: Dict[str, Any] = {}
    try:
        task = json.loads(Path(argv[1]).read_text())
        t0 = time.perf_counter()
        result = run(task)
        logger.info(
            "taste worker %s done in %.2fs (%d scores)",
            task.get("mode"),
            time.perf_counter() - t0,
            len(result.get("scores") or {}),
        )
    except Exception as e:
        logger.exception("taste worker failed")
        out = task.get("out_dir")
        if out:
            _write_result(Path(out), {"status": "error", "error": str(e) or repr(e)})
        return 1
    _write_result(Path(task["out_dir"]), result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
