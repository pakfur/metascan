"""REST endpoints for prompt-writer adapters (taste-learning fine-tunes)."""

import asyncio
import logging
import shutil
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db
from backend.services.gpu_lock import GPU_FREE
from backend.ws.manager import ws_manager
from metascan.core.writer_adapters import (
    STATUS_TRAINING,
    adapter_dir,
    adapter_writer_id,
)
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writers", tags=["writers"])

_NAME_CODES = {"invalid_name": 422, "duplicate_name": 409}


class RenameRequest(BaseModel):
    name: str


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {**row, "writer_id": adapter_writer_id(row["id"])}


def _live(adapter_id: int) -> Dict[str, Any]:
    row = get_db().get_writer_adapter(adapter_id)
    if row is None or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail={"code": "unknown_adapter"})
    return row


async def _changed() -> None:
    """A registry change: the writer's llama-server picks it up (restarted
    when idle, else after its next idle moment), and the UI refetches."""
    from backend.api.vlm import refresh_writer_adapters

    try:
        await refresh_writer_adapters()
    except Exception:
        logger.exception("Applying the adapter registry to the writer failed")
    ws_manager.broadcast_sync("training", "adapters_changed", {})


@router.get("/adapters")
async def list_adapters() -> List[Dict[str, Any]]:
    rows = await asyncio.to_thread(get_db().list_writer_adapters)
    return [_public(r) for r in rows]


def _rename(adapter_id: int, name: str) -> Dict[str, Any]:
    _live(adapter_id)
    try:
        get_db().rename_writer_adapter(adapter_id, name)
    except ValueError as exc:
        code = str(exc)
        if code not in _NAME_CODES:
            raise
        raise HTTPException(
            status_code=_NAME_CODES[code], detail={"code": code}
        ) from exc
    return _public(_live(adapter_id))


@router.patch("/adapters/{adapter_id}")
async def rename_adapter(adapter_id: int, body: RenameRequest) -> Dict[str, Any]:
    row = await asyncio.to_thread(_rename, adapter_id, body.name)
    ws_manager.broadcast_sync("training", "adapters_changed", {})
    return row


def _deletable(adapter_id: int) -> None:
    row = _live(adapter_id)
    if row["status"] == STATUS_TRAINING:
        raise HTTPException(status_code=409, detail={"code": "adapter_training"})


def _delete(adapter_id: int) -> None:
    _deletable(adapter_id)
    folder = adapter_dir(get_data_dir(), adapter_id)
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except OSError as exc:
        # Keep the row and its file_path so the delete can be retried.
        logger.warning("could not remove adapter %s files: %s", adapter_id, exc)
        raise HTTPException(
            status_code=409, detail={"code": "adapter_files_busy"}
        ) from exc
    get_db().soft_delete_writer_adapter(adapter_id)
    # A paused / failed adapter's open run goes with it (its checkpoints
    # could never be resumed into a deleted adapter).
    from backend.services.training_manager import discard_open_runs

    for run_id in discard_open_runs(get_db(), adapter_id):
        ws_manager.broadcast_sync(
            "training", "run_cancelled", {"run_id": run_id, "adapter_id": adapter_id}
        )


def _op_lock() -> Any:
    """The training manager's operation lock (a delete never interleaves
    with a resume of the same adapter's run); a no-op without a manager."""
    import contextlib

    from backend.services.training_manager import get_training_manager

    manager = get_training_manager()
    return manager.op_lock if manager is not None else contextlib.nullcontext()


@router.delete("/adapters/{adapter_id}")
async def delete_adapter(adapter_id: int) -> Dict[str, str]:
    async with _op_lock():
        await _delete_locked(adapter_id)
    await _changed()
    return {"status": "deleted"}


async def _delete_locked(adapter_id: int) -> None:
    from backend.api.vlm import withdraw_writer_adapter

    await asyncio.to_thread(_deletable, adapter_id)
    # Stop serving it first: the writer restarts without it when idle, so
    # its GGUF is no longer mapped (Windows cannot delete a mapped file).
    if not await withdraw_writer_adapter(adapter_id):
        # A running (or loading) writer still maps its GGUF; the deferred
        # restart drops it at the writer's next idle moment -- retry then.
        # Never left to the file removal failing: POSIX would succeed.
        raise HTTPException(status_code=409, detail={"code": "adapter_files_busy"})
    await asyncio.to_thread(_delete, adapter_id)


# ---- hindsight briefs (training data) ----------------------------------------


@router.post("/briefs/run", status_code=202)
async def run_briefs() -> Dict[str, Any]:
    """Write a hindsight brief for every favorited generated image that has
    none yet (resumable). 202; progress arrives on WS ``training``."""
    from backend.services.hindsight_briefs import BriefsJobError, get_briefs_job

    job = get_briefs_job()
    try:
        job.start()
    except BriefsJobError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from exc
    return {"status": "started"}


@router.get("/briefs/status")
async def briefs_status() -> Dict[str, Any]:
    from backend.services.hindsight_briefs import get_briefs_job

    return get_briefs_job().status()


@router.post("/briefs/cancel")
async def cancel_briefs() -> Dict[str, Any]:
    from backend.services.hindsight_briefs import get_briefs_job

    return {"status": "cancelling" if get_briefs_job().cancel() else "idle"}


# ---- writer LoRA training ------------------------------------------------------


class TrainingStartRequest(BaseModel):
    scope: str = "all"
    effort: str = "standard"
    overrides: Optional[Dict[str, Any]] = None
    start_from: Union[str, int] = "fresh"
    render_scorecard: bool = False
    # VRAM-cap rehearsal (GiB): None = models.writer_train_vram_cap_gb,
    # 0 = off, else 8..1024. Validated by the manager (422 invalid_vram_cap).
    vram_cap_gb: Optional[Any] = None


class TrainingRunRequest(BaseModel):
    run_id: Optional[int] = None


def _manager() -> Any:
    from backend.services.training_manager import get_training_manager

    manager = get_training_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail={"code": "training_unavailable"})
    return manager


async def _training_call(coro: Any) -> Any:
    from backend.services.training_manager import TrainingError

    try:
        return await coro
    except TrainingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc


def _gate_payload(gate: Any) -> Dict[str, Any]:
    return {
        "available": gate.available,
        "reason": gate.reason,
        "reasons": list(gate.reasons),
    }


@router.get("/training")
async def training_status(
    scope: str = "all", recheck: bool = False, dataset: bool = True
) -> Dict[str, Any]:
    """The training window's state: the active run (if any) and resumable
    runs, the dataset counts + readiness for ``scope`` (cached ~30 s), the
    name the next run of ``scope`` would get (``next_name``), the
    ``writer_training`` gate (each missing piece; ``recheck=1`` re-runs its
    subprocess probes, e.g. after installing the training deps) and every
    effort preset resolved, with the Advanced clamps.

    ``dataset=0`` is the light status (the always-on header chip and GPU
    lock): no dataset build, no ``dataset`` / ``next_name``, and ``scope``
    is ignored."""
    from backend.services.training_manager import OVERRIDE_CLAMPS, resolved_presets

    manager = _manager()
    extra: Dict[str, Any] = {}
    if dataset:
        extra["dataset"] = await _training_call(manager.dataset_summary(scope))
        extra["next_name"] = await _training_call(
            asyncio.to_thread(manager.next_adapter_name, scope)
        )
    # Never waits for the dependency probe: it runs in the background and
    # the gate says "checking" until it has finished.
    gate, checking = await manager.gate_status(recheck)
    return {
        **manager.status(),
        "runs": await asyncio.to_thread(manager.resumable_runs),
        **extra,
        "gate": {**_gate_payload(gate), "checking": checking},
        "presets": resolved_presets(),
        "clamps": {k: list(v) for k, v in OVERRIDE_CLAMPS.items()},
        "vram_cap": manager.vram_cap_info(),
    }


@router.post("/training/start")
async def start_training(body: TrainingStartRequest) -> Dict[str, Any]:
    manager = _manager()
    run_id = await _training_call(
        manager.start(
            scope=body.scope,
            effort=body.effort,
            overrides=body.overrides,
            start_from=body.start_from,
            render_scorecard=body.render_scorecard,
            vram_cap_gb=body.vram_cap_gb,
        )
    )
    return {"run_id": run_id, **manager.status()}


@router.get("/training/profiles")
async def training_profiles() -> Dict[str, Any]:
    """The training profiles (VRAM, time, throughput, data shape; numbers
    only): ``{schema, max_entries, profiles (newest first, no timeline),
    current (the running or last attempt, with its timeline)}``. Read-only:
    no GPU lock."""
    result: Dict[str, Any] = await asyncio.to_thread(_manager().profiles)
    return result


@router.post("/training/pause")
async def pause_training() -> Dict[str, Any]:
    result: Dict[str, Any] = await _training_call(_manager().pause())
    return result


@router.post("/training/resume")
async def resume_training(body: TrainingRunRequest) -> Dict[str, Any]:
    manager = _manager()
    if body.run_id is None:
        raise HTTPException(status_code=422, detail={"code": "run_id_required"})
    run_id = await _training_call(manager.resume(body.run_id))
    return {"run_id": run_id, **manager.status()}


@router.post("/training/cancel")
async def cancel_training(body: Optional[TrainingRunRequest] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = await _training_call(
        _manager().cancel(body.run_id if body is not None else None)
    )
    return result


@router.get("/dataset/preview")
async def dataset_preview(scope: str = "all") -> Dict[str, Any]:
    """Up to 10 examples of ``scope`` balanced across preference / edit /
    hindsight (``examples[].kind``), for the owner's training window only
    (never logged)."""
    result: Dict[str, Any] = await _training_call(_manager().dataset_preview(scope))
    return result


# ---- scorecard (base vs adapter, judged blind) ---------------------------------


class ScorecardRequest(BaseModel):
    # A T2I model profile id (default krea2) and its workflow preset (default:
    # the config's t2i.model_workflows entry for it).
    target_model: Optional[str] = None
    preset_id: Optional[int] = None
    # True: a new scorecard even when the latest one still lacks pairs.
    fresh: bool = False


def _training_broadcast(event: str, data: Dict[str, Any]) -> None:
    ws_manager.broadcast_sync("training", event, data)


def _scorecard_service() -> Any:
    from backend.services.scorecard import get_scorecard_service

    service = get_scorecard_service()
    if service is None:
        raise HTTPException(status_code=503, detail={"code": "scorecard_unavailable"})
    return service


@router.post("/adapters/{adapter_id}/scorecard", status_code=202, dependencies=GPU_FREE)
async def render_scorecard(
    adapter_id: int, body: Optional[ScorecardRequest] = None
) -> Dict[str, Any]:
    """Render (or resume) the adapter's scorecard in the background: every
    brief with the base writer and with the adapter, same seed. 202;
    progress arrives on WS ``training`` (``scorecard_*``)."""
    from backend.services.scorecard import ScorecardError

    req = body or ScorecardRequest()
    try:
        result: Dict[str, Any] = await _scorecard_service().start(
            adapter_id,
            target_model=req.target_model,
            preset_id=req.preset_id,
            fresh=req.fresh,
        )
    except ScorecardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from exc
    return result


@router.post("/scorecard/cancel")
async def cancel_scorecard() -> Dict[str, str]:
    cancelled = await _scorecard_service().cancel()
    return {"status": "cancelling" if cancelled else "idle"}


@router.get("/adapters/{adapter_id}/scorecard")
async def adapter_scorecard_results(adapter_id: int) -> Dict[str, Any]:
    """``{adapter_id, n_pairs, n_judged, fav_rate_base, fav_rate_adapter,
    mean_score_base, mean_score_adapter, ready_pairs, latest, running}``.
    Also promotes an untested adapter whose judged pairs suffice."""
    from backend.services.scorecard import (
        adapter_scorecard,
        evaluate_adapter,
        running_scorecard,
    )

    await asyncio.to_thread(_live, adapter_id)
    await evaluate_adapter(get_db(), adapter_id, _training_broadcast)
    result: Dict[str, Any] = await asyncio.to_thread(
        adapter_scorecard, get_db(), adapter_id, running_scorecard()
    )
    return result


@router.get("/scorecards")
async def scorecards() -> Dict[str, Any]:
    """Every T2I adapter's scorecard result, the base writer's pooled rate,
    the brief count and the running scorecard (the training window)."""
    from backend.services.scorecard import (
        READY_PAIRS,
        evaluate_adapter,
        running_scorecard,
        scorecard_summary,
    )

    result: Dict[str, Any] = await asyncio.to_thread(
        scorecard_summary, get_db(), running=running_scorecard()
    )
    due = [
        r["adapter_id"]
        for r in result["adapters"]
        if r.get("status") == "untested" and r["n_judged"] >= READY_PAIRS
    ]
    for adapter_id in due:
        await evaluate_adapter(get_db(), adapter_id, _training_broadcast)
    if due:
        result = await asyncio.to_thread(
            scorecard_summary, get_db(), running=running_scorecard()
        )
    return result
