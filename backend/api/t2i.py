"""REST endpoints for the text-to-image (t2i) flow.

The T2iRunner singleton is installed by the FastAPI lifespan via
set_t2i_runner(). Prompt writing and caption resolution are review-only
(they write nothing); batches run on the server after POST /batches and
report on the ``t2i`` WebSocket channel. The image, path and output-preview
routes read only the DB (or nothing), so they work while the runner is down.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config import get_comfy_config, get_t2i_config, load_app_config
from backend.dependencies import get_db
from backend.services.t2i_service import T2iService
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import ComfyError, PresetNotFoundError
from metascan.core.i2v_output import (
    I2vOutputError,
    output_prefix_warnings,
    resolve_output_target,
)
from metascan.core.t2i_captions import CaptionFilterError, CaptionStore
from metascan.core.t2i_characters import Library
from metascan.core.t2i_form import (
    ASPECT_RATIOS,
    SEED_MAX,
    SEED_POLICIES,
    T2iFormError,
    validate_form_patch,
)
from metascan.core.t2i_models import MODEL_PROFILES, effective_identity
from metascan.core.t2i_runner import (
    BatchRequest,
    T2iConflictError,
    T2iNotFoundError,
    T2iRequestError,
    T2iUnavailableError,
    default_output_root,
)
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/t2i", tags=["t2i"])

_runner: Optional[Any] = None


def set_t2i_runner(runner: Any) -> None:
    """Install the T2iRunner singleton. Pass None to clear (tests)."""
    global _runner
    _runner = runner


def get_t2i_runner() -> Any:
    return _runner


def _require_runner() -> Any:
    if _runner is None:
        raise HTTPException(status_code=503, detail="t2i runner is not running")
    return _runner


def _service() -> T2iService:
    return T2iService(get_db())


# What the runner and the stores raise, and the status each one means. The
# order matters: the t2i errors and PresetNotFoundError are RuntimeErrors,
# which are otherwise a 502.
_BAD_REQUEST = (
    T2iRequestError,
    BindingError,
    PresetNotFoundError,
    T2iFormError,
    CaptionFilterError,
)


def _http_error(exc: Exception) -> Optional[HTTPException]:
    """The HTTP answer for a runner error, or None for an unexpected one
    (which stays a 500 with a traceback in the log)."""
    if isinstance(exc, _BAD_REQUEST):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, T2iNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, T2iConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (T2iUnavailableError, VlmSelectError)):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (ComfyError, VlmError, TimeoutError, RuntimeError)):
        return HTTPException(status_code=502, detail=str(exc))
    return None


class LoraSpec(BaseModel):
    """Typed, unlike I2V's free-form dicts: a missing name is a 422 here
    rather than a KeyError deep in the runner."""

    name: str
    strength: float


class CaptionFilterBody(BaseModel):
    filter: Optional[Dict[str, Any]] = None


class CaptionRequest(BaseModel):
    caption: str
    seed: int
    model: str
    # Caption directions for /prompt; None means the config's default.
    directions: Optional[bool] = None


class BatchBody(BaseModel):
    mode: str
    model: str
    preset_id: int
    megapixels: float
    seed: int
    seed_policy: str
    batch_size: int = 1
    count_per_batch: int = 1
    loras: List[LoraSpec] = Field(default_factory=list)
    # Manual fields
    caption: Optional[str] = None
    prompt: Optional[str] = None
    negative: Optional[str] = None
    aspect_ratio: Optional[str] = None
    # Random field
    filter: Optional[Dict[str, Any]] = None
    # Caption directions; None means the config's default.
    directions: Optional[bool] = None


def _list_slots(library: Library) -> List[str]:
    """Names of the lists that exist, one per slot (``body.female`` and
    ``body.male`` are both ``body``): the configured slots in their order,
    then any other list by name."""
    present = {key.split(".")[0] for key, values in library.lists.items() if values}
    ordered = [slot for slot in library.config.slots if slot in present]
    return ordered + sorted(present - set(ordered))


def _csv_state(captions: CaptionStore) -> Dict[str, Any]:
    available = captions.available()
    return {
        "available": available,
        "total": captions.total(),
        "error": None if available else captions.error(),
    }


@router.get("/config")
async def t2i_config() -> Dict[str, Any]:
    runner = _require_runner()
    cfg = get_t2i_config(load_app_config())
    library_and_warnings = await asyncio.to_thread(runner.library.get)
    library, warnings = library_and_warnings
    cfg["models"] = [
        {
            "id": profile.id,
            "label": profile.label,
            "has_negative": profile.has_negative,
            "identity": effective_identity(profile, cfg["identity"]),
        }
        for profile in MODEL_PROFILES.values()
    ]
    cfg["aspect_ratios"] = list(ASPECT_RATIOS)
    cfg["seed_policies"] = list(SEED_POLICIES)
    cfg["seed_max"] = SEED_MAX
    # The first call may build the caption index (about a second on the real
    # file), so it runs off the event loop.
    cfg["csv"] = await asyncio.to_thread(_csv_state, runner.captions)
    cfg["wildcards"] = {"slots": _list_slots(library), "warnings": warnings}
    return cfg


@router.get("/captions/meta")
async def caption_meta() -> Dict[str, Any]:
    runner = _require_runner()
    meta: Dict[str, Any] = await asyncio.to_thread(runner.captions.meta)
    return meta


@router.post("/captions/count")
async def count_captions(body: CaptionFilterBody) -> Dict[str, int]:
    runner = _require_runner()
    try:
        count = await asyncio.to_thread(runner.captions.count, body.filter)
        total = await asyncio.to_thread(runner.captions.total)
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return {"count": count, "total": total}


@router.post("/captions/random")
async def random_caption(body: CaptionFilterBody) -> Dict[str, Any]:
    """One caption row chosen at random among those the filter keeps: the
    dialog's dice button, to preview what a Random run would draw from."""
    runner = _require_runner()
    captions: CaptionStore = runner.captions
    try:
        if not await asyncio.to_thread(captions.available):
            reason = await asyncio.to_thread(captions.error)
            raise HTTPException(
                status_code=503,
                detail=f"caption CSV unavailable: {reason or 'unknown error'}",
            )
        # count() raises for a bad filter (400); a filter that keeps nothing
        # is a 404, not a 400: it is a valid question with an empty answer.
        if await asyncio.to_thread(captions.count, body.filter) == 0:
            raise HTTPException(status_code=404, detail="no captions match the filter")
        picker = await asyncio.to_thread(captions.picker, body.filter, random.Random())
        row = await asyncio.to_thread(picker.next)
    except HTTPException:
        raise
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return row.to_dict()


@router.post("/captions/resolve")
async def resolve_caption(body: CaptionRequest) -> Dict[str, Any]:
    runner = _require_runner()
    try:
        resolved = await runner.resolve(
            caption=body.caption, seed=body.seed, model=body.model
        )
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return {
        "resolved_caption": resolved.text,
        "characters": resolved.characters,
        "warnings": resolved.warnings,
    }


@router.post("/prompt")
async def generate_prompt(body: CaptionRequest) -> Dict[str, Any]:
    runner = _require_runner()
    try:
        result = await runner.generate_prompt(
            caption=body.caption,
            seed=body.seed,
            model=body.model,
            directions=body.directions,
        )
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return {
        "prompt": result.prompt,
        "negative": result.negative,
        "resolved_caption": result.resolved_caption,
        "warnings": result.warnings,
        "direction": result.direction,
        "direction_parts": result.direction_parts,
    }


@router.post("/batches")
async def start_batch(body: BatchBody) -> Dict[str, Any]:
    runner = _require_runner()
    request = BatchRequest(
        mode=body.mode,
        model=body.model,
        preset_id=body.preset_id,
        megapixels=body.megapixels,
        seed=body.seed,
        seed_policy=body.seed_policy,
        batch_size=body.batch_size,
        count_per_batch=body.count_per_batch,
        loras=[lora.model_dump() for lora in body.loras],
        caption=body.caption,
        prompt=body.prompt,
        negative=body.negative,
        aspect_ratio=body.aspect_ratio,
        filter=body.filter,
        directions=body.directions,
    )
    try:
        started = await runner.start_batch(request)
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return {
        "batch_id": started.batch_id,
        "total_images": started.total_images,
        "warnings": started.warnings,
    }


@router.get("/batches")
async def list_batches() -> List[Dict[str, Any]]:
    runner = _require_runner()
    rows: List[Dict[str, Any]] = runner.active_batches()
    return rows


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str) -> Dict[str, str]:
    """Cancel a batch. Idempotent: one that is already over answers the same
    ``cancelled``; only an id the runner has never seen is a 404."""
    runner = _require_runner()
    try:
        await runner.cancel_batch(batch_id)
    except Exception as exc:
        mapped = _http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return {"status": "cancelled"}


@router.get("/images")
async def list_images(
    limit: int = Query(60, ge=1, le=200), before_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    return await _service().list_images(limit, before_id)


@router.patch("/images/{image_id}")
async def update_image_form_state(
    image_id: int, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Autosave the dialog's form into one image's editable ``form_state``.

    Partial: send only what changed, or the whole form. The image's
    as-rendered columns (``prompt_used``, ``seed``, ``model``, ...) are
    facts about the picture and not reachable from here. Needs neither the
    runner nor ComfyUI, so it works while either is down.
    """
    try:
        patch = validate_form_patch(body)
    except T2iFormError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await _service().update_form_state(image_id, patch)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No t2i image {image_id}")
    return row


@router.delete("/images/{image_id}")
async def delete_image(image_id: int) -> Dict[str, str]:
    if not await _service().delete_image(image_id):
        raise HTTPException(status_code=404, detail=f"No t2i image {image_id}")
    return {"status": "deleted"}


@router.get("/paths")
async def list_paths() -> List[str]:
    """Native paths of every generated image still in the library, for the
    smart-folder "Generated with T2I" rule. One flat list rather than a
    per-image flag on /api/media, so the grid's covering indexes stay put."""
    return await _service().list_paths()


@router.get("/output-preview")
def output_preview(root: str = "", prefix: str = "") -> Dict[str, Any]:
    """Where an image generated right now would land, for the T2I config
    tab's live preview. Always 200: a problem with the (unsaved, mid-edit)
    values is data for the form -- ``error`` -- not a failed request.

    A blank ``root`` previews the default root, ``<comfy.output_root>/t2i``,
    which is created on demand and so is never checked for existence; a
    given one must be an existing directory, as at generation time. Sync on
    purpose (threadpool): it stats a possibly slow mount."""
    warnings = output_prefix_warnings(prefix)
    given = root.strip()
    if given:
        base = Path(to_native_path(given))
    else:
        base = default_output_root(get_comfy_config(load_app_config())["output_root"])
    try:
        target = resolve_output_target(
            str(base), prefix, datetime.now(), int(time.time())
        )
    except I2vOutputError as exc:
        return {"path": None, "error": str(exc), "warnings": warnings}
    if given and not base.is_dir():
        return {
            "path": None,
            "error": f"Directory does not exist: {base}",
            "warnings": warnings,
        }
    return {
        "path": str(target.directory / f"{target.stem}.png"),
        "error": None,
        "warnings": warnings,
    }
