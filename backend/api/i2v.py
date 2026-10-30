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
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import AfterValidator, BaseModel, Field

from backend.config import get_i2v_config, load_app_config
from backend.api.categories import checked_category_ids
from backend.api.errors import trash_failed_error
from backend.dependencies import get_db
from backend.services.i2v_service import I2vService
from backend.services.gpu_lock import GPU_FREE, gpu_busy_http_error
from metascan.core.comfy_bindings import BindingError
from metascan.core.comfy_client import ComfyError, GpuBusyError, PresetNotFoundError
from metascan.core.i2v_compiler import I2vError, lint_i2v_prompt
from metascan.core.i2v_fixes import lint_i2v_report
from metascan.core.i2v_form import FormStateError, validate_form_patch
from metascan.core.i2v_output import (
    I2vOutputError,
    output_prefix_warnings,
    resolve_output_target,
)
from metascan.core.i2v_runner import (
    I2vBatchRequest,
    I2vNotFoundError,
    I2vRequestError,
    I2vUnavailableError,
)
from metascan.core.i2v_templates import (
    I2vTemplateError,
    I2vTemplateNotFound,
    get_i2v_template,
)
from metascan.utils.path_utils import to_native_path
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError
from metascan.core.writer_adapters import (
    WriterAdapterUnavailable,
    parse_adapter_writer_id,
)

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


def _adapter_409(exc: WriterAdapterUnavailable) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": exc.code,
            "message": str(exc),
            "writer_id": exc.writer_id,
        },
    )


def _writer_choice(value: str) -> str:
    if value == "base" or parse_adapter_writer_id(value) is not None:
        return value
    raise ValueError("writer must be 'base' or 'adapter:<id>'")


class PromptRequest(BaseModel):
    source_path: str
    idea: str = ""
    duration_s: float
    # Découpage cadence; null = single take (the pre-template behaviour).
    template_id: Optional[str] = None
    # The unguided base writer, or an I2V prompt-writer adapter
    # (``adapter:<id>``); taste examples are T2I-only.
    writer: Annotated[str, AfterValidator(_writer_choice)] = "base"


class LintRequest(BaseModel):
    prompt: str
    duration_s: float
    template_id: Optional[str] = None


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
    # Recorded into the clip's form_state only; the prompt text already
    # embodies the cadence, so the render never needs the template.
    template_id: Optional[str] = None
    # Categories the clip is tagged with at ingest; unknown ids are a 404
    # before anything is uploaded or queued.
    category_ids: List[int] = Field(default_factory=list)
    # Lineage: the writer that drafted `prompt` and its draft, as returned by
    # /prompt. Neither (a prompt typed from scratch) is recorded as `manual`.
    writer_id: Optional[str] = None
    draft_prompt: Optional[str] = None
    # The VLM model that wrote the draft (/prompt's ``writer_model``).
    writer_model: Optional[str] = None


class BatchRequestBody(GenerateRequest):
    """A run: the form of one render plus how many to make. An empty
    ``prompt`` has the run write batch 1 too."""

    prompt: str = ""
    seed_policy: Literal["fixed", "increment", "decrement", "random"]
    batch_size: int
    count_per_batch: int
    writer: Annotated[str, AfterValidator(_writer_choice)] = "base"


@router.post("/prompt", dependencies=GPU_FREE)
async def generate_prompt(body: PromptRequest) -> Dict[str, Any]:
    runner = _require_runner()
    if body.duration_s <= 0:
        raise HTTPException(status_code=400, detail="duration_s must be positive")
    try:
        prompt, warnings, writer_model = await runner.write_prompt(
            body.source_path,
            body.idea,
            body.duration_s,
            template_id=body.template_id,
            writer=body.writer,
        )
    except WriterAdapterUnavailable as exc:
        # The dialog falls back to Base on this code.
        raise _adapter_409(exc) from exc
    except I2vUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VlmSelectError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except I2vRequestError as exc:
        # The runner re-raises an unknown template as
        # `I2vRequestError(str(exc)) from exc`, so the chained cause -- not
        # the message text -- is what identifies an unknown template. A
        # bare `I2vTemplateError` cause (the library itself is broken) is a
        # data fault, not a bad request -- 500, not 404/400.
        if isinstance(exc.__cause__, I2vTemplateNotFound):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc.__cause__, I2vTemplateError):
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (I2vError, VlmError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # No deterministic fallback exists for I2V: a success is always the writer.
    return {
        "prompt": prompt,
        "warnings": warnings,
        "writer_id": body.writer,
        "writer_model": writer_model,
    }


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
    template = None
    warnings_extra: List[str] = []
    if body.template_id:
        try:
            template = get_i2v_template(body.template_id)
        except I2vTemplateNotFound:
            warnings_extra.append(
                f"unknown i2v template '{body.template_id}'; linted as a single take"
            )
        except I2vTemplateError as exc:
            # The library itself is broken (a data fault), not an unknown
            # id -- must not be reported as "unknown template".
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    report = lint_i2v_report(body.prompt, body.duration_s, template=template)
    report["warnings"] = list(report["warnings"]) + warnings_extra
    return report


def _checked_form(
    body: GenerateRequest,
) -> Tuple[Dict[str, Any], int, Any]:
    """What /batches does first: the form's checks, the config and
    the preset its quality slot names, and the cadence template (or None)."""
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
    # Resolved tolerantly: an unknown id just falls back to single-take
    # lint (the render itself never needs the template -- the prompt text
    # already embodies the cadence), but a broken library file is a data
    # fault, not a bad request.
    template = None
    if body.template_id:
        try:
            template = get_i2v_template(body.template_id)
        except I2vTemplateNotFound:
            template = None
        except I2vTemplateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
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
    return cfg, preset_id, template


@router.post("/batches", dependencies=GPU_FREE)
async def start_batch(body: BatchRequestBody) -> Dict[str, Any]:
    runner = _require_runner()
    cfg, preset_id, template = _checked_form(body)
    if not 1 <= body.batch_size <= cfg["max_batch_size"]:
        raise HTTPException(
            status_code=400,
            detail=f"batch_size must be between 1 and {cfg['max_batch_size']}",
        )
    if not 1 <= body.count_per_batch <= cfg["max_count_per_batch"]:
        raise HTTPException(
            status_code=400,
            detail="count_per_batch must be between 1 and "
            f"{cfg['max_count_per_batch']}",
        )
    category_ids = await checked_category_ids(body.category_ids)
    request = I2vBatchRequest(
        source_path=body.source_path,
        prompt=body.prompt,
        idea=body.idea or "",
        duration_s=body.duration_s,
        quality=body.quality,
        seed=body.seed,
        seed_policy=body.seed_policy,
        batch_size=body.batch_size,
        count_per_batch=body.count_per_batch,
        megapixels=body.megapixels,
        loras=body.loras,
        preset_id=preset_id,
        writer=body.writer,
        steps=body.steps,
        template_id=body.template_id,
        output_root=cfg["output_root"] or None,
        output_prefix=cfg["output_prefix"],
        category_ids=category_ids,
        writer_id=body.writer_id,
        draft_prompt=body.draft_prompt,
        writer_model=body.writer_model,
        window=cfg["window"],
    )
    try:
        started = await runner.start_batch(request)
    except WriterAdapterUnavailable as exc:
        raise _adapter_409(exc) from exc
    except I2vUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except I2vRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (BindingError, PresetNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GpuBusyError as exc:
        raise gpu_busy_http_error() from exc
    return {
        "batch_id": started.batch_id,
        "total_videos": started.total_videos,
        "next_seed": started.next_seed,
        "warnings": (
            lint_i2v_prompt(body.prompt, body.duration_s, template=template)
            if body.prompt.strip()
            else []
        ),
    }


@router.get("/batches")
async def list_batches() -> List[Dict[str, Any]]:
    runner = _require_runner()
    rows: List[Dict[str, Any]] = runner.active_batches()
    return rows


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str) -> Dict[str, str]:
    """Idempotent: a run that is already over answers the same ``cancelled``;
    only an id the runner has never seen is a 404."""
    runner = _require_runner()
    try:
        await runner.cancel_batch(batch_id)
    except I2vNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "cancelled"}


@router.get("/templates")
async def list_templates() -> List[Dict[str, Any]]:
    cfg = get_i2v_config(load_app_config())
    try:
        return await _service().list_templates(cfg["durations"])
    except I2vTemplateError as exc:
        # A malformed library file must not load silently.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _megapixel_options() -> List[float]:
    return list(get_i2v_config(load_app_config())["megapixels"])


@router.get("/videos")
async def list_videos(
    source_path: Optional[str] = None,
    limit: int = Query(60, ge=1, le=200),
    before_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """One source image's clips, or -- without ``source_path`` -- a page of
    every clip, newest first (``limit`` / ``before_id`` apply only there)."""
    if source_path is None:
        return await _service().list_all_videos(limit, before_id, _megapixel_options())
    return await _service().list_videos(source_path, _megapixel_options())


@router.get("/sources")
async def list_sources() -> List[str]:
    """Source images that have one or more i2v clips in the library, for
    the smart-folder "Has I2V video" rule. One flat list rather than a
    per-image flag on /api/media, so the grid's covering indexes stay put."""
    return await _service().list_source_paths()


@router.patch("/videos/{video_id}")
async def update_video_form_state(
    video_id: int, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Autosave the dialog's form into one clip's editable ``form_state``.

    Partial: send only what changed, or the whole form. The clip's
    as-rendered columns (``prompt_used``, ``seed``, ``quality``, ...) are
    not reachable from here -- they are facts about the video. Needs
    neither the runner nor ComfyUI, so it works while either is down.
    """
    try:
        patch = validate_form_patch(body)
    except FormStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await _service().update_form_state(video_id, patch, _megapixel_options())
    if row is None:
        raise HTTPException(status_code=404, detail=f"No i2v video {video_id}")
    return row


@router.delete("/videos/{video_id}")
async def delete_video(video_id: int) -> Dict[str, str]:
    try:
        deleted = await _service().delete_video(video_id)
    except OSError as exc:  # trash_file: neither trashed nor deleted
        raise trash_failed_error(f"Deleting I2V clip {video_id}", exc) from None
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
