"""One-shot LoRA training worker for the 8B prompt writer (taste project 4).

Usage:
    python -m metascan.workers.writer_train_worker <task.json>

Fine-tunes the writer (Qwen3-VL-8B, HF-format base) with LoRA in bf16 --
stage 1 SFT on the SFT file, stage 2 DPO on the preference file -- then
measures holdout agreement (adapter vs base), saves the PEFT adapter and
converts it to GGUF with llama.cpp's ``convert_lora_to_gguf.py``.

This module is deliberately light: stdlib only. The heavy training stack
(torch / transformers / peft / trl / datasets) is imported lazily inside
``writer_train_backend`` and only for ``backend: "real"``, so the FastAPI
process may import the constants and ``validate_hyperparams`` from here.

Task (JSON)::

    {"backend": "real" | "fake",
     "base_dir": str,            # HF base weights dir (real; converter --base)
     "sft_path": str, "pref_path": str, "holdout_path": str,  # JSONL, schema
                                 # in metascan.core.writer_dataset
     "out_dir": str,             # progress/result/meta, peft/, adapter.gguf
     "checkpoint_dir": str,      # trainer checkpoints + state.json
     "control_dir": str,         # pause.signal / cancel.signal
     "resume": bool,             # continue from checkpoint_dir's state
     "hyperparams": {"rank", "lr", "epochs", ...optional, see
                     validate_hyperparams},
     "converter": {"python": str, "script": str},   # required for real
     "init_adapter_dir": str | null,  # optional: continue an existing
                                      # PEFT adapter instead of a fresh one
     "meta": {...},              # optional, merged into meta.json
                                 # (e.g. {"counts": DatasetCounts.to_dict()})
     "fake": {...}}              # fake backend knobs (writer_train_fake)

Outputs in ``out_dir``:

* ``progress.json`` (atomic, every ``progress_every`` steps and at every
  stage change / pause)::

    {"stage": "sft" | "dpo" | "eval" | "convert",
     "step": int, "total_steps": int,          # overall: SFT + DPO steps
     "stage_step": int, "stage_total": int,    # within the current stage
     "loss": float | null, "holdout_agreement": float | null,
     "base_agreement": float | null, "eta_s": float | null,
     "last_checkpoint_step": int | null,       # overall step
     "last_checkpoint_at": iso | null, "updated_at": iso}

* ``result.json``::

    {"status": "ok" | "paused" | "cancelled" | "error",
     "adapter_dir": str | null, "gguf_path": str | null,
     "meta_path": str | null, "holdout_agreement": float | null,
     "base_agreement": float | null, "error": str | null,
     "stage": str | null, "step": int | null,  # paused: where (stage-local)
     "resumed_from": {"stage", "step"} | null,
     "n_sft": int, "n_pref": int, "n_holdout": int}

* ``peft/`` (adapter_config.json + adapter weights), ``adapter.gguf``,
  ``meta.json`` (resolved hyperparams, method, counts, agreements, the
  task's ``meta`` merged in).

``checkpoint_dir/state.json``: ``{"version": 1, "stage": "sft" | "dpo" |
"eval" | "convert", "fingerprint": str, "sft_adapter_dir": str | null,
"totals": {"sft": int, "dpo": int}, "holdout_agreement", "base_agreement"}``
-- a resume after SFT finished goes straight to DPO; trainer checkpoints
live in ``checkpoint_dir/sft/checkpoint-<step>`` and ``.../dpo/...`` and the
latest one is resumed. A resume whose data or hyperparameters differ from
the fingerprint is refused. A fresh (``resume: false``) run wipes
``checkpoint_dir`` first; a successful run deletes it.

Control: the parent drops ``pause.signal`` / ``cancel.signal`` in
``control_dir``; they are checked between steps (and between stages).
Pause saves a checkpoint at that step boundary, consumes ``pause.signal``
and exits 3. Cancel deletes the checkpoints and partial outputs, consumes
``cancel.signal`` and exits 4. The parent should empty ``control_dir``
before spawning a run.

Exit codes: 0 ok, 1 error (result.json carries it when ``out_dir`` is
known), 2 usage, 3 paused, 4 cancelled.

Example text (prompts, briefs, targets) is NEVER logged: counts only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger("writer_train_worker")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_PAUSED = 3
EXIT_CANCELLED = 4

PROGRESS_NAME = "progress.json"
RESULT_NAME = "result.json"
META_NAME = "meta.json"
STATE_NAME = "state.json"
GGUF_NAME = "adapter.gguf"
PEFT_DIRNAME = "peft"
SFT_ADAPTER_DIRNAME = "sft_adapter"
PAUSE_SIGNAL = "pause.signal"
CANCEL_SIGNAL = "cancel.signal"
STATE_VERSION = 1

STAGES = ("sft", "dpo", "eval", "convert")
BACKENDS = ("real", "fake")

# Effort presets (global constraints): shown resolved in the UI.
EFFORT_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {"rank": 8, "lr": 1e-4, "epochs": 1},
    "standard": {"rank": 16, "lr": 1e-4, "epochs": 2},
    "thorough": {"rank": 32, "lr": 5e-5, "epochs": 3},
}
# Owner-facing clamps (Advanced overrides).
CLAMPS: Dict[str, Tuple[float, float]] = {
    "rank": (4, 64),
    "lr": (1e-6, 5e-4),
    "epochs": (1, 5),
}
MAX_SEQ_LEN = 1024
DPO_BETA = 0.1
CHECKPOINT_EVERY = 50
SEED = 0
METHOD = "lora-bf16-sft-dpo"
REAL_PROGRESS_EVERY = 2  # progress.json cadence in optimizer steps

# Internal knobs: (default, min, max, type). Not shown to the owner.
_KNOBS: Dict[str, Tuple[Any, float, float, type]] = {
    "lora_dropout": (0.05, 0.0, 0.5, float),
    "batch_size": (1, 1, 16, int),
    "grad_accum": (8, 1, 64, int),
    "max_seq_len": (MAX_SEQ_LEN, 64, MAX_SEQ_LEN, int),
    "beta": (DPO_BETA, 0.01, 1.0, float),
    "checkpoint_every": (CHECKPOINT_EVERY, 1, 100000, int),
    "warmup_ratio": (0.03, 0.0, 0.5, float),
    "seed": (SEED, 0, 2**31 - 1, int),
}

SFT_KEYS = ("system", "user", "chosen")
PREF_KEYS = ("system", "user", "chosen", "rejected")


class TrainWorkerError(Exception):
    """A failure the result.json reports verbatim (never example text)."""


class Paused(Exception):
    def __init__(self, stage: str, step: int) -> None:
        super().__init__(f"paused at {stage} step {step}")
        self.stage = stage
        self.step = step


class Cancelled(Exception):
    pass


# ---- hyperparameters ------------------------------------------------------


def _number(name: str, value: Any, kind: type) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainWorkerError(f"hyperparams.{name} must be a number")
    if kind is int and (not float(value).is_integer()):
        raise TrainWorkerError(f"hyperparams.{name} must be an integer")
    if not math.isfinite(float(value)):
        raise TrainWorkerError(f"hyperparams.{name} must be finite")
    return float(value)


def validate_hyperparams(raw: Any) -> Dict[str, Any]:
    """Validate and resolve: ``rank``/``lr``/``epochs`` are required and
    must sit inside ``CLAMPS``; every other knob gets its default. Out of
    range is an error here (the manager clamps before it gets this far)."""
    if not isinstance(raw, Mapping):
        raise TrainWorkerError("hyperparams must be an object")
    known = set(CLAMPS) | set(_KNOBS) | {"lora_alpha"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise TrainWorkerError(f"unknown hyperparams: {', '.join(unknown)}")
    out: Dict[str, Any] = {}
    for name, (lo, hi) in CLAMPS.items():
        if name not in raw:
            raise TrainWorkerError(f"hyperparams.{name} is required")
        kind = float if name == "lr" else int
        v = _number(name, raw[name], kind)
        if not lo <= v <= hi:
            raise TrainWorkerError(f"hyperparams.{name}={v:g} outside [{lo:g}, {hi:g}]")
        out[name] = int(v) if kind is int else v
    for name, (default, lo, hi, kind) in _KNOBS.items():
        v = _number(name, raw.get(name, default), kind)
        if not lo <= v <= hi:
            raise TrainWorkerError(f"hyperparams.{name}={v:g} outside [{lo:g}, {hi:g}]")
        out[name] = int(v) if kind is int else v
    alpha = raw.get("lora_alpha")
    if alpha is None:
        out["lora_alpha"] = 2 * out["rank"]
    else:
        a = _number("lora_alpha", alpha, int)
        if not 1 <= a <= 1024:
            raise TrainWorkerError("hyperparams.lora_alpha outside [1, 1024]")
        out["lora_alpha"] = int(a)
    return out


def stage_steps(
    n_examples: int, *, batch_size: int, grad_accum: int, epochs: int
) -> int:
    """Optimizer steps the HF Trainer takes: ceil(ceil(n/bs)/ga) * epochs."""
    if n_examples <= 0:
        return 0
    batches = math.ceil(n_examples / batch_size)
    return math.ceil(batches / grad_accum) * epochs


# ---- task / data ----------------------------------------------------------


@dataclass
class TrainTask:
    backend: str
    base_dir: str
    sft_path: Path
    pref_path: Path
    holdout_path: Path
    out_dir: Path
    checkpoint_dir: Path
    control_dir: Path
    resume: bool
    hyperparams: Dict[str, Any]
    converter: Optional[Dict[str, str]]
    init_adapter_dir: Optional[str]
    meta: Dict[str, Any]
    fake: Dict[str, Any]


def _str_field(task: Mapping[str, Any], key: str, required: bool = True) -> str:
    v = task.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not v:
        raise TrainWorkerError(f"task.{key} must be a non-empty string")
    return v


def parse_task(raw: Any) -> TrainTask:
    if not isinstance(raw, Mapping):
        raise TrainWorkerError("task must be a JSON object")
    backend = raw.get("backend")
    if backend not in BACKENDS:
        raise TrainWorkerError(f"task.backend must be one of {BACKENDS}")
    resume = raw.get("resume", False)
    if not isinstance(resume, bool):
        raise TrainWorkerError("task.resume must be a boolean")
    converter = raw.get("converter")
    if converter is not None:
        if not (
            isinstance(converter, Mapping)
            and all(
                isinstance(converter.get(k), str) and converter.get(k)
                for k in ("python", "script")
            )
        ):
            raise TrainWorkerError("task.converter must be {python, script}")
        converter = {"python": converter["python"], "script": converter["script"]}
    if backend == "real" and converter is None:
        raise TrainWorkerError("task.converter is required for the real backend")
    base_dir = _str_field(raw, "base_dir", required=(backend == "real"))
    init = raw.get("init_adapter_dir")
    if init is not None and (not isinstance(init, str) or not init):
        raise TrainWorkerError("task.init_adapter_dir must be a string or null")
    meta = raw.get("meta") or {}
    fake = raw.get("fake") or {}
    if not isinstance(meta, Mapping) or not isinstance(fake, Mapping):
        raise TrainWorkerError("task.meta / task.fake must be objects")
    return TrainTask(
        backend=str(backend),
        base_dir=base_dir,
        sft_path=Path(_str_field(raw, "sft_path")),
        pref_path=Path(_str_field(raw, "pref_path")),
        holdout_path=Path(_str_field(raw, "holdout_path")),
        out_dir=Path(_str_field(raw, "out_dir")),
        checkpoint_dir=Path(_str_field(raw, "checkpoint_dir")),
        control_dir=Path(_str_field(raw, "control_dir")),
        resume=resume,
        hyperparams=validate_hyperparams(raw.get("hyperparams")),
        converter=converter,
        init_adapter_dir=init,
        meta=dict(meta),
        fake=dict(fake),
    )


def read_jsonl(path: Path, keys: Sequence[str]) -> List[Dict[str, Any]]:
    """Every line must be an object with string ``keys`` (and, when
    present, a list ``categories``). Errors name the file and line, never
    its content."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise TrainWorkerError(f"cannot read {path.name}: {e.strerror or e}") from None
    rows: List[Dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise TrainWorkerError(f"{path.name} line {n}: not valid JSON") from None
        if not isinstance(row, dict):
            raise TrainWorkerError(f"{path.name} line {n}: not an object")
        bad = [k for k in keys if not isinstance(row.get(k), str)]
        if bad:
            raise TrainWorkerError(
                f"{path.name} line {n}: missing/invalid {', '.join(bad)}"
            )
        if not isinstance(row.get("categories", []), list):
            raise TrainWorkerError(f"{path.name} line {n}: categories must be a list")
        rows.append(row)
    return rows


@dataclass
class TrainData:
    sft: List[Dict[str, Any]]
    prefs: List[Dict[str, Any]]
    holdout: List[Dict[str, Any]]


def load_data(task: TrainTask) -> TrainData:
    data = TrainData(
        sft=read_jsonl(task.sft_path, SFT_KEYS),
        prefs=read_jsonl(task.pref_path, PREF_KEYS),
        holdout=read_jsonl(task.holdout_path, PREF_KEYS),
    )
    if not data.prefs:
        raise TrainWorkerError("no preference pairs to train on")
    return data


def fingerprint(task: TrainTask) -> str:
    """What a resume must match: data files, hyperparameters, base, start."""
    h = hashlib.sha256()
    h.update(
        json.dumps(
            {
                "backend": task.backend,
                "base_dir": task.base_dir,
                "init_adapter_dir": task.init_adapter_dir,
                "hyperparams": task.hyperparams,
            },
            sort_keys=True,
        ).encode()
    )
    for p in (task.sft_path, task.pref_path, task.holdout_path):
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


# ---- files ---------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def latest_checkpoint(stage_dir: Path) -> Optional[Path]:
    """``stage_dir/checkpoint-<N>`` with the largest N (the HF naming)."""
    best: Optional[Tuple[int, Path]] = None
    if not stage_dir.is_dir():
        return None
    for child in stage_dir.iterdir():
        name = child.name
        if child.is_dir() and name.startswith("checkpoint-") and name[11:].isdigit():
            n = int(name[11:])
            if best is None or n > best[0]:
                best = (n, child)
    return best[1] if best else None


def checkpoint_step(path: Optional[Path]) -> int:
    return int(path.name.split("-", 1)[1]) if path is not None else 0


class Control:
    def __init__(self, control_dir: Path) -> None:
        self.dir = control_dir

    def check(self) -> Optional[str]:
        """``"cancel"`` (wins), ``"pause"`` or None."""
        if (self.dir / CANCEL_SIGNAL).exists():
            return "cancel"
        if (self.dir / PAUSE_SIGNAL).exists():
            return "pause"
        return None

    def consume(self, name: str) -> None:
        try:
            (self.dir / name).unlink()
        except FileNotFoundError:
            pass


class RunState:
    def __init__(self, path: Path, data: Dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @property
    def stage(self) -> str:
        return str(self.data["stage"])

    def update(self, **fields: Any) -> None:
        self.data.update(fields)
        write_json_atomic(self.path, self.data)


# ---- progress / context ---------------------------------------------------


class StageContext:
    """What a backend sees: progress reporting, control checks and
    checkpoint bookkeeping. Steps passed in are stage-local."""

    def __init__(
        self,
        out_dir: Path,
        control: Control,
        totals: Dict[str, int],
        progress_every: int,
    ) -> None:
        self.out_dir = out_dir
        self.control = control
        self.totals = totals
        self.progress_every = max(1, progress_every)
        self.stage = "sft"
        self.stage_step = 0
        self.stage_total = 0
        self.loss: Optional[float] = None
        self.holdout_agreement: Optional[float] = None
        self.base_agreement: Optional[float] = None
        self.last_checkpoint_step: Optional[int] = None
        self.last_checkpoint_at: Optional[str] = None
        self._t0: Optional[Tuple[float, int]] = None
        self._writes = 0

    def _offset(self, stage: str) -> int:
        return self.totals.get("sft", 0) if stage != "sft" else 0

    def overall_total(self) -> int:
        return self.totals.get("sft", 0) + self.totals.get("dpo", 0)

    def overall_step(self) -> int:
        if self.stage in ("eval", "convert"):
            return self.overall_total()
        return min(self._offset(self.stage) + self.stage_step, self.overall_total())

    def set_total(self, stage: str, total: int) -> None:
        if stage in ("sft", "dpo") and total > 0:
            self.totals[stage] = int(total)
        if stage == self.stage:
            self.stage_total = int(total)

    def begin(self, stage: str, total: int, step: int = 0) -> None:
        self.stage = stage
        self.set_total(stage, total)
        self.stage_total = int(total)
        self.stage_step = step
        self.flush()

    def report(
        self, stage: str, step: int, loss: Optional[float] = None, force: bool = False
    ) -> None:
        self.stage = stage
        self.stage_step = int(step)
        if loss is not None:
            self.loss = float(loss)
        if force or self.stage_step % self.progress_every == 0:
            self.flush()

    def note_checkpoint(self, stage: str, step: int) -> None:
        self.last_checkpoint_step = self._offset(stage) + int(step)
        self.last_checkpoint_at = _now_iso()
        logger.info("writer train: checkpoint %s step %d", stage, step)

    def check(self) -> Optional[str]:
        return self.control.check()

    def _eta(self, step: int, total: int) -> Optional[float]:
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = (now, step)
            return None
        t0, s0 = self._t0
        if step <= s0 or self.stage not in ("sft", "dpo"):
            return None
        return round((now - t0) / (step - s0) * max(0, total - step), 1)

    def flush(self) -> None:
        total = self.overall_total()
        step = self.overall_step()
        write_json_atomic(
            self.out_dir / PROGRESS_NAME,
            {
                "stage": self.stage,
                "step": step,
                "total_steps": total,
                "stage_step": self.stage_step,
                "stage_total": self.stage_total,
                "loss": self.loss,
                "holdout_agreement": self.holdout_agreement,
                "base_agreement": self.base_agreement,
                "eta_s": self._eta(step, total),
                "last_checkpoint_step": self.last_checkpoint_step,
                "last_checkpoint_at": self.last_checkpoint_at,
                "updated_at": _now_iso(),
            },
        )
        self._writes += 1


class Backend(Protocol):
    def train_sft(
        self,
        ctx: StageContext,
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> None: ...

    def train_dpo(
        self,
        ctx: StageContext,
        examples: List[Dict[str, Any]],
        init_adapter: Optional[str],
        resume_from: Optional[Path],
        save_to: Path,
    ) -> None: ...

    def evaluate(
        self, ctx: StageContext, holdout: List[Dict[str, Any]], adapter_dir: Path
    ) -> Tuple[Optional[float], Optional[float]]: ...

    def convert(self, ctx: StageContext, peft_dir: Path, gguf_path: Path) -> None: ...


def make_backend(task: TrainTask) -> Backend:
    if task.backend == "fake":
        from metascan.workers.writer_train_fake import FakeBackend

        return FakeBackend(task)
    from metascan.workers.writer_train_backend import RealBackend

    return RealBackend(task)


# ---- converter ------------------------------------------------------------


def converter_command(
    converter: Mapping[str, str], base_dir: str, peft_dir: str, gguf_path: str
) -> List[str]:
    return [
        converter["python"],
        converter["script"],
        "--base",
        base_dir,
        "--outtype",
        "f16",
        "--outfile",
        gguf_path,
        peft_dir,
    ]


def run_converter(
    converter: Mapping[str, str],
    base_dir: str,
    peft_dir: str,
    gguf_path: str,
    timeout: float = 3600.0,
) -> None:
    cmd = converter_command(converter, base_dir, peft_dir, gguf_path)
    logger.info("writer train: converting adapter to GGUF")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TrainWorkerError(f"converter could not run: {e}") from None
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-5:])
        raise TrainWorkerError(f"converter failed (exit {proc.returncode}): {tail}")
    out = Path(gguf_path)
    if not out.is_file() or out.stat().st_size == 0:
        raise TrainWorkerError("converter produced no output file")


# ---- the run ---------------------------------------------------------------


def _blank_result() -> Dict[str, Any]:
    return {
        "status": "error",
        "adapter_dir": None,
        "gguf_path": None,
        "meta_path": None,
        "holdout_agreement": None,
        "base_agreement": None,
        "error": None,
        "stage": None,
        "step": None,
        "resumed_from": None,
        "n_sft": 0,
        "n_pref": 0,
        "n_holdout": 0,
    }


def _clean_cancelled(task: TrainTask) -> None:
    shutil.rmtree(task.checkpoint_dir, ignore_errors=True)
    shutil.rmtree(task.out_dir / PEFT_DIRNAME, ignore_errors=True)
    for name in (GGUF_NAME, META_NAME):
        try:
            (task.out_dir / name).unlink()
        except FileNotFoundError:
            pass


def _prepare_state(task: TrainTask, fp: str, totals: Dict[str, int]) -> RunState:
    path = task.checkpoint_dir / STATE_NAME
    if task.resume and path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            raise TrainWorkerError("cannot resume: state.json is corrupt") from None
        if data.get("version") != STATE_VERSION or data.get("stage") not in STAGES:
            raise TrainWorkerError("cannot resume: unknown state.json")
        if data.get("fingerprint") != fp:
            raise TrainWorkerError(
                "cannot resume: training data or hyperparameters changed since "
                "the checkpoint (start fresh instead)"
            )
        return RunState(path, data)
    if task.resume:
        logger.warning("writer train: resume requested but no state; starting fresh")
    shutil.rmtree(task.checkpoint_dir, ignore_errors=True)
    task.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(
        path,
        {
            "version": STATE_VERSION,
            "stage": "sft",
            "fingerprint": fp,
            "sft_adapter_dir": None,
            "totals": dict(totals),
            "holdout_agreement": None,
            "base_agreement": None,
        },
    )
    state.update()
    return state


def _initial_totals(task: TrainTask, data: TrainData) -> Dict[str, int]:
    hp = task.hyperparams
    kw = dict(
        batch_size=hp["batch_size"], grad_accum=hp["grad_accum"], epochs=hp["epochs"]
    )
    totals = {
        "sft": stage_steps(len(data.sft), **kw),
        "dpo": stage_steps(len(data.prefs), **kw),
    }
    if task.backend == "fake":
        for stage in ("sft", "dpo"):
            v = task.fake.get(f"{stage}_steps")
            if v is not None and (stage == "dpo" or data.sft):
                totals[stage] = int(v)
    if not data.sft:
        totals["sft"] = 0
    return totals


def _write_meta(task: TrainTask, data: TrainData, state: RunState) -> Path:
    meta: Dict[str, Any] = dict(task.meta)
    meta.update(
        {
            "method": METHOD,
            "hyperparams": task.hyperparams,
            "max_seq_len": task.hyperparams["max_seq_len"],
            "beta": task.hyperparams["beta"],
            "seed": task.hyperparams["seed"],
            "init_adapter_dir": task.init_adapter_dir,
            "n_sft": len(data.sft),
            "n_pref": len(data.prefs),
            "n_holdout": len(data.holdout),
            "steps": dict(state.data.get("totals") or {}),
            "holdout_agreement": state.data.get("holdout_agreement"),
            "base_agreement": state.data.get("base_agreement"),
            "finished_at": _now_iso(),
        }
    )
    path = task.out_dir / META_NAME
    write_json_atomic(path, meta)
    return path


def _stop_check(ctx: StageContext, stage: str) -> None:
    act = ctx.check()
    if act == "cancel":
        raise Cancelled()
    if act == "pause":
        raise Paused(stage, 0)


def execute(task: TrainTask) -> Tuple[int, Dict[str, Any]]:
    """Run (or resume) the task; returns ``(exit code, result)``."""
    result = _blank_result()
    control = Control(task.control_dir)
    task.out_dir.mkdir(parents=True, exist_ok=True)
    if control.check() == "cancel":
        _clean_cancelled(task)
        control.consume(CANCEL_SIGNAL)
        result["status"] = "cancelled"
        return EXIT_CANCELLED, result

    data = load_data(task)
    result.update(
        n_sft=len(data.sft), n_pref=len(data.prefs), n_holdout=len(data.holdout)
    )
    logger.info(
        "writer train: %s backend, %d SFT, %d preference, %d holdout example(s)",
        task.backend,
        len(data.sft),
        len(data.prefs),
        len(data.holdout),
    )
    totals = _initial_totals(task, data)
    state = _prepare_state(task, fingerprint(task), totals)
    totals = dict(state.data.get("totals") or totals)
    progress_every = (
        int(task.fake.get("progress_every", REAL_PROGRESS_EVERY))
        if task.backend == "fake"
        else REAL_PROGRESS_EVERY
    )
    ctx = StageContext(task.out_dir, control, totals, progress_every)
    ctx.holdout_agreement = state.data.get("holdout_agreement")
    ctx.base_agreement = state.data.get("base_agreement")

    if task.resume:
        if state.stage in ("sft", "dpo"):
            ck = latest_checkpoint(task.checkpoint_dir / state.stage)
            if ck is not None:
                result["resumed_from"] = {
                    "stage": state.stage,
                    "step": checkpoint_step(ck),
                }
                ctx.last_checkpoint_step = ctx._offset(state.stage) + checkpoint_step(
                    ck
                )
        logger.info("writer train: resuming at stage %s", state.stage)

    backend = make_backend(task)
    peft_dir = task.out_dir / PEFT_DIRNAME
    gguf = task.out_dir / GGUF_NAME
    try:
        if state.stage == "sft":
            sft_adapter: Optional[str] = None
            if data.sft:
                ck = latest_checkpoint(task.checkpoint_dir / "sft")
                ctx.begin("sft", totals["sft"], checkpoint_step(ck))
                save_to = task.checkpoint_dir / SFT_ADAPTER_DIRNAME
                backend.train_sft(ctx, data.sft, task.init_adapter_dir, ck, save_to)
                sft_adapter = str(save_to)
                logger.info("writer train: SFT done")
            state.update(stage="dpo", sft_adapter_dir=sft_adapter, totals=ctx.totals)
        if state.stage == "dpo":
            _stop_check(ctx, "dpo")
            ck = latest_checkpoint(task.checkpoint_dir / "dpo")
            ctx.begin("dpo", ctx.totals["dpo"], checkpoint_step(ck))
            init = state.data.get("sft_adapter_dir") or task.init_adapter_dir
            backend.train_dpo(ctx, data.prefs, init, ck, peft_dir)
            logger.info("writer train: DPO done")
            state.update(stage="eval", totals=ctx.totals)
        if state.stage == "eval":
            _stop_check(ctx, "eval")
            if not peft_dir.is_dir():
                raise TrainWorkerError(
                    "cannot evaluate: the trained adapter is missing"
                )
            ctx.begin("eval", len(data.holdout))
            h, b = backend.evaluate(ctx, data.holdout, peft_dir)
            ctx.holdout_agreement, ctx.base_agreement = h, b
            logger.info(
                "writer train: holdout agreement %s (base %s) over %d pair(s)",
                "n/a" if h is None else f"{h:.3f}",
                "n/a" if b is None else f"{b:.3f}",
                len(data.holdout),
            )
            state.update(stage="convert", holdout_agreement=h, base_agreement=b)
        _stop_check(ctx, "convert")
        ctx.begin("convert", 1)
        backend.convert(ctx, peft_dir, gguf)
        ctx.report("convert", 1, force=True)
    except Paused as p:
        ctx.flush()
        control.consume(PAUSE_SIGNAL)
        logger.info("writer train: paused at %s step %d", p.stage, p.step)
        result.update(status="paused", stage=p.stage, step=p.step)
        return EXIT_PAUSED, result
    except Cancelled:
        _clean_cancelled(task)
        control.consume(CANCEL_SIGNAL)
        logger.info("writer train: cancelled")
        result.update(status="cancelled", stage=ctx.stage)
        return EXIT_CANCELLED, result

    meta_path = _write_meta(task, data, state)
    shutil.rmtree(task.checkpoint_dir, ignore_errors=True)
    result.update(
        status="ok",
        adapter_dir=str(peft_dir),
        gguf_path=str(gguf),
        meta_path=str(meta_path),
        holdout_agreement=ctx.holdout_agreement,
        base_agreement=ctx.base_agreement,
    )
    return EXIT_OK, result


def main(argv: List[str]) -> int:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
    )
    if len(argv) < 2:
        print(
            "Usage: python -m metascan.workers.writer_train_worker <task.json>",
            file=sys.stderr,
        )
        return EXIT_USAGE
    raw: Any = None
    try:
        raw = json.loads(Path(argv[1]).read_text())
        task = parse_task(raw)
        code, result = execute(task)
    except Exception as e:
        if isinstance(e, TrainWorkerError):
            logger.error("writer train failed: %s", e)
        else:
            logger.exception("writer train failed")
        out = raw.get("out_dir") if isinstance(raw, dict) else None
        if isinstance(out, str) and out:
            res = _blank_result()
            res["error"] = str(e) or repr(e)
            try:
                write_json_atomic(Path(out) / RESULT_NAME, res)
            except OSError:
                logger.exception("cannot write result.json")
        return EXIT_ERROR
    write_json_atomic(task.out_dir / RESULT_NAME, result)
    logger.info("writer train: %s", result["status"])
    return code


if __name__ == "__main__":
    # Re-enter through the package module so the backends (which import
    # ``metascan.workers.writer_train_worker``) share these exception
    # classes with the code that catches them.
    from metascan.workers.writer_train_worker import main as _main

    sys.exit(_main(sys.argv))
