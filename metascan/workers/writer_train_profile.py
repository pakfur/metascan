"""Per-attempt training profile for the writer LoRA worker (stdlib only).

Every worker invocation -- a fresh start or a resume, one "attempt" --
writes ``<out_dir>/profile.json`` atomically (tmp + ``os.replace``): at the
start, at every stage end, every ``write_every_s`` while a stage runs and
at exit (completed, paused, cancelled, failed or oom). It is the evidence
for tuning the owner's runs and for supporting smaller cards later.

NUMBERS ONLY: no prompt, brief, example or completion text ever goes in
(counts, token lengths, timings, memory, versions and hyperparameters).

Schema 1 (``PROFILE_SCHEMA``)::

    {"schema": 1,
     "attempt": {"run_id", "attempt_id", "started_at", "ended_at", "wall_s",
                 "outcome",
                 "exit_code", "resumed", "resumed_from_stage",
                 "resumed_from_step"},
     "environment": {"os", "os_release", "machine", "python", "gpu_name",
                     "total_vram_gib", "nominal_vram_gb",
                     "compute_capability", "cuda_runtime", "driver_version",
                     "libraries": {name: version | null}},
     "config": {"backend", "method", "preset", "rank", "alpha", "dropout",
                "lr", "dpo_lr", "epochs", "batch_size", "grad_accum",
                "beta", "max_seq_len", "dtype", "gradient_checkpointing",
                "attn_implementation", "lora_target_regex", "vram_cap_gb",
                "vram_cap_applied", "vram_cap_fraction"},
     "dataset": {"counts": {"sft", "pref_train", "holdout"},
                 "dropped_too_long": {"sft", "dpo", "holdout"},
                 "token_lengths": {"sft" | "dpo": {"n", "min", "p50", "p90",
                                                   "p95", "p99", "max"}}},
     "stages": [{"name": "load" | "sft" | "dpo" | "eval" | "convert",
                 "completed", "wall_s", "start_step", "end_step",
                 "steps_done", "samples_per_s", "tokens_per_s",
                 "peak_allocated_gib", "peak_reserved_gib",
                 + load: "allocated_after_load_gib",
                 + sft/dpo: "loss_first", "loss_last",
                 + eval: "holdout_agreement", "base_agreement"}],
     "timeline": {stage: [{"step", "t_s", "loss", "alloc_gib",
                           "reserved_gib"}]},     # <= TIMELINE_MAX each
     "oom": null | {"stage", "step", "batch_tokens", "peak_allocated_gib",
                    "peak_reserved_gib", "reserved_gib", "allocated_gib",
                    "total_vram_gib", "vram_cap_gb"}}

Memory comes from a :class:`MemoryProbe` the backend installs (torch's
CUDA allocator counters in the real backend, deterministic numbers in the
fake); without one every memory field is null. ``samples_per_s`` is
optimizer steps x batch x grad_accum per second (eval: pairs per second)
and ``tokens_per_s`` that times the mean trained length -- an estimate.
"""

from __future__ import annotations

import logging
import math
import os
import platform
import time
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence

logger = logging.getLogger("writer_train_worker")

PROFILE_NAME = "profile.json"
PROFILE_SCHEMA = 1
TIMELINE_MAX = 200
WRITE_EVERY_S = 30.0
OUTCOMES = ("completed", "paused", "cancelled", "failed", "oom")

# VRAM cap rehearsal (``models.writer_train_vram_cap_gb`` / the window's
# Advanced "Simulate VRAM cap"): in the units torch reports (GiB), the
# number printed on the card. Applied only below the card's real total.
VRAM_CAP_MIN_GB = 8.0
VRAM_CAP_MAX_GB = 1024.0
VRAM_CAP_CHOICES = (16, 20, 24, 28)

LIBRARIES = (
    "torch",
    "transformers",
    "peft",
    "trl",
    "accelerate",
    "datasets",
    "bitsandbytes",
)
# Marketing sizes a card's GiB total rounds up to (31.84 -> 32).
_NOMINAL_GB = (
    4,
    6,
    8,
    10,
    11,
    12,
    16,
    20,
    24,
    32,
    40,
    48,
    64,
    80,
    94,
    96,
    128,
    141,
    180,
    192,
)
GIB = float(1 << 30)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json_atomic(path: Path, data: Any) -> None:
    import json

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


# ---- pure helpers -------------------------------------------------------------


def gib(n_bytes: Optional[float]) -> Optional[float]:
    if n_bytes is None:
        return None
    return round(float(n_bytes) / GIB, 2)


def token_stats(lengths: Iterable[int]) -> Dict[str, Optional[int]]:
    """``{n, min, p50, p90, p95, p99, max}`` (nearest-rank, integers)."""
    vals = sorted(int(v) for v in lengths)
    n = len(vals)
    out: Dict[str, Optional[int]] = {"n": n}
    if not n:
        out.update(dict.fromkeys(("min", "p50", "p90", "p95", "p99", "max")))
        return out
    out["min"] = vals[0]
    for p in (50, 90, 95, 99):
        out[f"p{p}"] = vals[max(0, math.ceil(p / 100 * n) - 1)]
    out["max"] = vals[-1]
    return out


def nominal_vram_gb(total_gib: Optional[float]) -> Optional[int]:
    if total_gib is None or total_gib <= 0:
        return None
    for size in _NOMINAL_GB:
        if size >= total_gib:
            return size
    return int(round(total_gib))


def vram_cap_fraction(
    cap_gb: Optional[float], total_gib: Optional[float]
) -> Optional[float]:
    """``cap / total`` when a cap is set and below the card; else None (the
    cap is recorded but not applied)."""
    if cap_gb is None or not total_gib or total_gib <= 0:
        return None
    if cap_gb >= total_gib:
        return None
    return float(cap_gb) / float(total_gib)


def library_versions(names: Sequence[str] = LIBRARIES) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except Exception:
            out[name] = None
    return out


def base_environment() -> Dict[str, Any]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "gpu_name": None,
        "total_vram_gib": None,
        "nominal_vram_gb": None,
        "compute_capability": None,
        "cuda_runtime": None,
        "driver_version": None,
        "libraries": library_versions(),
    }


OOM_CHAIN_DEPTH = 5  # how many __cause__ / __context__ links are followed


def _is_oom_one(exc: BaseException) -> bool:
    if any(c.__name__ == "OutOfMemoryError" for c in type(exc).__mro__):
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def is_oom_error(exc: BaseException) -> bool:
    """torch's ``OutOfMemoryError`` (by name: this module never imports
    torch) or the older ``RuntimeError("CUDA out of memory ...")`` form --
    the exception itself or one up to ``OOM_CHAIN_DEPTH`` links down its
    ``__cause__`` / ``__context__`` chain (a library that re-raises an OOM
    wrapped). Messages are only inspected, never stored."""
    cur: Optional[BaseException] = exc
    seen = set()
    for _ in range(OOM_CHAIN_DEPTH + 1):
        if cur is None or id(cur) in seen:
            return False
        seen.add(id(cur))
        if _is_oom_one(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def oom_message(
    stage: str, step: int, peak_gib: Optional[float], of_gib: Optional[float]
) -> str:
    if peak_gib is None:
        mem = "peak unknown"
    elif of_gib is None:
        mem = f"peak {peak_gib:.2f} GiB"
    else:
        mem = f"peak {peak_gib:.2f} GiB of {of_gib:.2f}"
    return f"Ran out of GPU memory during {stage} at step {step} ({mem})."


# ---- memory probes --------------------------------------------------------------


class MemoryProbe(Protocol):
    """Allocator counters in bytes (None when unknown). ``reset_peak``
    starts a new peak window (each stage)."""

    def reset_peak(self) -> None: ...

    def allocated(self) -> Optional[float]: ...

    def reserved(self) -> Optional[float]: ...

    def peak_allocated(self) -> Optional[float]: ...

    def peak_reserved(self) -> Optional[float]: ...


class NullMemoryProbe:
    def reset_peak(self) -> None:
        return None

    def allocated(self) -> Optional[float]:
        return None

    def reserved(self) -> Optional[float]:
        return None

    def peak_allocated(self) -> Optional[float]:
        return None

    def peak_reserved(self) -> Optional[float]:
        return None


# ---- the bounded timeline ----------------------------------------------------------


class Timeline:
    """At most ``cap`` samples: when full, every other sample is dropped
    and the sampling interval doubles (the whole run's shape at a fixed
    size). :meth:`close` keeps the stage's last step."""

    def __init__(self, cap: int = TIMELINE_MAX) -> None:
        self.cap = max(2, int(cap))
        self.interval = 1
        self.samples: List[Dict[str, Any]] = []
        self._seen = 0
        self._last: Optional[Dict[str, Any]] = None

    def add(self, sample: Dict[str, Any]) -> None:
        self._last = sample
        if self._seen % self.interval == 0:
            self.samples.append(sample)
            self._shrink()
        self._seen += 1

    def _shrink(self) -> None:
        if len(self.samples) > self.cap:
            self.samples = self.samples[::2]
            self.interval *= 2

    def close(self) -> None:
        last = self._last
        if last is not None and (not self.samples or self.samples[-1] is not last):
            self.samples.append(last)
            if len(self.samples) > self.cap:
                # Keep the first and the last; thin the middle.
                self.samples = self.samples[:-1][::2] + [last]
                self.interval *= 2

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self.samples)


# ---- the recorder ------------------------------------------------------------------


class _OpenStage:
    def __init__(self, name: str, t0: float, start_step: int) -> None:
        self.name = name
        self.t0 = t0
        self.start_step = start_step
        self.last_step = start_step
        self.loss_first: Optional[float] = None
        self.loss_last: Optional[float] = None


class ProfileRecorder:
    """Collects one attempt's profile. ``path`` None keeps it in memory
    (tests constructing a bare ``StageContext``). A failing write is
    logged and ignored: a profile must never fail a training run."""

    def __init__(
        self,
        path: Optional[Path],
        *,
        run_id: Any = None,
        resumed: bool = False,
        config: Optional[Dict[str, Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        write_every_s: float = WRITE_EVERY_S,
    ) -> None:
        self.path = path
        self.clock = clock
        self.write_every_s = float(write_every_s)
        self.probe: MemoryProbe = NullMemoryProbe()
        self._t_start = clock()
        self._last_write = self._t_start
        self._open: Optional[_OpenStage] = None
        self._load: Optional[_OpenStage] = None
        self._timelines: Dict[str, Timeline] = {}
        self._token_mean: Dict[str, float] = {}
        self.batch_tokens: Optional[int] = None
        self.data: Dict[str, Any] = {
            "schema": PROFILE_SCHEMA,
            "attempt": {
                "run_id": run_id if isinstance(run_id, int) else None,
                # Unique per worker invocation: the history dedupes on it.
                "attempt_id": uuid.uuid4().hex,
                "started_at": _now_iso(),
                "ended_at": None,
                "wall_s": None,
                "outcome": None,
                "exit_code": None,
                "resumed": bool(resumed),
                "resumed_from_stage": None,
                "resumed_from_step": None,
            },
            "environment": base_environment(),
            "config": dict(config or {}),
            "dataset": {
                "counts": {"sft": 0, "pref_train": 0, "holdout": 0},
                "dropped_too_long": {"sft": 0, "dpo": 0, "holdout": 0},
                "token_lengths": {},
            },
            "stages": [],
            "timeline": {},
            "oom": None,
        }

    # -- inputs --------------------------------------------------------------

    def set_probe(self, probe: Optional[MemoryProbe]) -> None:
        self.probe = probe if probe is not None else NullMemoryProbe()

    def update_environment(self, **fields: Any) -> None:
        self.data["environment"].update(fields)

    def update_config(self, **fields: Any) -> None:
        self.data["config"].update(fields)

    def set_counts(self, sft: int, pref_train: int, holdout: int) -> None:
        self.data["dataset"]["counts"] = {
            "sft": int(sft),
            "pref_train": int(pref_train),
            "holdout": int(holdout),
        }

    def set_dropped(self, dropped: Dict[str, int]) -> None:
        self.data["dataset"]["dropped_too_long"] = {
            k: int(v) for k, v in dropped.items()
        }

    def set_token_lengths(self, stage: str, lengths: Sequence[int]) -> None:
        self.data["dataset"]["token_lengths"][stage] = token_stats(lengths)
        if lengths:
            self._token_mean[stage] = sum(lengths) / len(lengths)

    def set_resumed_from(self, stage: str, step: int) -> None:
        self.data["attempt"]["resumed_from_stage"] = str(stage)
        self.data["attempt"]["resumed_from_step"] = int(step)

    def note_batch_tokens(self, n: Optional[int]) -> None:
        self.batch_tokens = int(n) if isinstance(n, int) else None

    # -- memory ----------------------------------------------------------------

    def _read(self, fn: Callable[[], Optional[float]]) -> Optional[float]:
        try:
            return fn()
        except Exception:
            return None

    def memory_now(self) -> Dict[str, Optional[float]]:
        """``{alloc_gib, peak_gib}`` for progress.json (peak: this stage)."""
        return {
            "alloc_gib": gib(self._read(self.probe.allocated)),
            "peak_gib": gib(self._read(self.probe.peak_allocated)),
        }

    def _reset_peak(self) -> None:
        try:
            self.probe.reset_peak()
        except Exception:
            pass

    # -- stages ------------------------------------------------------------------

    def stage_begin(self, stage: str, step: int = 0) -> None:
        if self._open is not None:
            self.stage_end(completed=False)
        self._reset_peak()
        self._open = _OpenStage(stage, self.clock(), int(step))
        self._timelines[stage] = Timeline()

    def load_begin(self) -> None:
        self._reset_peak()
        self._load = _OpenStage("load", self.clock(), 0)

    @property
    def loading(self) -> bool:
        """A model load is in progress (an OOM there is the load's)."""
        return self._load is not None

    def load_end(self) -> None:
        load = self._load
        if load is None:
            return
        self._load = None
        entry = self._entry(load, completed=True)
        entry["allocated_after_load_gib"] = gib(self._read(self.probe.allocated))
        self.data["stages"].append(entry)
        # The stage that triggered the load measures training only.
        self._reset_peak()
        if self._open is not None:
            self._open.t0 = self.clock()
        self.write()

    def sample(self, stage: str, step: int, loss: Optional[float]) -> None:
        op = self._open
        if op is None or op.name != stage:
            return
        op.last_step = int(step)
        if loss is not None:
            if op.loss_first is None:
                op.loss_first = float(loss)
            op.loss_last = float(loss)
        self._timelines[stage].add(
            {
                "step": int(step),
                "t_s": round(self.clock() - op.t0, 2),
                "loss": None if loss is None else round(float(loss), 5),
                "alloc_gib": gib(self._read(self.probe.allocated)),
                "reserved_gib": gib(self._read(self.probe.reserved)),
            }
        )
        if self.clock() - self._last_write >= self.write_every_s:
            self.write()

    def _per_step_samples(self, stage: str) -> Optional[int]:
        cfg = self.data["config"]
        if stage in ("sft", "dpo"):
            bs, ga = cfg.get("batch_size"), cfg.get("grad_accum")
            if isinstance(bs, int) and isinstance(ga, int):
                return bs * ga
            return None
        if stage == "eval":
            return 1
        return None

    def _entry(self, op: _OpenStage, completed: bool) -> Dict[str, Any]:
        wall = max(0.0, self.clock() - op.t0)
        steps = max(0, op.last_step - op.start_step)
        per = self._per_step_samples(op.name)
        sps = round(steps * per / wall, 3) if per and steps and wall > 0 else None
        mean = self._token_mean.get(op.name)
        entry: Dict[str, Any] = {
            "name": op.name,
            "completed": bool(completed),
            "wall_s": round(wall, 2),
            "start_step": op.start_step,
            "end_step": op.last_step,
            "steps_done": steps,
            "samples_per_s": sps,
            "tokens_per_s": round(sps * mean, 1) if sps and mean else None,
            "peak_allocated_gib": gib(self._read(self.probe.peak_allocated)),
            "peak_reserved_gib": gib(self._read(self.probe.peak_reserved)),
        }
        if op.name in ("sft", "dpo"):
            entry["loss_first"] = op.loss_first
            entry["loss_last"] = op.loss_last
        return entry

    def stage_end(self, completed: bool = True, **extra: Any) -> None:
        if self._load is not None:  # a load that never finished
            load, self._load = self._load, None
            self.data["stages"].append(self._entry(load, completed=False))
        op = self._open
        if op is None:
            return
        self._open = None
        entry = self._entry(op, completed)
        entry.update(extra)
        self.data["stages"].append(entry)
        tl = self._timelines.get(op.name)
        if tl is not None:
            tl.close()
        self.write()

    # -- the end -------------------------------------------------------------------

    def record_oom(self, stage: str, step: int) -> str:
        """Fill ``oom`` and return the owner-facing message."""
        env, cfg = self.data["environment"], self.data["config"]
        peak = gib(self._read(self.probe.peak_allocated))
        cap = cfg.get("vram_cap_gb") if cfg.get("vram_cap_applied") else None
        total = env.get("total_vram_gib")
        self.data["oom"] = {
            "stage": stage,
            "step": int(step),
            "batch_tokens": self.batch_tokens,
            "peak_allocated_gib": peak,
            "peak_reserved_gib": gib(self._read(self.probe.peak_reserved)),
            "reserved_gib": gib(self._read(self.probe.reserved)),
            "allocated_gib": gib(self._read(self.probe.allocated)),
            "total_vram_gib": total,
            "vram_cap_gb": cfg.get("vram_cap_gb"),
        }
        return oom_message(stage, step, peak, cap if cap is not None else total)

    def finish(self, outcome: str, exit_code: int) -> None:
        if self._open is not None or self._load is not None:
            self.stage_end(completed=False)
        a = self.data["attempt"]
        a["outcome"] = outcome
        a["exit_code"] = int(exit_code)
        a["ended_at"] = _now_iso()
        a["wall_s"] = round(max(0.0, self.clock() - self._t_start), 2)
        self.write()

    def write(self) -> None:
        self._last_write = self.clock()
        self.data["timeline"] = {k: t.to_list() for k, t in self._timelines.items()}
        if self.path is None:
            return
        try:
            _write_json_atomic(self.path, self.data)
        except (OSError, TypeError, ValueError) as e:
            logger.warning(
                "writer train: could not write the profile (%s)", type(e).__name__
            )
