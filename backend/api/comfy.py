"""REST endpoints for the ComfyUI driver.

The ComfyClient singleton is installed by the FastAPI lifespan via
``set_comfy_client(...)``. Endpoints that need the network fail fast with
503 when it is missing; read-only endpoints degrade gracefully so the UI
can still show what is configured.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies import get_db
from backend.services.comfy_service import ComfyService, PresetInUseError
from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyError, PresetNotFoundError
from metascan.core.workflow_validation import (
    VIDEO_MODES,
    VIDEO_TARGETS,
    apply_fixes,
    validate_workflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comfy", tags=["comfy"])

_comfy_client: Optional[Any] = None


def set_comfy_client(client: Any) -> None:
    """Install the ComfyClient singleton. Pass None to clear (tests)."""
    global _comfy_client
    _comfy_client = client


def get_comfy_client() -> Any:
    return _comfy_client


def _require_client() -> Any:
    if _comfy_client is None:
        raise HTTPException(
            status_code=503,
            detail="ComfyUI driver is not running. Check the 'comfy' section "
            "of config.json and restart the server.",
        )
    return _comfy_client


def _service() -> ComfyService:
    return ComfyService(get_db())


class PresetRequest(BaseModel):
    name: str
    kind: str = "t2i"
    workflow: Dict[str, Any]
    # Optional association with a video dialect + generation mode (see
    # metascan.core.workflow_validation). Drives target-specific validation
    # here and the mismatch guard in StoryboardRunner.generate_video.
    video_target: Optional[str] = None
    video_mode: Optional[str] = None


class PresetValidateRequest(BaseModel):
    kind: str = "ref2v"
    workflow: Dict[str, Any]
    video_target: Optional[str] = None
    video_mode: Optional[str] = None


def _check_target_mode(video_target: Optional[str], video_mode: Optional[str]) -> None:
    if video_target is not None and video_target not in VIDEO_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"video_target must be one of: {', '.join(VIDEO_TARGETS)}",
        )
    if video_mode is not None and video_mode not in VIDEO_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"video_mode must be one of: {', '.join(VIDEO_MODES)}",
        )


class SubmitRequest(BaseModel):
    preset_id: int
    positive: str
    seed: int
    width: int
    height: int
    batch_size: int = Field(default=1, ge=1)
    negative: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    ref_image: Optional[str] = None
    priority: bool = False


@router.get("/status")
async def status() -> Dict[str, Any]:
    client = _comfy_client
    if client is None:
        return {"base_url": None, "client_id": None, "in_flight": 0}
    return client.snapshot()


@router.get("/loras")
async def list_loras() -> List[str]:
    """Loras installed on the connected ComfyUI server. Empty when no
    client is configured or the server is unreachable -- the frontend
    picker falls back to free text."""
    client = _comfy_client
    if client is None:
        return []
    return await client.list_loras()


@router.get("/presets")
async def list_presets() -> List[Dict[str, Any]]:
    return await _service().list_presets()


@router.post("/presets/validate")
async def validate_preset(body: PresetValidateRequest) -> Dict[str, Any]:
    """Dry-run validation of a workflow (pure -- no ComfyUI connection
    needed). Returns the full findings/fixes report plus, when fixes
    exist, the workflow with them applied so the dialog can offer a
    one-click repair."""
    _check_target_mode(body.video_target, body.video_mode)
    report = validate_workflow(
        body.workflow, body.kind, body.video_target, body.video_mode
    )
    out = report.to_dict()
    if report.fixes:
        out["fixed_workflow"] = apply_fixes(body.workflow, report.fixes)
    return out


@router.post("/presets")
async def create_preset(body: PresetRequest) -> Dict[str, Any]:
    client = _require_client()
    _check_target_mode(body.video_target, body.video_mode)
    report = validate_workflow(
        body.workflow, body.kind, body.video_target, body.video_mode
    )
    if not report.ok:
        # Structured 400 so the dialog can render per-finding rows and
        # offer the computed fixes instead of one opaque message.
        detail: Dict[str, Any] = {"code": "validation_failed", **report.to_dict()}
        if report.fixes:
            detail["fixed_workflow"] = apply_fixes(body.workflow, report.fixes)
        raise HTTPException(status_code=400, detail=detail)
    try:
        preset_id = await client.register_preset(
            body.name,
            body.kind,
            body.workflow,
            body.video_target,
            body.video_mode,
        )
    except BindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": int(preset_id),
        "warnings": [f["message"] for f in report.to_dict()["findings"]],
    }


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int) -> Dict[str, str]:
    try:
        deleted = await _service().delete_preset(preset_id)
    except PresetInUseError as exc:
        # 409, not 500: the request is well-formed but conflicts with
        # existing job history, which we deliberately never cascade away.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No preset {preset_id}")
    return {"status": "deleted"}


@router.post("/submit")
async def submit(body: SubmitRequest) -> Dict[str, int]:
    client = _require_client()
    params = GenerationParams(
        positive=body.positive,
        seed=body.seed,
        width=body.width,
        height=body.height,
        batch_size=body.batch_size,
        negative=body.negative,
        lora_name=body.lora_name,
        lora_strength=body.lora_strength,
        ref_image=body.ref_image,
    )
    try:
        job_id = await client.submit(body.preset_id, params, priority=body.priority)
    except PresetNotFoundError as exc:
        # Must be caught before ComfyError -- it's a subclass. A bad
        # preset id is a client-request problem, not a ComfyUI-down
        # problem, so it gets the same 404 the other preset/job routes
        # use for an unknown id.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComfyError as exc:
        # 503, not 502: spec §9 wants "ComfyUI unreachable" to read as a
        # service-availability problem, and ComfyError's message already
        # carries the configured base_url.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"job_id": int(job_id)}


@router.get("/jobs")
async def list_jobs(
    state: Optional[str] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    states = [state] if state else None
    return await _service().list_jobs(states=states, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int) -> Dict[str, Any]:
    job = await _service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int) -> Dict[str, str]:
    client = _require_client()
    job = await _service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    await client.cancel(job_id)
    return {"status": "cancelled"}
