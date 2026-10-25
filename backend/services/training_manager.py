"""Writer LoRA training manager (taste learning project 4).

Owns the single training run of the prompt writer:

* builds the dataset (``metascan.core.writer_dataset``) for a scope, checks
  readiness and writes the run's JSONL files;
* holds the **GPU lock** for the whole run: training starts only when no
  ComfyUI job is pending/running and no T2I / storyboard batch or embedding
  build is active; it shuts down every VLM client and the CLIP inference
  worker first; while it holds the lock every GPU consumer answers 409
  ``gpu_busy_training`` (``backend.services.gpu_lock`` /
  ``metascan.core.gpu_guard``). The lock is released on completion,
  failure, pause or cancel;
* spawns ``python -m metascan.workers.writer_train_worker <task.json>``
  (the training interpreter: ``models.writer_train_python`` or this
  Python), drains its stdout AND stderr line by line, polls
  ``progress.json`` and broadcasts on WS ``training``;
* on completion copies ``adapter.gguf`` / ``adapter_config.json`` /
  ``meta.json`` (and the PEFT dir, for "Continue <adapter>") into
  ``data/models/vlm/adapters/<id>/``, marks the adapter ``untested`` --
  exactly once per run (a compare-and-set on the run row) -- refreshes the
  writer server's adapter list and broadcasts ``adapters_changed``.

Each run works in its own directory ``data/training/runs/<run_id>/``
(``task.json``, ``data/*.jsonl``, ``out/``, ``checkpoints/``, ``control/``)
so a resume reuses the same hyperparameters and JSONL files (the worker
refuses anything else) and a later fresh run never sees another run's
checkpoints. A server stop pauses the worker (``pause.signal``, then
terminate after a grace period); a server that died leaves rows
``running`` and :meth:`TrainingManager.recover` marks them ``paused``.

WS ``training`` events: ``run_started {run_id, adapter_id, name, scope,
resumed}``, ``run_progress {run_id, adapter_id, stage, step, total_steps,
...}``, ``run_paused {run_id, adapter_id}``, ``run_completed {run_id,
adapter_id}``, ``run_failed {run_id, adapter_id, error}``, ``run_cancelled
{run_id, adapter_id}``, ``adapters_changed``. Example text is never logged.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

from metascan.core import writer_dataset as wd
from metascan.core.hardware import (
    DEPS_CHECK,
    TRAINING_DEPS_UNCHECKED,
    Gate,
    WriterTrainingPaths,
    cached_training_probe,
    clear_writer_training_probe_cache,
    detect_hardware,
    parse_deps_probe,
    store_training_probe,
    writer_training_gate,
)
from metascan.core.writer_adapters import (
    ADAPTER_BASE_MODEL,
    ADAPTER_CONFIG,
    ADAPTER_GGUF,
    ADAPTER_META,
    ADAPTER_PEFT_DIRNAME,
    SERVABLE_STATUSES,
    STATUS_DELETED,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_TRAINING,
    adapter_dir,
)
from backend.services import training_profiles
from metascan.workers import writer_train_profile as wprof
from metascan.workers import writer_train_worker as ww
from metascan.workers.writer_train_probe import TRL_CHECK, parse_trl_probe

logger = logging.getLogger(__name__)
worker_logger = logging.getLogger(__name__ + ".worker")

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE = "metascan.workers.writer_train_worker"
# The run's private copy of the "Continue from" adapter's PEFT dir (so a
# resume never depends on the source adapter still existing).
INIT_ADAPTER_DIRNAME = "init_adapter"

RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
RESUMABLE_RUN_STATUSES = (RUN_PAUSED, RUN_FAILED)

EFFORTS = tuple(ww.EFFORT_PRESETS)
DEFAULT_EFFORT = "standard"
OVERRIDE_KEYS = ("rank", "lr", "epochs", "dpo_lr")
OVERRIDE_CLAMPS: Dict[str, Tuple[float, float]] = {
    **ww.CLAMPS,
    "dpo_lr": ww.DPO_LR_CLAMP,
}

_ERROR_LINE = re.compile(r"\b(error|exception|traceback|failed|fatal)\b", re.I)
_TAIL_LINES = 40


class TrainingError(Exception):
    """A refused training operation: the routes answer ``status`` with
    ``{"code": code, **extra}``."""

    def __init__(self, status: int, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = extra

    def detail(self) -> Dict[str, Any]:
        return {"code": self.code, **self.extra}


# ---- hyperparameters --------------------------------------------------------


def resolved_presets() -> Dict[str, Dict[str, Any]]:
    """Every effort preset as the worker resolves it (shown read-only)."""
    return {
        name: ww.validate_hyperparams(dict(preset))
        for name, preset in ww.EFFORT_PRESETS.items()
    }


def resolve_hyperparams(
    effort: str, overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """The preset with Advanced ``overrides`` clamped into range (rank and
    epochs rounded to integers). Unknown keys / non-numbers are refused."""
    if effort not in ww.EFFORT_PRESETS:
        raise TrainingError(422, "invalid_effort", efforts=list(EFFORTS))
    hp: Dict[str, Any] = dict(ww.EFFORT_PRESETS[effort])
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key not in OVERRIDE_KEYS:
            raise TrainingError(422, "invalid_hyperparams", message=f"unknown {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrainingError(
                422, "invalid_hyperparams", message=f"{key} must be a number"
            )
        lo, hi = OVERRIDE_CLAMPS[key]
        v = min(max(float(value), lo), hi)
        hp[key] = int(round(v)) if key in ("rank", "epochs") else v
    try:
        ww.validate_hyperparams(hp)
    except ww.TrainWorkerError as exc:
        raise TrainingError(422, "invalid_hyperparams", message=str(exc)) from exc
    return hp


def _valid_cap(value: Any) -> Optional[float]:
    """A cap in GiB inside the worker's range, 0 / None = off; else raises
    ``ValueError``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("not finite")
    if v == 0:
        return None
    if not wprof.VRAM_CAP_MIN_GB <= v <= wprof.VRAM_CAP_MAX_GB:
        raise ValueError("out of range")
    return v


_WARNED_CAPS: Set[str] = set()  # bad config values already logged (once each)


def config_vram_cap(value: Any) -> Optional[float]:
    """``models.writer_train_vram_cap_gb`` validated; a bad value is
    ignored (logged), never fatal."""
    try:
        return _valid_cap(value)
    except ValueError:
        key = repr(value)
        if key in _WARNED_CAPS:
            return None
        _WARNED_CAPS.add(key)
        logger.warning(
            "models.writer_train_vram_cap_gb=%r ignored (needs %g..%g GB, or 0/null)",
            value,
            wprof.VRAM_CAP_MIN_GB,
            wprof.VRAM_CAP_MAX_GB,
        )
        return None


def resolve_vram_cap(requested: Any, config_value: Any) -> Optional[float]:
    """The VRAM-cap rehearsal of one run: ``requested`` None -> the config
    default; 0 -> off (beats the config); else ``VRAM_CAP_MIN_GB`` ..
    ``VRAM_CAP_MAX_GB`` GiB (422 ``invalid_vram_cap`` otherwise). The
    worker applies it only below the card's real total; the gate still
    checks the real card."""
    if requested is None:
        return config_vram_cap(config_value)
    try:
        return _valid_cap(requested)
    except ValueError:
        raise TrainingError(
            422,
            "invalid_vram_cap",
            min=wprof.VRAM_CAP_MIN_GB,
            max=wprof.VRAM_CAP_MAX_GB,
        ) from None


def scope_slug(scope: str, categories: Dict[int, str]) -> str:
    """``all`` or the category's name slugified (``cat<id>`` if empty)."""
    if scope == wd.SCOPE_ALL:
        return wd.SCOPE_ALL
    name = categories.get(int(scope), "")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or f"cat{scope}"


def training_paths_from_config(
    config: Dict[str, Any], data_dir: Path
) -> WriterTrainingPaths:
    """The training interpreter (``models.writer_train_python``, default
    this Python), the HF base weights dir (``models.writer_train_repo``,
    default the 8B writer's source repo) and llama.cpp's LoRA converter
    (``models.llama_cpp_dir``, default ``data/llama.cpp``). One resolver
    with the Training tab's asset rows: ``writer_training_assets.asset_paths``."""
    from backend.services.writer_training_assets import asset_paths

    return asset_paths(config, data_dir).gate_paths()


# ---- the worker's PID record (orphans after a hard kill) ---------------------

WORKER_PID_NAME = "worker.pid"
_CREATE_TIME_TOLERANCE_S = 1.0


def write_worker_pid(run_dir: Path, pid: int) -> None:
    """Record ``{pid, create_time, module}`` at spawn so a later server can
    recognise (and stop) a worker it no longer supervises."""
    import psutil

    try:
        created = psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        created = None
    ww.write_json_atomic(
        run_dir / WORKER_PID_NAME,
        {"pid": pid, "create_time": created, "module": WORKER_MODULE},
    )


def clear_worker_pid(run_dir: Path) -> None:
    try:
        (run_dir / WORKER_PID_NAME).unlink(missing_ok=True)
    except OSError:
        logger.debug("could not remove %s", run_dir / WORKER_PID_NAME)


WORKER_NONE = "none"
WORKER_OURS = "ours"
WORKER_UNVERIFIABLE = "unverifiable"


def worker_state(run_dir: Optional[Path]) -> Tuple[str, Any]:
    """What ``run_dir/worker.pid`` points at:

    * ``("ours", process)`` -- alive, same start time (+- 1 s) and a
      command line naming ``writer_train_worker``;
    * ``("unverifiable", pid)`` -- alive with the same start time but its
      command line cannot be read (another user's process): never killed,
      never assumed gone;
    * ``("none", None)`` -- no record, dead, a reused PID, or not ours.
    """
    import psutil

    if run_dir is None:
        return WORKER_NONE, None
    try:
        rec = json.loads((Path(run_dir) / WORKER_PID_NAME).read_text())
        pid = int(rec["pid"])
        created = rec.get("create_time")
    except (OSError, ValueError, KeyError, TypeError):
        return WORKER_NONE, None
    if created is None:
        return WORKER_NONE, None
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - float(created)) > _CREATE_TIME_TOLERANCE_S:
            return WORKER_NONE, None
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return WORKER_NONE, None
        try:
            cmdline = " ".join(proc.cmdline())
        except psutil.AccessDenied:
            return WORKER_UNVERIFIABLE, pid
        if "writer_train_worker" not in cmdline:
            return WORKER_NONE, None
        return WORKER_OURS, proc
    except psutil.AccessDenied:
        return WORKER_UNVERIFIABLE, pid
    except (psutil.Error, OSError):
        return WORKER_NONE, None


def recorded_worker(run_dir: Optional[Path]) -> Any:
    """The recorded worker process when it is provably ours, else None."""
    state, proc = worker_state(run_dir)
    return proc if state == WORKER_OURS else None


def stop_orphan_worker(run_dir: Optional[Path], timeout: float = 10.0) -> bool:
    """Terminate (then kill) the recorded worker if it is ours and alive.
    True when one was stopped. Synchronous."""
    import psutil

    proc = recorded_worker(run_dir)
    if proc is None:
        return False
    logger.warning(
        "stopping an orphaned writer training worker (pid %d, run dir %s)",
        proc.pid,
        run_dir,
    )
    try:
        proc.terminate()
        _, alive = psutil.wait_procs([proc], timeout=timeout)
        for p in alive:
            p.kill()
        psutil.wait_procs(alive, timeout=timeout)
    except psutil.NoSuchProcess:
        pass
    except psutil.Error:
        logger.exception("could not stop the orphaned training worker")
        return False
    if run_dir is not None:
        clear_worker_pid(Path(run_dir))
    return True


# ---- the training-interpreter probe (async, never in the default pool) --------

PROBE_TIMEOUT_S = 300.0
ProbeFn = Callable[[str], Awaitable[Tuple[str, str]]]
GATE_CHECKING_REASON = "Checking training dependencies…"


async def _run_check(
    python: str, code: str, timeout: float
) -> Tuple[Optional[int], str, str]:
    """``(returncode, stdout, error)`` of ``python -c code``; ``error`` is
    non-empty when it could not run (missing interpreter, timeout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            python,
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        return None, "", str(exc) or type(exc).__name__
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, "", f"timed out after {timeout:.0f}s"
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    return proc.returncode, out.decode("utf-8", errors="replace"), ""


async def probe_training_python(
    python: str, timeout: float = PROBE_TIMEOUT_S
) -> Tuple[str, str]:
    """``(deps_reason, trl_reason)`` for ``python`` ("" = ok), run with
    asyncio subprocesses so it never occupies a thread."""
    rc, out, err = await _run_check(python, DEPS_CHECK, timeout)
    deps = (
        f"could not run the training dependency probe: {err}"
        if err
        else parse_deps_probe(rc, out, python)
    )
    if deps:
        return deps, ""
    rc, out, err = await _run_check(python, TRL_CHECK, timeout)
    if err:
        return "", f"could not run the TRL probe: {err}"
    ok, reason = parse_trl_probe(rc, out)
    return "", ("" if ok else reason)


# ---- the manager ------------------------------------------------------------


DatasetInputs = Tuple[Any, str, Optional[wd.CaptionResolver]]


@dataclass
class _Built:
    scope: str
    slug: str
    sft: List[wd.SftExample]
    prefs: List[wd.PrefExample]
    counts: wd.DatasetCounts
    readiness: Dict[str, Any]
    built_at: str


@dataclass
class _Active:
    run_id: int
    adapter_id: int
    name: str
    scope: str
    run_dir: Path
    resumed: bool
    state: str = "starting"  # starting | running | pausing | cancelling
    proc: Optional[asyncio.subprocess.Process] = None
    supervisor: Optional["asyncio.Task[None]"] = None
    progress: Dict[str, Any] = field(default_factory=dict)
    tail: Deque[str] = field(
        default_factory=lambda: collections.deque(maxlen=_TAIL_LINES)
    )
    streams: Dict[str, int] = field(default_factory=lambda: {"stdout": 0, "stderr": 0})


class TrainingManager:
    """See the module docstring. ``gpu_busy()`` returns the reasons the GPU
    cannot be taken now ([] = free); ``release_gpu()`` shuts down every
    VLM client and the CLIP worker; ``dataset_inputs()`` gives ``(prompt
    store, content_mode, caption resolver)`` exactly as T2I inference uses
    them; ``paths()`` the training interpreter / base dir / converter."""

    def __init__(
        self,
        db: Any,
        data_dir: Path,
        *,
        dataset_inputs: Callable[[], DatasetInputs],
        gpu_busy: Callable[[], List[str]],
        release_gpu: Callable[[], Awaitable[None]],
        refresh_adapters: Callable[[], Awaitable[None]],
        broadcast: Callable[[str, Dict[str, Any]], None],
        paths: Callable[[], WriterTrainingPaths],
        gate: Optional[Callable[[WriterTrainingPaths], Gate]] = None,
        backend: str = "real",
        fake: Optional[Dict[str, Any]] = None,
        poll_s: float = 1.0,
        shutdown_grace_s: float = 30.0,
        on_completed: Optional[Callable[[int, bool], Awaitable[None]]] = None,
        on_gpu_released: Optional[Callable[[], None]] = None,
        probe: Optional[ProbeFn] = None,
        now: Optional[Callable[[], datetime]] = None,
        summary_ttl_s: float = 30.0,
        vram_cap: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.db = db
        self.data_dir = Path(data_dir)
        self._dataset_inputs = dataset_inputs
        self._gpu_busy = gpu_busy
        self._release_gpu = release_gpu
        self._refresh_adapters = refresh_adapters
        self._broadcast = broadcast
        self._paths = paths
        # A test override of the whole gate (no probing); else the real
        # gate with the background probe below.
        self._gate = gate
        self._probe = probe or probe_training_python
        # One background probe per training interpreter (single flight).
        self._probes: Dict[str, "asyncio.Task[None]"] = {}
        self._close_hooks: List[Callable[[], Awaitable[None]]] = []
        self.backend = backend
        self.fake = dict(fake or {})
        self.poll_s = float(poll_s)
        self.shutdown_grace_s = float(shutdown_grace_s)
        # Task 9 hook: (adapter_id, render_scorecard) after a completed run.
        self.on_completed = on_completed
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._op_lock = asyncio.Lock()
        self._active: Optional[_Active] = None
        self._gpu_held = False
        self._closing = False
        # Set once release_gpu() ran for the current hold.
        self._released = False
        self.on_gpu_released = on_gpu_released
        # GET /training's dataset summary per scope: (monotonic time, value).
        self.summary_ttl_s = float(summary_ttl_s)
        self._summary_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        # One build per scope at a time: concurrent cold callers share it.
        self._summary_inflight: Dict[str, "asyncio.Future[Dict[str, Any]]"] = {}
        self._summary_generation = 0
        # models.writer_train_vram_cap_gb (raw; validated per run).
        self._vram_cap = vram_cap or (lambda: None)

    # ---- GPU lock -------------------------------------------------------

    def gpu_locked(self) -> bool:
        """True while a run holds the GPU (consulted by every consumer)."""
        return self._gpu_held

    async def _take_gpu(self) -> None:
        """Set the lock FIRST (new consumer requests 409 from here on), then
        check nothing is using the GPU, free it, and check again (a request
        admitted just before the lock may have registered work meanwhile)."""
        self._gpu_held = True
        try:
            busy = self._gpu_busy()
            if busy:
                raise TrainingError(409, "gpu_busy", reasons=busy)
            self._released = True
            await self._release_gpu()
            busy = self._gpu_busy()
            if busy:
                raise TrainingError(409, "gpu_busy", reasons=busy)
        except BaseException:
            self._drop_lock()
            raise

    def _drop_lock(self) -> None:
        """Release the GPU (completion, failure, pause, cancel or a refused
        start) and tell the lifespan (it restarts work it interrupted, e.g.
        the hindsight briefs job)."""
        self._active = None
        if not self._gpu_held:
            return
        self._gpu_held = False
        released, self._released = self._released, False
        hook = self.on_gpu_released
        if released and hook is not None and not self._closing:
            try:
                hook()
            except Exception:
                logger.exception("GPU release hook failed")

    # ---- dataset --------------------------------------------------------

    def _categories(self) -> Dict[int, str]:
        return {int(c["id"]): str(c["name"]) for c in self.db.list_categories()}

    def _normalize_scope(self, scope: Any) -> Tuple[str, Dict[int, str]]:
        categories = self._categories()
        try:
            cid = wd.parse_scope(scope, categories)
        except ValueError:
            raise TrainingError(422, "unknown_scope") from None
        return (wd.SCOPE_ALL if cid is None else str(cid)), categories

    def _build(self, scope: Any) -> _Built:
        """Synchronous (callers use ``asyncio.to_thread``)."""
        norm, categories = self._normalize_scope(scope)
        store, content_mode, resolve = self._dataset_inputs()
        now = self._now()
        sft, prefs, counts = wd.build_examples(
            self.db, norm, store, now, content_mode=content_mode, resolve=resolve
        )
        cutoff = wd.last_adapter_cutoff(self.db.list_writer_adapters(), norm)
        return _Built(
            scope=norm,
            slug=scope_slug(norm, categories),
            sft=sft,
            prefs=prefs,
            counts=counts,
            readiness=wd.readiness(counts, cutoff),
            built_at=now.astimezone(timezone.utc).isoformat(),
        )

    async def dataset_summary(self, scope: Any = wd.SCOPE_ALL) -> Dict[str, Any]:
        """Counts + readiness of ``scope``, cached ``summary_ttl_s`` per scope
        (the window polls it; a build reads the whole library). Starting or
        completing a run invalidates it."""
        key = str(scope).strip().lower()
        loop = asyncio.get_running_loop()
        hit = self._summary_cache.get(key)
        if hit is not None and loop.time() - hit[0] < self.summary_ttl_s:
            return hit[1]
        task = self._summary_inflight.get(key)
        if task is None:
            task = loop.create_task(
                self._summary_build(key, scope, self._summary_generation),
                name=f"writer-dataset-summary:{key}",
            )
            # Every waiter may have been cancelled: retrieve the outcome so
            # an error is never reported as "never retrieved".
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._summary_inflight[key] = task
        # A cancelled caller never cancels the build the others await.
        return await asyncio.shield(task)

    async def _summary_build(
        self, key: str, scope: Any, generation: int
    ) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        try:
            value = await self._dataset_summary(scope)
            # An invalidation during the build makes its result stale.
            if generation == self._summary_generation:
                self._summary_cache[key] = (loop.time(), value)
            return value
        finally:
            if self._summary_inflight.get(key) is asyncio.current_task():
                del self._summary_inflight[key]

    def invalidate_dataset_summary(self) -> None:
        self._summary_cache.clear()
        self._summary_inflight.clear()
        self._summary_generation += 1

    async def _dataset_summary(self, scope: Any) -> Dict[str, Any]:
        built = await asyncio.to_thread(self._build, scope)
        _, holdout = wd.split_holdout(built.prefs)
        return {
            "scope": built.scope,
            "counts": built.counts.to_dict(),
            "readiness": built.readiness,
            "holdout_pairs": len(holdout),
            "holdout_fraction": wd.DEFAULT_HOLDOUT_FRACTION,
        }

    def next_adapter_name(self, scope: Any = wd.SCOPE_ALL) -> str:
        """The name a run of ``scope`` started now would get (the window
        shows it; the final one is assigned at start). Synchronous."""
        norm, categories = self._normalize_scope(scope)
        return str(self.db.next_adapter_name(scope_slug(norm, categories)))

    async def dataset_preview(self, scope: Any = wd.SCOPE_ALL) -> Dict[str, Any]:
        built = await asyncio.to_thread(self._build, scope)
        return {
            "scope": built.scope,
            "examples": wd.preview_mix(built.prefs, built.sft),
        }

    @property
    def op_lock(self) -> asyncio.Lock:
        """Serialises start / resume / cancel-by-id and adapter deletes."""
        return self._op_lock

    def add_close_hook(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Awaited at the end of :meth:`aclose` (the lifespan's own cleanup,
        e.g. a scheduled briefs restart)."""
        self._close_hooks.append(hook)

    def probing(self, python: Optional[str] = None) -> bool:
        python = python or self._paths().python
        task = self._probes.get(python)
        return task is not None and not task.done()

    def _ensure_probe(self, python: str, force: bool = False) -> bool:
        """Start the background probe unless a fresh result is cached or one
        is already running (single flight). ``force`` (recheck) drops the
        cache and restarts it. True while a probe is in flight."""
        task = self._probes.get(python)
        if task is not None and not task.done():
            if not force:
                return True
            task.cancel()
        if force:
            clear_writer_training_probe_cache(python)
        elif cached_training_probe(python) is not None:
            return False
        if self._closing:
            return False
        new = asyncio.get_running_loop().create_task(
            self._probe_task(python), name=f"writer-train-probe:{python}"
        )
        self._probes[python] = new
        return True

    async def _probe_task(self, python: str) -> None:
        me = asyncio.current_task()
        try:
            deps, trl = await self._probe(python)
            store_training_probe(python, deps, trl)
            # The Training tab shows only cached results: tell it one landed.
            self._emit("gate_changed", {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("writer training probe failed")
            store_training_probe(
                python,
                f"could not run the training dependency probe: {type(exc).__name__}",
                "",
            )
        finally:
            if self._probes.get(python) is me:
                del self._probes[python]

    async def gate_status(self, recheck: bool = False) -> Tuple[Gate, bool]:
        """``(gate, checking)`` without ever waiting for the dependency
        probe: it runs in the background and, while it does, the gate is
        unavailable with "Checking training dependencies…" in place of its
        result (the other pieces are reported as usual)."""
        paths = self._paths()
        if self._gate is not None:
            return await asyncio.to_thread(self._gate, paths), False
        checking = self._ensure_probe(paths.python, force=recheck)
        gate = await asyncio.to_thread(
            lambda: writer_training_gate(detect_hardware(), paths, probe=False)
        )
        if checking:
            reasons = [
                GATE_CHECKING_REASON if r == TRAINING_DEPS_UNCHECKED else r
                for r in gate.reasons
            ]
            if GATE_CHECKING_REASON not in reasons:
                reasons.append(GATE_CHECKING_REASON)
            gate = Gate(False, False, " ".join(reasons), reasons)
        return gate, checking

    # ---- start / resume -------------------------------------------------

    def _init_adapter_dir(self, start_from: Any, scope: str) -> Optional[str]:
        if start_from in (None, "", "fresh"):
            return None
        try:
            aid = int(start_from)
        except (TypeError, ValueError):
            raise TrainingError(422, "invalid_start_from") from None
        row = self.db.get_writer_adapter(aid)
        if row is None or row["deleted_at"] is not None:
            raise TrainingError(404, "unknown_adapter")
        if row["status"] not in SERVABLE_STATUSES or row["kind"] != "t2i":
            raise TrainingError(409, "adapter_not_continuable")
        peft = adapter_dir(self.data_dir, aid) / ADAPTER_PEFT_DIRNAME
        if not (peft / ADAPTER_CONFIG).is_file():
            raise TrainingError(409, "adapter_not_continuable")
        return str(peft)

    def _runs_root(self) -> Path:
        return self.data_dir / "training" / "runs"

    def _create_run(
        self,
        built: _Built,
        hyperparams: Dict[str, Any],
        effort: str,
        init_adapter_dir: Optional[str],
        start_from: Any,
        render_scorecard: bool,
        vram_cap_gb: Optional[float] = None,
    ) -> _Active:
        """Register the adapter (``training``) and the run, write the JSONL
        files and task.json. Synchronous."""
        name = self.db.next_adapter_name(built.slug)
        try:
            adapter_id = self.db.create_writer_adapter(
                name=name,
                scope=built.scope,
                kind="t2i",
                base_model=ADAPTER_BASE_MODEL,
                status=STATUS_TRAINING,
                hyperparams=hyperparams,
                counts=built.counts.to_dict(),
            )
        except ValueError:  # a concurrent rename took the name: next one
            name = self.db.next_adapter_name(built.slug)
            adapter_id = self.db.create_writer_adapter(
                name=name,
                scope=built.scope,
                kind="t2i",
                base_model=ADAPTER_BASE_MODEL,
                status=STATUS_TRAINING,
                hyperparams=hyperparams,
                counts=built.counts.to_dict(),
            )
        options = {
            "scope": built.scope,
            "effort": effort,
            "hyperparams": hyperparams,
            "start_from": "fresh" if init_adapter_dir is None else str(start_from),
            "render_scorecard": bool(render_scorecard),
            "data_cutoff": built.built_at,
            "vram_cap_gb": vram_cap_gb,
        }
        run_id = self.db.create_training_run(
            adapter_id, status=RUN_RUNNING, options=options
        )
        run_dir = self._runs_root() / str(run_id)
        if run_dir.exists():  # a stale dir from a reset DB: never reuse it
            shutil.rmtree(run_dir)
        data = run_dir / "data"
        train, holdout = wd.split_holdout(built.prefs)
        sft = wd.sft_without_holdout(built.sft, holdout)
        wd.write_jsonl(sft, data / "sft.jsonl")
        wd.write_jsonl(train, data / "prefs.jsonl")
        wd.write_jsonl(holdout, data / "holdout.jsonl")
        if init_adapter_dir is not None:
            # The run keeps its own copy of the "Continue from" PEFT dir:
            # deleting the source adapter while this run is paused or
            # failed must never break its resume.
            copy = run_dir / INIT_ADAPTER_DIRNAME
            shutil.copytree(init_adapter_dir, copy)
            init_adapter_dir = str(copy)
        paths = self._paths()
        task: Dict[str, Any] = {
            "backend": self.backend,
            "base_dir": str(paths.base_dir),
            "data_root": str(run_dir),
            "sft_path": str(data / "sft.jsonl"),
            "pref_path": str(data / "prefs.jsonl"),
            "holdout_path": str(data / "holdout.jsonl"),
            "out_dir": str(run_dir / "out"),
            "checkpoint_dir": str(run_dir / "checkpoints"),
            "control_dir": str(run_dir / "control"),
            "resume": False,
            "hyperparams": hyperparams,
            "init_adapter_dir": init_adapter_dir,
            "vram_cap_gb": vram_cap_gb,
            "meta": {
                "counts": built.counts.to_dict(),
                "scope": built.scope,
                "name": name,
                "run_id": run_id,
                "adapter_id": adapter_id,
                "effort": effort,
                "data_cutoff": built.built_at,
                "n_train_pairs": len(train),
                "n_holdout_pairs": len(holdout),
            },
        }
        if self.backend == "real":
            task["converter"] = {
                "python": paths.python,
                "script": str(paths.converter_script),
            }
        if self.fake:
            task["fake"] = self.fake
        ww.write_json_atomic(run_dir / "task.json", task)
        self.db.update_training_run(
            run_id,
            run_dir=str(run_dir),
            checkpoint_dir=str(run_dir / "checkpoints"),
        )
        return _Active(
            run_id=run_id,
            adapter_id=adapter_id,
            name=name,
            scope=built.scope,
            run_dir=run_dir,
            resumed=False,
        )

    async def start(
        self,
        scope: Any = wd.SCOPE_ALL,
        effort: str = DEFAULT_EFFORT,
        overrides: Optional[Dict[str, Any]] = None,
        start_from: Any = "fresh",
        render_scorecard: bool = False,
        vram_cap_gb: Any = None,
    ) -> int:
        """Start a fresh run; returns its id. Raises :class:`TrainingError`
        (409 ``already_training`` / ``gate_unavailable`` / ``not_ready`` /
        ``gpu_busy``; 422 for bad input). ``vram_cap_gb``: None = the
        config's ``models.writer_train_vram_cap_gb``, 0 = off."""
        async with self._op_lock:
            self._refuse_if_running()
            hyperparams = resolve_hyperparams(effort, overrides)
            cap = resolve_vram_cap(vram_cap_gb, self._config_cap())
            await self._require_gate()
            norm, _ = await asyncio.to_thread(self._normalize_scope, scope)
            init_dir = await asyncio.to_thread(self._init_adapter_dir, start_from, norm)
            built = await asyncio.to_thread(self._build, norm)
            if not built.readiness["trainable"]:
                raise TrainingError(409, "not_ready", readiness=built.readiness)
            await self._take_gpu()
            try:
                active = await asyncio.to_thread(
                    self._create_run,
                    built,
                    hyperparams,
                    effort,
                    init_dir,
                    start_from,
                    render_scorecard,
                    cap,
                )
                self._active = active
                self.invalidate_dataset_summary()
                await self._spawn(active)
            except BaseException:
                await self._abort_start()
                raise
            logger.info(
                "writer training run %d started (adapter %d, scope %s, effort %s)",
                active.run_id,
                active.adapter_id,
                active.scope,
                effort,
            )
            self._emit("adapters_changed", {})
            return active.run_id

    async def resume(self, run_id: int) -> int:
        """Resume a paused (or failed) run from its last checkpoint, with
        the same task (hyperparameters and JSONL files)."""
        async with self._op_lock:
            self._refuse_if_running()
            run = await asyncio.to_thread(self.db.get_training_run, int(run_id))
            if run is None:
                raise TrainingError(404, "unknown_run")
            if run["status"] not in RESUMABLE_RUN_STATUSES:
                raise TrainingError(409, "not_resumable", run_status=run["status"])
            adapter = await asyncio.to_thread(
                self.db.get_writer_adapter, run["adapter_id"]
            )
            run_dir = Path(run["run_dir"] or "")
            task_path = run_dir / "task.json"
            if adapter is None or adapter["deleted_at"] is not None:
                raise TrainingError(409, "not_resumable", run_status="adapter_deleted")
            if not run["run_dir"] or not task_path.is_file():
                raise TrainingError(409, "not_resumable", run_status="files_missing")
            state, who = await asyncio.to_thread(worker_state, run_dir)
            if state == WORKER_OURS:
                # Never two workers on one checkpoint dir.
                raise TrainingError(409, "worker_alive")
            if state == WORKER_UNVERIFIABLE:
                raise TrainingError(
                    409,
                    "worker_unverifiable",
                    pid=who,
                    message=(
                        "A process with the recorded training worker's PID is "
                        f"running but cannot be inspected; stop the process "
                        f"yourself, PID {who}."
                    ),
                )
            await self._require_gate()
            await self._take_gpu()
            # Re-read after the await-heavy GPU take: a delete may have
            # discarded the run (or its adapter) meanwhile.
            run = await asyncio.to_thread(self.db.get_training_run, int(run_id))
            adapter = await asyncio.to_thread(
                self.db.get_writer_adapter, int(run["adapter_id"]) if run else 0
            )
            if (
                run is None
                or run["status"] not in RESUMABLE_RUN_STATUSES
                or adapter is None
                or adapter["deleted_at"] is not None
            ):
                self._drop_lock()
                raise TrainingError(409, "run_discarded")
            active = _Active(
                run_id=int(run["id"]),
                adapter_id=int(run["adapter_id"]),
                name=str(adapter["name"]),
                scope=str(adapter["scope"]),
                run_dir=run_dir,
                resumed=True,
            )
            try:
                await asyncio.to_thread(self._prepare_resume, active, task_path)
                self._active = active
                await self._spawn(active)
            except BaseException:
                try:
                    await self._stop_spawned(active)
                except Exception:
                    logger.exception(
                        "Stopping the training worker of a failed resume raised"
                    )
                self._drop_lock()
                await asyncio.to_thread(
                    self._set_states, active, run["status"], adapter["status"]
                )
                raise
            logger.info("writer training run %d resumed", active.run_id)
            self._emit("adapters_changed", {})
            return active.run_id

    def _prepare_resume(self, active: _Active, task_path: Path) -> None:
        task = json.loads(task_path.read_text())
        task["resume"] = True
        if task.get("backend") == "real":
            # The converter is not part of the resume fingerprint: pick up a
            # moved llama.cpp checkout.
            paths = self._paths()
            task["converter"] = {
                "python": paths.python,
                "script": str(paths.converter_script),
            }
        ww.write_json_atomic(task_path, task)
        self._set_states(active, RUN_RUNNING, STATUS_TRAINING, error=None)

    def _set_states(
        self, active: _Active, run_status: str, adapter_status: str, **extra: Any
    ) -> None:
        self.db.update_training_run(active.run_id, status=run_status, **extra)
        self.db.set_writer_adapter_status(active.adapter_id, adapter_status, **extra)

    def _refuse_if_running(self) -> None:
        if self._active is not None or self._gpu_held:
            raise TrainingError(409, "already_training")
        if self._closing:
            raise TrainingError(503, "shutting_down")

    async def _require_gate(self) -> None:
        gate, checking = await self.gate_status()
        if checking:
            raise TrainingError(409, "gate_checking")
        if not gate.available:
            raise TrainingError(
                409, "gate_unavailable", reasons=list(gate.reasons or [gate.reason])
            )

    @staticmethod
    async def _stop_spawned(active: Optional[_Active]) -> None:
        """A start/resume that failed after the worker was spawned (e.g.
        writing ``worker.pid`` raised): stop that worker and reap it BEFORE
        the lock is released, so it never trains on behind a free GPU."""
        if active is None:
            return
        sup = active.supervisor
        if sup is not None and not sup.done():
            sup.cancel()
        proc = active.proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    async def _abort_start(self) -> None:
        """A start that failed after registering: the worker (if spawned) is
        stopped, the run is failed, the adapter too, and the GPU is
        released."""
        active = self._active
        try:
            await self._stop_spawned(active)
        except Exception:
            logger.exception("Stopping the training worker of a failed start raised")
        self._drop_lock()
        if active is None:
            return
        try:
            await asyncio.to_thread(
                self._set_states,
                active,
                RUN_FAILED,
                STATUS_FAILED,
                error="the training worker could not start",
            )
        except Exception:
            logger.exception("Marking an aborted training start failed")

    # ---- the subprocess ---------------------------------------------------

    def _command(self, active: _Active) -> List[str]:
        return [
            self._paths().python,
            "-m",
            WORKER_MODULE,
            str(active.run_dir / "task.json"),
        ]

    async def _spawn(self, active: _Active) -> None:
        control = active.run_dir / "control"
        await asyncio.to_thread(self._empty_dir, control)
        # The previous attempt (already recorded) must not be recorded again
        # if this worker dies before writing its own profile.
        await asyncio.to_thread(training_profiles.set_aside_run_profile, active.run_dir)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p
        )
        env["PYTHONUNBUFFERED"] = "1"
        active.proc = await asyncio.create_subprocess_exec(
            *self._command(active),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
            limit=1 << 20,
        )
        await asyncio.to_thread(write_worker_pid, active.run_dir, active.proc.pid)
        active.state = "running"
        active.supervisor = asyncio.create_task(
            self._supervise(active), name=f"writer-train-{active.run_id}"
        )
        self._emit(
            "run_started",
            {
                "run_id": active.run_id,
                "adapter_id": active.adapter_id,
                "name": active.name,
                "scope": active.scope,
                "resumed": active.resumed,
            },
        )

    @staticmethod
    def _empty_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    async def _drain(self, active: _Active, stream: Any, name: str) -> None:
        """Forward one worker pipe line by line (never left undrained: a
        full pipe would hang the worker). HF's PrinterCallback prints log
        dicts on stdout (DEBUG); the worker's own logging is on stderr."""
        while True:
            try:
                raw = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                raw = await stream.read(1 << 16)  # an over-long line: skip it
                if not raw:
                    return
                continue
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            active.streams[name] += 1
            active.tail.append(text)
            if _ERROR_LINE.search(text):
                level = logging.WARNING
            else:
                level = logging.INFO if name == "stderr" else logging.DEBUG
            worker_logger.log(level, "[run %d %s] %s", active.run_id, name, text)

    async def _supervise(self, active: _Active) -> None:
        proc = active.proc
        assert proc is not None
        drains = [
            asyncio.create_task(self._drain(active, proc.stdout, "stdout")),
            asyncio.create_task(self._drain(active, proc.stderr, "stderr")),
        ]
        rc: Optional[int] = None
        try:
            while rc is None:
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    await self._poll_progress(active)
            await asyncio.gather(*drains, return_exceptions=True)
            await asyncio.to_thread(clear_worker_pid, active.run_dir)
            await self._poll_progress(active)
            await self._finish(active, rc)
        except asyncio.CancelledError:
            for t in drains:
                t.cancel()
            raise
        except Exception:
            logger.exception("writer training run %d: supervisor failed", active.run_id)
            try:
                await self._failed(active, "internal error while finishing the run")
            except Exception:
                logger.exception("Marking run %d failed raised", active.run_id)
        finally:
            if self._active is active:
                self._drop_lock()

    async def _poll_progress(self, active: _Active) -> None:
        path = active.run_dir / "out" / ww.PROGRESS_NAME
        try:
            raw = await asyncio.to_thread(path.read_text)
            progress = json.loads(raw)
        except (OSError, ValueError):
            return
        if not isinstance(progress, dict) or progress == active.progress:
            return
        step_changed = progress.get("step") != active.progress.get("step") or (
            progress.get("total_steps") != active.progress.get("total_steps")
        )
        active.progress = progress
        if step_changed:
            try:
                await asyncio.to_thread(
                    self.db.update_training_run,
                    active.run_id,
                    step=int(progress.get("step") or 0),
                    total_steps=progress.get("total_steps"),
                )
            except Exception:
                logger.exception("Recording training progress failed")
        self._emit(
            "run_progress",
            {"run_id": active.run_id, "adapter_id": active.adapter_id, **progress},
        )

    def _read_result(self, active: _Active) -> Dict[str, Any]:
        try:
            data = json.loads((active.run_dir / "out" / ww.RESULT_NAME).read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _verdict(self, rc: int, result: Dict[str, Any]) -> str:
        """The attempt's outcome as :meth:`_finish` will treat it."""
        if rc == ww.EXIT_OK and result.get("status") == "ok":
            return "completed"
        if rc == ww.EXIT_CANCELLED:
            return "cancelled"
        if rc == ww.EXIT_PAUSED or self._closing:
            return "paused"
        return "oom" if result.get("error_code") == "oom" else "failed"

    async def _finish(self, active: _Active, rc: int) -> None:
        result = await asyncio.to_thread(self._read_result, active)
        # Before any outcome handler: cancel / completion delete the run dir.
        await asyncio.to_thread(
            training_profiles.record_attempt,
            self.data_dir,
            active,
            verdict=self._verdict(rc, result),
            exit_code=rc,
        )
        status = result.get("status")
        if rc == ww.EXIT_OK and status == "ok":
            await self._complete(active, result)
        elif rc == ww.EXIT_CANCELLED:
            await self._cancelled(active)
        elif rc == ww.EXIT_PAUSED or self._closing:
            # A server stop pauses (and terminates a worker that does not
            # reach a step boundary in time): the last checkpoint resumes.
            await self._paused(active)
        else:
            error = result.get("error") or (
                f"the training worker exited with code {rc}"
                + (f": {active.tail[-1]}" if active.tail else "")
            )
            await self._failed(active, str(error)[:2000])

    # ---- outcomes ---------------------------------------------------------

    def _install_adapter(self, active: _Active) -> Path:
        """Copy the worker's outputs into the adapter folder (atomically
        replacing it). Synchronous."""
        out = active.run_dir / "out"
        peft = out / ww.PEFT_DIRNAME
        dest = adapter_dir(self.data_dir, active.adapter_id)
        tmp = dest.with_name(f".{dest.name}.installing")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        shutil.copy2(out / ww.GGUF_NAME, tmp / ADAPTER_GGUF)
        shutil.copy2(peft / ADAPTER_CONFIG, tmp / ADAPTER_CONFIG)
        shutil.copy2(out / ww.META_NAME, tmp / ADAPTER_META)
        shutil.copytree(peft, tmp / ADAPTER_PEFT_DIRNAME)
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
        return dest

    async def _complete(self, active: _Active, result: Dict[str, Any]) -> None:
        run = await asyncio.to_thread(self.db.get_training_run, active.run_id)
        if run is None or run["status"] == RUN_COMPLETED:
            logger.info("writer training run %d already completed", active.run_id)
            return
        try:
            dest = await asyncio.to_thread(self._install_adapter, active)
        except OSError as exc:
            await self._failed(active, f"could not install the adapter files: {exc}")
            return
        options = run.get("options") or {}
        fields: Dict[str, Any] = {
            "file_path": str(dest / ADAPTER_GGUF),
            "holdout_agreement": result.get("holdout_agreement"),
            "base_agreement": result.get("base_agreement"),
            "data_cutoff": options.get("data_cutoff") or self._now().isoformat(),
        }
        registered = await asyncio.to_thread(
            self.db.complete_training_run, active.run_id, active.adapter_id, fields
        )
        if not registered:
            logger.info("writer training run %d already completed", active.run_id)
            return
        self.invalidate_dataset_summary()
        logger.info(
            "writer training run %d completed: adapter %d (holdout %s, base %s)",
            active.run_id,
            active.adapter_id,
            result.get("holdout_agreement"),
            result.get("base_agreement"),
        )
        # The checkpoints (and a refused-wipe leftover) and the JSONL live
        # in the run dir; the adapter folder now holds everything kept.
        await asyncio.to_thread(shutil.rmtree, active.run_dir, True)
        # Apply the new adapter to the writer BEFORE releasing the GPU: the
        # release restarts an interrupted briefs job, which may spawn the
        # (shared) writer client -- it must already carry the new --lora
        # list. Refreshing never spawns (an idle stale client is only
        # evicted), so it is safe under the lock.
        await self._refresh_writer()
        if self._active is active:
            self._drop_lock()
        self._emit("adapters_changed", {})
        self._emit(
            "run_completed",
            {"run_id": active.run_id, "adapter_id": active.adapter_id},
        )
        hook = self.on_completed
        if hook is not None and self._closing:
            logger.info(
                "writer training run %d completed during shutdown: no scorecard",
                active.run_id,
            )
        elif hook is not None:
            try:
                await hook(active.adapter_id, bool(options.get("render_scorecard")))
            except Exception:
                logger.exception("Post-training hook failed")

    async def _paused(self, active: _Active) -> None:
        await asyncio.to_thread(self._set_states, active, RUN_PAUSED, STATUS_PAUSED)
        if self._active is active:
            self._drop_lock()
        logger.info("writer training run %d paused", active.run_id)
        self._emit(
            "run_paused",
            {
                "run_id": active.run_id,
                "adapter_id": active.adapter_id,
                "step": active.progress.get("step"),
                "total_steps": active.progress.get("total_steps"),
            },
        )
        self._emit("adapters_changed", {})

    async def _failed(self, active: _Active, error: str) -> None:
        await asyncio.to_thread(
            self._set_states, active, RUN_FAILED, STATUS_FAILED, error=error
        )
        if self._active is active:
            self._drop_lock()
        logger.warning("writer training run %d failed: %s", active.run_id, error)
        self._emit(
            "run_failed",
            {"run_id": active.run_id, "adapter_id": active.adapter_id, "error": error},
        )
        self._emit("adapters_changed", {})

    def _discard_run(
        self, run_id: int, adapter_id: int, run_dir: Optional[str]
    ) -> None:
        """Cancelled: the run's files go, the adapter row is soft-deleted
        (its name stays taken, like every deleted adapter). Synchronous."""
        self.db.update_training_run(run_id, status=RUN_CANCELLED)
        row = self.db.get_writer_adapter(adapter_id)
        if row is not None and row["status"] != STATUS_DELETED:
            self.db.soft_delete_writer_adapter(adapter_id)
        folder = adapter_dir(self.data_dir, adapter_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)

    async def _cancelled(self, active: _Active) -> None:
        await asyncio.to_thread(
            self._discard_run, active.run_id, active.adapter_id, str(active.run_dir)
        )
        if self._active is active:
            self._drop_lock()
        logger.info("writer training run %d cancelled", active.run_id)
        self._emit(
            "run_cancelled", {"run_id": active.run_id, "adapter_id": active.adapter_id}
        )
        self._emit("adapters_changed", {})

    async def _refresh_writer(self) -> None:
        """The writer server reloads its adapter list (restarted when idle);
        call it BEFORE ``adapters_changed`` reaches the UI."""
        try:
            await self._refresh_adapters()
        except Exception:
            logger.exception("Applying the adapter registry to the writer failed")

    # ---- pause / cancel ---------------------------------------------------

    def _signal(self, active: _Active, name: str) -> None:
        control = active.run_dir / "control"
        control.mkdir(parents=True, exist_ok=True)
        (control / name).write_text("")

    async def pause(self) -> Dict[str, Any]:
        """Ask the running worker to checkpoint and stop (it exits at the
        next step boundary; a pause during eval lands at the convert
        boundary). The GPU is released once it has exited."""
        active = self._active
        if active is None or active.state not in ("running",):
            raise TrainingError(409, "not_running")
        await asyncio.to_thread(self._signal, active, ww.PAUSE_SIGNAL)
        active.state = "pausing"
        return self.status()

    async def cancel(self, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel the running run (the worker deletes its checkpoints), or
        a paused / failed one by id (its files are removed here)."""
        active = self._active
        if active is not None and (run_id is None or int(run_id) == active.run_id):
            if active.state == "cancelling":
                return self.status()
            await asyncio.to_thread(self._signal, active, ww.CANCEL_SIGNAL)
            active.state = "cancelling"
            return self.status()
        if run_id is None:
            raise TrainingError(409, "not_running")
        async with self._op_lock:
            run = await asyncio.to_thread(self.db.get_training_run, int(run_id))
            if run is None:
                raise TrainingError(404, "unknown_run")
            if run["status"] not in RESUMABLE_RUN_STATUSES:
                raise TrainingError(409, "not_cancellable", run_status=run["status"])
            await asyncio.to_thread(
                self._discard_run,
                int(run["id"]),
                int(run["adapter_id"]),
                run["run_dir"],
            )
        self._emit(
            "run_cancelled", {"run_id": int(run["id"]), "adapter_id": run["adapter_id"]}
        )
        self._emit("adapters_changed", {})
        return self.status()

    # ---- status -------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        active = self._active
        run: Optional[Dict[str, Any]] = None
        if active is not None:
            run = {
                "run_id": active.run_id,
                "adapter_id": active.adapter_id,
                "name": active.name,
                "scope": active.scope,
                "resumed": active.resumed,
                "state": active.state,
                "progress": dict(active.progress),
            }
        return {
            "state": active.state if active is not None else "idle",
            "gpu_locked": self._gpu_held,
            "run": run,
        }

    def _config_cap(self) -> Any:
        try:
            return self._vram_cap()
        except Exception:
            logger.exception("Reading models.writer_train_vram_cap_gb failed")
            return None

    def vram_cap_info(self) -> Dict[str, Any]:
        """The rehearsal knob for the window: the config default (validated)
        and the choices."""
        return {
            "default": config_vram_cap(self._config_cap()),
            "choices": list(wprof.VRAM_CAP_CHOICES),
            "min": int(wprof.VRAM_CAP_MIN_GB),
        }

    def profiles(self) -> Dict[str, Any]:
        """``GET /api/writers/training/profiles``: the bounded history
        (newest first) and the current run's live profile, else the last
        attempt's. Read-only, no GPU lock. Synchronous."""
        active = self._active
        return training_profiles.profiles_payload(
            self.data_dir, active.run_dir if active is not None else None
        )

    def resumable_runs(self) -> List[Dict[str, Any]]:
        """Paused / failed runs, newest first, with their adapter's name.
        Synchronous."""
        out: List[Dict[str, Any]] = []
        for run in reversed(self.db.list_training_runs(RESUMABLE_RUN_STATUSES)):
            adapter = self.db.get_writer_adapter(run["adapter_id"])
            if adapter is None or adapter["deleted_at"] is not None:
                continue
            out.append(
                {
                    "run_id": run["id"],
                    "adapter_id": run["adapter_id"],
                    "name": adapter["name"],
                    "scope": adapter["scope"],
                    "status": run["status"],
                    "step": run["step"],
                    "total_steps": run["total_steps"],
                    "error": run["error"],
                    "updated_at": run["updated_at"],
                    "options": run["options"],
                }
            )
        return out

    def runs_using_base(self, base_dir: Path) -> List[int]:
        """Runs that read the HF weights in ``base_dir``: the active run and
        every running / paused / failed (resumable) run whose task.json names
        it -- deleting the weights would break them. A run whose task.json
        cannot be read counts (conservative). Synchronous."""
        want = Path(base_dir).expanduser().resolve()
        out: List[int] = []
        if self._active is not None:
            out.append(self._active.run_id)
        statuses = (RUN_RUNNING,) + tuple(RESUMABLE_RUN_STATUSES)
        for run in self.db.list_training_runs(statuses):
            if run["id"] in out:
                continue
            run_dir = run.get("run_dir")
            if not run_dir:
                continue
            try:
                task = json.loads((Path(run_dir) / "task.json").read_text())
                used = Path(str(task["base_dir"])).expanduser().resolve()
            except FileNotFoundError:
                if not Path(run_dir).exists():
                    continue  # nothing left to resume
                out.append(run["id"])
                continue
            except (OSError, ValueError, KeyError, TypeError):
                out.append(run["id"])
                continue
            if used == want:
                out.append(run["id"])
        return out

    # ---- lifecycle ----------------------------------------------------------

    async def recover(self) -> List[int]:
        """Startup: runs a stopped server left ``running`` become
        ``paused`` (resumable from their last checkpoint)."""
        running = await asyncio.to_thread(self.db.list_training_runs, [RUN_RUNNING])
        for run in running:
            # A server that died hard may have left its worker training on
            # the GPU: stop it (only when provably ours), then pause.
            if run.get("run_dir"):
                await asyncio.to_thread(stop_orphan_worker, Path(run["run_dir"]))
                # Nobody recorded that attempt; the resume would overwrite
                # its profile.json.
                await asyncio.to_thread(self._record_interrupted, run)
        ids: List[int] = await asyncio.to_thread(self.db.mark_interrupted_training_runs)
        if ids:
            logger.info("writer training runs marked paused at startup: %s", ids)
        return ids

    def _record_interrupted(self, run: Dict[str, Any]) -> None:
        adapter = self.db.get_writer_adapter(run["adapter_id"])
        training_profiles.record_attempt(
            self.data_dir,
            SimpleNamespace(
                run_id=run["id"],
                adapter_id=run["adapter_id"],
                name=adapter["name"] if adapter else None,
                run_dir=Path(run["run_dir"]),
            ),
            verdict="paused",
            exit_code=-1,
            ended_by="server_restart",
        )

    async def aclose(self) -> None:
        """Server shutdown: pause the worker (a checkpoint at the next step
        boundary), terminate it after the grace period, and record the run
        ``paused``."""
        self._closing = True
        for task in list(self._probes.values()):
            task.cancel()
        try:
            await self._pause_for_shutdown()
        finally:
            for hook in self._close_hooks:
                try:
                    await hook()
                except Exception:
                    logger.exception("Training close hook failed")

    async def _pause_for_shutdown(self) -> None:
        active = self._active
        if active is None or active.proc is None:
            return
        proc = active.proc
        sup = active.supervisor
        if proc.returncode is None:
            try:
                await asyncio.to_thread(self._signal, active, ww.PAUSE_SIGNAL)
            except OSError:
                logger.exception("Could not signal the training worker to pause")
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.shutdown_grace_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "training worker did not pause within %.0fs; terminating",
                    self.shutdown_grace_s,
                )
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        if sup is not None:
            try:
                await asyncio.wait_for(asyncio.shield(sup), timeout=30.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                sup.cancel()
            except Exception:
                logger.exception("Training supervisor raised on shutdown")

    # ---- helpers --------------------------------------------------------------

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            self._broadcast(event, payload)
        except Exception:
            logger.exception("training broadcast failed")


def discard_open_runs(db: Any, adapter_id: int) -> List[int]:
    """Deleting a paused / failed adapter discards its open runs: run rows
    ``cancelled``, their files removed (a still-live recorded worker is
    stopped first). Returns the run ids. Synchronous."""
    out: List[int] = []
    for run in db.list_training_runs(RESUMABLE_RUN_STATUSES):
        if int(run["adapter_id"]) != int(adapter_id):
            continue
        run_dir = run.get("run_dir")
        if run_dir:
            stop_orphan_worker(Path(run_dir))
        db.update_training_run(int(run["id"]), status=RUN_CANCELLED)
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)
        out.append(int(run["id"]))
    return out


# ---- singleton (installed by the lifespan) ------------------------------------

_manager: Optional[TrainingManager] = None


def set_training_manager(manager: Optional[TrainingManager]) -> None:
    global _manager
    _manager = manager


def get_training_manager() -> Optional[TrainingManager]:
    return _manager
