"""ComfyUI-driven upscaling: runs, their candidates, accept, revert, and moving
the Original folder. Rules: ``.claude/rules/upscale.md``."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config import get_upscale_config, load_app_config, save_app_config
from backend.dependencies import get_db, get_generation_process
from backend.services import upscale_folders
from backend.services.gpu_lock import GPU_FREE, gpu_busy_http_error
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import GpuBusyError, PresetNotFoundError
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.core.upscale_defaults import model_family
from metascan.core.upscale_refine_prompts import default_store
from metascan.core.upscale_runner import (
    MAX_STEPS,
    UpscaleNotFoundError,
    UpscaleRequestError,
    UpscaleRunRequest,
)
from metascan.core.upscale_store import UpscaleError
from metascan.utils.path_utils import to_native_path, to_posix_path

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


def prompt_model_for(model: Optional[str]) -> Optional[str]:
    """The T2I model profile (``t2i_models.MODEL_PROFILES``) whose prompt
    style a graph loading ``model`` reads: its model folder's name, when a
    profile has that id (``moss\\dirtyMoss_bf16.safetensors`` -> ``moss``).
    None when there is no such profile."""
    family = model_family(model)
    return family if family in MODEL_PROFILES else None


def _workflow(row: Dict[str, Any], db: Any) -> Dict[str, Any]:
    """What the dialog offers for a preset, read from its stored bindings.
    ``model`` is the model the graph loads (MS_MODEL), so the dialog can tell
    whether an image's own model and LoRAs belong in it. ``prompt_model`` is
    the T2I profile a prompt for it is written in (None: the dialog offers no
    prompt from the image)."""
    bindings = json.loads(row["bindings"])
    model: Optional[str] = None
    steps: Optional[int] = None
    graph: Dict[str, Any] = {}
    if bindings.get("model") or bindings.get("steps"):
        full = db.get_workflow_preset(row["id"])
        try:
            graph = json.loads(full["workflow_json"]) if full else {}
        except (TypeError, ValueError):
            graph = {}

    def baked(node_key: str, widget: Optional[str]) -> Any:
        try:
            node = graph.get(str(bindings[node_key])) or {}
            return (node.get("inputs") or {}).get(widget)
        except (AttributeError, KeyError, TypeError):
            return None

    if bindings.get("model"):
        value = baked("model", bindings.get("model_widget"))
        model = value if isinstance(value, str) else None
    if bindings.get("steps"):
        value = baked("steps", "steps")
        steps = (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )
    prompt_model = prompt_model_for(model) if bindings.get("positive") else None
    return {
        "id": row["id"],
        "name": row["name"],
        "has_seed": bool(bindings.get("seed")),
        "has_prompt": bool(bindings.get("positive")),
        "has_scale": bool(bindings.get("scale")),
        "has_negative": bool(bindings.get("negative")),
        "has_loras": bool(bindings.get("lora_stack")),
        "has_denoise": bool(bindings.get("denoise")),
        "has_model": bool(bindings.get("model")),
        "has_steps": bool(bindings.get("steps")),
        # The step count the graph has baked in: the dialog starts on it.
        "steps": steps,
        "has_max_size": bool(bindings.get("max_size")),
        "model": model,
        "prompt_model": prompt_model,
        "prompt_model_label": (
            MODEL_PROFILES[prompt_model].label if prompt_model else None
        ),
    }


def _config_payload() -> Dict[str, Any]:
    config = load_app_config()
    db = get_db()
    presets = db.list_workflow_presets()
    return {
        **get_upscale_config(config),
        "resolved_original_root": str(upscale_folders.original_root(config)),
        "image_workflows": [
            _workflow(p, db) for p in presets if p["kind"] == "upscale_image"
        ],
        "video_workflows": [
            _workflow(p, db) for p in presets if p["kind"] == "upscale_video"
        ],
        # The detail prompts a refine workflow starts on at a low denoise
        # (data/upscale_refine_prompts.yml, else built in).
        "refine_prompts": [p.to_dict() for p in default_store().prompts()],
    }


@router.get("/config")
def get_config() -> Dict[str, Any]:
    return _config_payload()


@router.get("/state")
def get_state(path: str = Query(...)) -> Dict[str, Any]:
    state: Dict[str, Any] = _require_service().state(path)
    return state


class LoraEntry(BaseModel):
    name: str = Field(min_length=1)
    strength: float = Field(ge=-10.0, le=10.0)


class RunRequest(BaseModel):
    source_path: str
    preset_id: int
    count: int = 1
    seed: Optional[int] = None
    seed_policy: Literal["fixed", "increment", "decrement", "random"] = "random"
    prompt: Optional[str] = None
    scale: Optional[float] = None
    negative: Optional[str] = None
    # An MS_LORA_STACK node gets exactly this list; none leaves it empty.
    loras: Optional[List[LoraEntry]] = None
    denoise: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    model: Optional[str] = None
    steps: Optional[int] = Field(default=None, ge=1, le=MAX_STEPS)
    # Longest output edge in pixels; 0 = no limit.
    max_size: Optional[int] = Field(default=None, ge=0, le=16384)


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
        negative=body.negative,
        loras=(
            None
            if body.loras is None
            else [{"name": e.name, "strength": e.strength} for e in body.loras]
        ),
        denoise=body.denoise,
        model=body.model,
        steps=body.steps,
        max_size=body.max_size,
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


class PromptFromImageRequest(BaseModel):
    source_path: str
    # A T2I model profile id (t2i_models.MODEL_PROFILES), the workflow's
    # prompt_model.
    model: str


@router.post("/prompt-from-image", dependencies=GPU_FREE)
async def prompt_from_image(body: PromptFromImageRequest) -> Dict[str, Any]:
    """A prompt for ``model`` written from the image itself, for an image
    with no prompt or one written for another model family (SDXL or Pony
    tags read poorly in Moss): the prompt writer captions the image, then
    the caption goes through the T2I caption-to-prompt flow like a typed
    one (``T2iRunner.generate_prompt``: the model's guideline, its stock
    negative, the content mode). The caption is made from the original of
    an upscaled item, as every run is. Writes nothing; never logs the
    caption or the prompt."""
    from backend.api import t2i as t2i_api

    if body.model not in MODEL_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown model {body.model!r}")
    runner = t2i_api.get_t2i_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="the prompt writer is not running")
    posix = to_posix_path(body.source_path)
    record = await asyncio.to_thread(get_db().upscale_record, posix)
    image = Path(to_native_path(record["original_path"] if record else posix))
    try:
        caption, caption_model = await runner.caption_image(image)
        result = await runner.generate_prompt(
            caption=caption,
            seed=0,  # picks wildcards only, and a caption made here has none
            model=body.model,
            directions=False,
            variants=1,
            writer="base",
        )
    except Exception as exc:
        mapped = t2i_api._http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    draft = result.drafts[0] if result.drafts else None
    return {
        "caption": caption,
        "caption_model": caption_model,
        "prompt": result.prompt,
        "negative": result.negative,
        "warnings": list(result.warnings),
        "writer_id": draft.writer_id if draft else None,
        "writer_model": draft.writer_model if draft else None,
    }


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
