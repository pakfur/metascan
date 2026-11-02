"""ComfyUI-driven upscaling: runs, their candidates, accept, revert, and moving
the Original folder. Rules: ``.claude/rules/upscale.md``."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.config import get_upscale_config, load_app_config, save_app_config
from backend.dependencies import get_db, get_generation_process
from backend.services import upscale_folders
from backend.services.gpu_lock import GPU_FREE, gpu_busy_http_error
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import GpuBusyError, PresetNotFoundError
from metascan.core.upscale_runner import (
    UpscaleNotFoundError,
    UpscaleRequestError,
    UpscaleRunRequest,
)
from metascan.core.upscale_store import UpscaleError
from metascan.utils.path_utils import to_posix_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upscale", tags=["upscale"])

_runner: Any = None
_service: Any = None
_originals: Any = None

_STATUS_FOR_CODE = {
    "not_found": 404,
    "not_upscaled": 404,
    "unsupported_output": 400,
    "file_in_use": 409,
    "library_busy": 409,
    "originals_moving": 409,
    "swap_unfinished": 503,
}


def install(runner: Any, service: Any, originals: Any) -> None:
    """Installed by the lifespan; None clears them (tests)."""
    global _runner, _service, _originals
    _runner, _service, _originals = runner, service, originals


def upscale_active() -> bool:
    """A run is going (writer training refuses the GPU)."""
    return bool(_runner is not None and _runner.active_batches())


def _require_runner() -> Any:
    if _runner is None:
        raise HTTPException(status_code=503, detail="upscaling is not running")
    return _runner


def _require_service() -> Any:
    if _service is None:
        raise HTTPException(status_code=503, detail="upscaling is not running")
    return _service


def _http_error(exc: UpscaleError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_FOR_CODE.get(exc.code, 400),
        detail={"code": exc.code, "message": str(exc)},
    )


def _workflow(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the dialog offers for a preset, read from its stored bindings."""
    bindings = json.loads(row["bindings"])
    return {
        "id": row["id"],
        "name": row["name"],
        "has_seed": bool(bindings.get("seed")),
        "has_prompt": bool(bindings.get("positive")),
        "has_scale": bool(bindings.get("scale")),
    }


def _config_payload() -> Dict[str, Any]:
    config = load_app_config()
    presets = get_db().list_workflow_presets()
    return {
        **get_upscale_config(config),
        "resolved_original_root": str(upscale_folders.original_root(config)),
        "image_workflows": [
            _workflow(p) for p in presets if p["kind"] == "upscale_image"
        ],
        "video_workflows": [
            _workflow(p) for p in presets if p["kind"] == "upscale_video"
        ],
    }


@router.get("/config")
def get_config() -> Dict[str, Any]:
    return _config_payload()


@router.get("/state")
def get_state(path: str = Query(...)) -> Dict[str, Any]:
    state: Dict[str, Any] = _require_service().state(path)
    return state


class RunRequest(BaseModel):
    source_path: str
    preset_id: int
    count: int = 1
    seed: Optional[int] = None
    seed_policy: Literal["fixed", "increment", "decrement", "random"] = "random"
    prompt: Optional[str] = None
    scale: Optional[float] = None


@router.post("/runs", dependencies=GPU_FREE)
async def start_run(body: RunRequest) -> Dict[str, Any]:
    runner = _require_runner()
    config = await asyncio.to_thread(load_app_config)
    cfg = get_upscale_config(config)
    if not 1 <= body.count <= cfg["max_count"]:
        raise HTTPException(
            status_code=400, detail=f"Count must be 1 to {cfg['max_count']}"
        )
    conflict = await asyncio.to_thread(upscale_folders.staging_conflict, config)
    if conflict is not None:
        raise HTTPException(status_code=409, detail=conflict)
    request = UpscaleRunRequest(
        source_path=to_posix_path(body.source_path),
        preset_id=body.preset_id,
        count=body.count,
        seed=body.seed,
        seed_policy=body.seed_policy,
        prompt=body.prompt,
        scale=body.scale,
    )
    try:
        started = await runner.start_run(request, window=cfg["window"])
    except (UpscaleRequestError, BindingError, PresetNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GpuBusyError as exc:
        raise gpu_busy_http_error() from exc
    process = get_generation_process("upscale")
    if process is not None:
        # The owner started a run: that clears a stop; the panel updates now.
        await process.owner_batch_starting()
        process.poke()
    return {
        "batch_id": started.batch_id,
        "total": started.total,
        "next_seed": started.next_seed,
    }


@router.get("/runs")
def list_runs() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = _require_runner().active_batches()
    return rows


@router.post("/runs/{batch_id}/cancel")
async def cancel_run(batch_id: str) -> Dict[str, Any]:
    try:
        cancelled = await _require_runner().cancel_batch(batch_id)
    except UpscaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such run") from exc
    return {"cancelled": cancelled}


@router.delete("/candidates/{candidate_id}")
async def discard_candidate(candidate_id: int) -> Dict[str, Any]:
    try:
        await _require_service().discard(candidate_id)
    except UpscaleError as exc:
        raise _http_error(exc) from exc
    return {"status": "deleted"}


@router.post("/candidates/{candidate_id}/accept")
async def accept_candidate(candidate_id: int) -> Dict[str, Any]:
    try:
        path = await _require_service().accept(candidate_id)
    except UpscaleError as exc:
        raise _http_error(exc) from exc
    return {"file_path": path}


class RevertRequest(BaseModel):
    file_path: str


@router.post("/revert")
async def revert(body: RevertRequest) -> Dict[str, Any]:
    try:
        path = await _require_service().revert(body.file_path)
    except UpscaleError as exc:
        raise _http_error(exc) from exc
    return {"file_path": path}


class OriginalFolderRequest(BaseModel):
    path: str  # "" = the default under the data folder


def _save_original_root(root: str) -> None:
    config = load_app_config()
    config["upscale"] = {**(config.get("upscale") or {}), "original_root": root}
    save_app_config(config)


@router.post("/original-folder", status_code=202)
async def set_original_folder(body: OriginalFolderRequest) -> Dict[str, Any]:
    """Change the Original folder. With originals present the files move first
    (Moving originals panel) and the setting changes when they have arrived."""
    wanted = body.path.strip()
    config = await asyncio.to_thread(load_app_config)
    merged = {
        **config,
        "upscale": {**(config.get("upscale") or {}), "original_root": wanted},
    }
    conflict = await asyncio.to_thread(upscale_folders.original_folder_conflict, merged)
    if conflict is not None:
        raise HTTPException(status_code=400, detail=conflict)
    records = await asyncio.to_thread(get_db().list_upscale_records)
    target = upscale_folders.original_root(merged)
    if not records or target == upscale_folders.original_root(config):
        await asyncio.to_thread(_save_original_root, wanted)
        return {"status": "saved"}
    if _originals is None:
        raise HTTPException(status_code=503, detail="upscaling is not running")
    if _originals.active():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "originals_moving",
                "message": "The Original folder is already being moved.",
            },
        )
    await asyncio.to_thread(Path(target).mkdir, parents=True, exist_ok=True)
    await _originals.begin(str(target))
    return {"status": "moving"}
