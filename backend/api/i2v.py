"""REST endpoints for the image-to-video (i2v) flow.

The I2vRunner singleton is installed by the FastAPI lifespan via
set_i2v_runner(). Prompt expansion is review-only (writes nothing);
generation resolves the preset from the config's fast/quality slot and
lints the submitted prompt text (advisory -- warnings never block).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import get_i2v_config, load_app_config
from backend.dependencies import get_db
from backend.services.i2v_service import I2vService
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import ComfyError, PresetNotFoundError
from metascan.core.i2v_compiler import I2vError, lint_i2v_prompt
from metascan.core.i2v_runner import I2vRequestError, I2vUnavailableError
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/i2v", tags=["i2v"])

_runner: Optional[Any] = None


def set_i2v_runner(runner: Any) -> None:
    """Install the I2vRunner singleton. Pass None to clear (tests)."""
    global _runner
    _runner = runner


def get_i2v_runner() -> Any:
    return _runner


def _require_runner() -> Any:
    if _runner is None:
        raise HTTPException(status_code=503, detail="i2v runner is not running")
    return _runner


def _service() -> I2vService:
    return I2vService(get_db())


class PromptRequest(BaseModel):
    source_path: str
    idea: str = ""
    duration_s: float


class GenerateRequest(BaseModel):
    source_path: str
    prompt: str
    duration_s: float
    quality: str
    seed: int
    width: int = 0
    height: int = 0
    loras: List[Dict[str, Any]] = Field(default_factory=list)
    # Dialog metadata persisted onto the i2v_videos row at ingest.
    idea: Optional[str] = None


@router.post("/prompt")
async def generate_prompt(body: PromptRequest) -> Dict[str, Any]:
    runner = _require_runner()
    if body.duration_s <= 0:
        raise HTTPException(status_code=400, detail="duration_s must be positive")
    try:
        prompt, warnings = await runner.generate_prompt(
            body.source_path, body.idea, body.duration_s
        )
    except I2vUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VlmSelectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except I2vRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (I2vError, VlmError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"prompt": prompt, "warnings": warnings}


@router.post("/generate")
async def generate(body: GenerateRequest) -> Dict[str, Any]:
    runner = _require_runner()
    if body.duration_s <= 0:
        raise HTTPException(status_code=400, detail="duration_s must be positive")
    if body.quality not in ("fast", "quality"):
        raise HTTPException(
            status_code=400, detail="quality must be 'fast' or 'quality'"
        )
    cfg = get_i2v_config(load_app_config())
    preset_id = (
        cfg["fast_preset_id"] if body.quality == "fast" else cfg["quality_preset_id"]
    )
    if not preset_id:
        raise HTTPException(
            status_code=400,
            detail=f"No {body.quality} preset is configured -- set one in "
            "Configuration → Image to Video.",
        )
    try:
        job_id = await runner.generate(
            source_path=body.source_path,
            prompt=body.prompt,
            duration_s=body.duration_s,
            quality=body.quality,
            seed=body.seed,
            width=body.width,
            height=body.height,
            loras=body.loras,
            preset_id=preset_id,
            idea=body.idea,
        )
    except I2vRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (BindingError, PresetNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComfyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "warnings": lint_i2v_prompt(body.prompt, body.duration_s),
    }


@router.get("/videos")
async def list_videos(source_path: str) -> List[Dict[str, Any]]:
    return await _service().list_videos(source_path)


@router.delete("/videos/{video_id}")
async def delete_video(video_id: int) -> Dict[str, str]:
    deleted = await _service().delete_video(video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No i2v video {video_id}")
    return {"status": "deleted"}


@router.get("/config")
async def i2v_config() -> Dict[str, Any]:
    return get_i2v_config(load_app_config())
