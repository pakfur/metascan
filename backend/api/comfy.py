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
from backend.services.comfy_service import ComfyService
from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyError

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


@router.get("/presets")
async def list_presets() -> List[Dict[str, Any]]:
    return await _service().list_presets()


@router.post("/presets")
async def create_preset(body: PresetRequest) -> Dict[str, int]:
    client = _require_client()
    try:
        preset_id = await client.register_preset(body.name, body.kind, body.workflow)
    except BindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": int(preset_id)}


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int) -> Dict[str, str]:
    if not await _service().delete_preset(preset_id):
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
