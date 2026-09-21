"""REST endpoints for the image-to-video (i2v) flow.

The I2vRunner singleton is installed by the FastAPI lifespan via
set_i2v_runner(). Prompt expansion is review-only (writes nothing);
generation resolves the preset from the config's fast/quality slot and
lints the submitted prompt text (advisory -- warnings never block).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config import get_i2v_config, load_app_config
from backend.dependencies import get_db
from backend.services.i2v_service import I2vService
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import ComfyError, PresetNotFoundError
from metascan.core.i2v_compiler import I2vError, lint_i2v_prompt
from metascan.core.i2v_fixes import lint_i2v_report
from metascan.core.i2v_output import (
    I2vOutputError,
    output_prefix_warnings,
    resolve_output_target,
)
from metascan.core.i2v_runner import I2vRequestError, I2vUnavailableError
from metascan.utils.path_utils import to_native_path
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/i2v", tags=["i2v"])

_runner: Optional[Any] = None

# Sanity ceiling for a client-supplied step count; the dialog offers 20-40.
_MAX_STEPS = 200


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


class LintRequest(BaseModel):
    prompt: str
    duration_s: float


class GenerateRequest(BaseModel):
    source_path: str
    prompt: str
    duration_s: float
    quality: str
    seed: int
    # Pixel budget; output dimensions are derived from it and the source
    # image's aspect ratio server-side. Explicit checks rather than
    # Field(gt=0) so a bad value is a 400, not a 422.
    megapixels: float = 0.75
    loras: List[Dict[str, Any]] = Field(default_factory=list)
    # Dialog metadata persisted onto the i2v_videos row at ingest.
    idea: Optional[str] = None
    # Sampler steps. Only the High quality preset is ever driven, and only
    # when its workflow binds MS_STEPS; ignored otherwise.
    steps: Optional[int] = None


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


@router.post("/lint")
async def lint_prompt(body: LintRequest) -> Dict[str, Any]:
    """Advisory lint of the prompt box's current text, plus the rewrites
    that need no model call (natural camera phrasing -> the guide's motion
    vocabulary; quoted speech -> ``(S1) says: <d>[Language] ...</d>``).

    ``fixes`` is the fixable subset with before/after text; ``fixed_prompt``
    is the prompt with all of them applied, or null when there is nothing
    to fix. Never rewrites on its own -- the dialog's "Apply fixes" button
    opts in. Pure text analysis: needs neither the runner nor the VLM.
    """
    if body.duration_s <= 0:
        raise HTTPException(status_code=400, detail="duration_s must be positive")
    return lint_i2v_report(body.prompt, body.duration_s)


@router.post("/generate")
async def generate(body: GenerateRequest) -> Dict[str, Any]:
    runner = _require_runner()
    if body.duration_s <= 0:
        raise HTTPException(status_code=400, detail="duration_s must be positive")
    if body.quality not in ("fast", "quality"):
        raise HTTPException(
            status_code=400, detail="quality must be 'fast' or 'quality'"
        )
    if body.megapixels <= 0:
        raise HTTPException(status_code=400, detail="megapixels must be positive")
    if body.steps is not None and not 1 <= body.steps <= _MAX_STEPS:
        raise HTTPException(
            status_code=400, detail=f"steps must be between 1 and {_MAX_STEPS}"
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
            megapixels=body.megapixels,
            loras=body.loras,
            preset_id=preset_id,
            idea=body.idea,
            steps=body.steps,
            output_root=cfg["output_root"] or None,
            output_prefix=cfg["output_prefix"],
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


@router.get("/output-preview")
def output_preview(root: str = "", prefix: str = "") -> Dict[str, Any]:
    """Where a clip generated right now would land, for the Video config
    tab's live preview. Always 200: a problem with the (unsaved, mid-edit)
    values is data for the form -- ``error`` -- not a failed request.
    ``path`` is null both on error and for a blank root (the default
    layout under comfy.output_root, which depends on the source image).
    Sync on purpose (threadpool): it stats a possibly slow mount."""
    warnings = output_prefix_warnings(prefix)
    if not root.strip():
        return {"path": None, "error": None, "warnings": warnings}
    native = Path(to_native_path(root.strip()))
    try:
        target = resolve_output_target(
            str(native), prefix, datetime.now(), int(time.time())
        )
    except I2vOutputError as exc:
        return {"path": None, "error": str(exc), "warnings": warnings}
    if not native.is_dir():
        return {
            "path": None,
            "error": f"Directory does not exist: {native}",
            "warnings": warnings,
        }
    return {
        "path": str(target.directory / f"{target.stem}.mp4"),
        "error": None,
        "warnings": warnings,
    }


@router.get("/config")
async def i2v_config() -> Dict[str, Any]:
    cfg = get_i2v_config(load_app_config())
    # Whether the Steps selector can actually drive the configured High
    # quality preset -- the dialog disables it (with a hint) when not.
    cfg["quality_steps_supported"] = await _service().preset_supports_steps(
        cfg["quality_preset_id"]
    )
    return cfg
