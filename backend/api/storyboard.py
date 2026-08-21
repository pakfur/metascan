"""REST endpoints for the storyboard domain.

The StoryboardRunner singleton is installed by the FastAPI lifespan via
``set_storyboard_runner(...)``. Endpoints that drive the runner (parse,
synthesize, generate, cancel) fail fast with 503 when it is missing; CRUD
endpoints go straight through StoryboardService and don't need it.

The runner already broadcasts folder_created / folder_items_changed /
beat_images_changed / synthesis_progress through its own on_event
callback (wired in the lifespan) -- routes here must not re-broadcast
those, only translate exceptions into HTTP responses.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import load_app_config
from backend.dependencies import get_db
from backend.ws.manager import ws_manager
from backend.services.comfy_service import ComfyService
from backend.services.storyboard_service import (
    InvalidReferenceError,
    ParentNotFoundError,
    StoryboardService,
)
from metascan.core import ref_describe as rd
from metascan.core.comfy_client import ComfyError
from metascan.core.storyboard_brief import bucket_dims
from metascan.core.storyboard_parse import ParseError
from metascan.core.storyboard_runner import ConfirmRequiredError, StoryboardError
from metascan.core.storyboard_story import PACING_VALUES, STORY_SCALES
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError, pick_vlm_model
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/storyboard", tags=["storyboard"])

# Columns that are NOT NULL in the schema (see metascan/core/database_sqlite.py's
# CREATE TABLE storyboards/storyboard_subjects/scenes/panels). PATCH now uses
# exclude_unset=True so an explicit `null` for a nullable column (e.g.
# `preset_id`, `shot_size`, `notes`) is a legitimate "clear this field"
# request -- but an explicit `null` for one of these would otherwise reach
# sqlite3 as a raw NOT NULL constraint violation (500). Reject it as a 400
# instead, naming the offending field(s).
_STORYBOARD_NOT_NULLABLE = frozenset(
    {
        "name",
        "aspect_ratio",
        "target_model",
        "architecture",
        "base_seed",
        "batch_size",
        "pacing",
        "story_scale",
    }
)
_SUBJECT_NOT_NULLABLE = frozenset({"name", "description", "sort_order", "sheet_ref"})
_SCENE_NOT_NULLABLE = frozenset({"name", "sort_order", "arc_beats"})
_PANEL_NOT_NULLABLE = frozenset(
    {
        "sort_order",
        "action",
        "duration_s",
        "image_loras",
        "video_loras",
        "video_prompt_locked",
        "is_turn",
    }
)
_BEAT_NOT_NULLABLE = frozenset(
    {
        "sort_order",
        "duration_s",
        "action",
        "is_cut",
        "dialog",
        "subject_ids",
        "prompt_locked",
    }
)

# H3 compiler only supports the "minimax" video target for now; "ltx" is a
# reserved id for a future target, not yet wired to any prompt compiler.
_VIDEO_TARGETS = frozenset({"minimax"})
_VIDEO_MODES = frozenset({"t2va", "i2va", "fl2va", "ref2va"})
_VIDEO_ANCHORS = frozenset({"keeper", "prev_last"})


def _reject_null_for_required(
    fields: Dict[str, Any], not_nullable: "frozenset[str]"
) -> None:
    nulled = sorted(k for k in not_nullable if k in fields and fields[k] is None)
    if nulled:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot clear required field(s): {', '.join(nulled)}",
        )


_storyboard_runner: Optional[Any] = None
# Background synthesize() tasks. A bare asyncio.create_task result is only
# weakly referenced by the event loop and can be garbage-collected
# mid-flight -- holding a strong reference here (and self-evicting once
# done) keeps them alive. Mirrors StoryboardRunner._ingest_tasks.
_background_tasks: "set[asyncio.Task[Any]]" = set()


def set_storyboard_runner(runner: Any) -> None:
    """Install the StoryboardRunner singleton. Pass None to clear (tests)."""
    global _storyboard_runner
    _storyboard_runner = runner


def get_storyboard_runner() -> Any:
    return _storyboard_runner


def _require_runner() -> Any:
    if _storyboard_runner is None:
        raise HTTPException(status_code=503, detail="storyboard runner not initialized")
    return _storyboard_runner


def _service() -> StoryboardService:
    return StoryboardService(get_db())


# ---- request models -------------------------------------------------------


class StoryboardCreate(BaseModel):
    name: str
    target_model: str
    architecture: str = "t2i"
    aspect_ratio: str = "16:9"
    style_block: Optional[str] = None
    negative: Optional[str] = None
    preset_id: Optional[int] = None
    base_seed: Optional[int] = None
    batch_size: int = 4
    notes: Optional[str] = None


class StoryboardPatch(BaseModel):
    name: Optional[str] = None
    source_text: Optional[str] = None
    aspect_ratio: Optional[str] = None
    style_block: Optional[str] = None
    negative: Optional[str] = None
    target_model: Optional[str] = None
    architecture: Optional[str] = None
    preset_id: Optional[int] = None
    base_seed: Optional[int] = None
    batch_size: Optional[int] = None
    outline: Optional[str] = None
    video_target: Optional[str] = None
    video_mode: Optional[str] = None
    video_preset_id: Optional[int] = None
    notes: Optional[str] = None
    video_output_dir: Optional[str] = None
    video_name_template: Optional[str] = None
    image_name_template: Optional[str] = None
    pacing: Optional[str] = None
    story_scale: Optional[str] = None


class SubjectCreate(BaseModel):
    name: str
    description: str
    lora_name: Optional[str] = None
    lora_strength: float = 0.8
    reference_path: Optional[str] = None
    reference_path_2: Optional[str] = None
    sort_order: int = 0
    voice: Optional[str] = None
    voice_ref_path: Optional[str] = None


class SubjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    reference_path: Optional[str] = None
    reference_path_2: Optional[str] = None
    sort_order: Optional[int] = None
    voice: Optional[str] = None
    voice_ref_path: Optional[str] = None
    sheet_ref: Optional[int] = None


class SceneCreate(BaseModel):
    name: str
    sort_order: int = 0
    subtitle: Optional[str] = None
    setting: Optional[str] = None
    location: Optional[str] = None
    time_of_day: Optional[str] = None
    mood: Optional[str] = None
    lighting: Optional[str] = None
    notes: Optional[str] = None
    reference_path: Optional[str] = None


class ScenePatch(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None
    subtitle: Optional[str] = None
    setting: Optional[str] = None
    location: Optional[str] = None
    time_of_day: Optional[str] = None
    mood: Optional[str] = None
    lighting: Optional[str] = None
    notes: Optional[str] = None
    reference_path: Optional[str] = None
    arc_beats: Optional[List[str]] = None
    charge_in: Optional[int] = None
    charge_out: Optional[int] = None


class PanelCreate(BaseModel):
    action: str
    sort_order: int = 0
    duration_s: float = 12.0


class LoraEntry(BaseModel):
    name: str
    strength: float = 1.0


class PanelPatch(BaseModel):
    sort_order: Optional[int] = None
    action: Optional[str] = None
    duration_s: Optional[float] = None
    image_loras: Optional[List[LoraEntry]] = None
    video_loras: Optional[List[LoraEntry]] = None
    video_prompt: Optional[str] = None
    video_prompt_locked: Optional[int] = None
    video_anchor: Optional[str] = None
    is_turn: Optional[int] = None
    subtext: Optional[str] = None


class ParseRequest(BaseModel):
    text: str
    confirm: bool = False


class SynthesizeRequest(BaseModel):
    beat_ids: Optional[List[int]] = None
    force: bool = False


class CompileRequest(BaseModel):
    panel_ids: Optional[List[int]] = None
    force: bool = False


class GenerateRequest(BaseModel):
    beat_ids: Optional[List[int]] = None
    only_failed: bool = False


class GenerateVideoRequest(BaseModel):
    panel_ids: Optional[List[int]] = None
    only_failed: bool = False


class ComposeRequest(BaseModel):
    stages: Optional[List[str]] = None
    scene_ids: Optional[List[int]] = None
    panel_ids: Optional[List[int]] = None
    confirm: bool = False


class DialogLine(BaseModel):
    subject_id: Optional[int] = None
    voice: Optional[str] = None
    delivery: Optional[str] = None
    language: str = "English"
    text: str


class BeatCreate(BaseModel):
    action: str
    sort_order: int = 0
    duration_s: float = 4.0
    shot_size: Optional[str] = None
    angle: Optional[str] = None
    lens: Optional[str] = None
    subject_ids: Optional[List[int]] = None
    camera_motion: Optional[str] = None
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    is_cut: int = 0
    dialog: List[DialogLine] = []
    sound: Optional[str] = None


class BeatPatch(BaseModel):
    action: Optional[str] = None
    sort_order: Optional[int] = None
    duration_s: Optional[float] = None
    shot_size: Optional[str] = None
    angle: Optional[str] = None
    lens: Optional[str] = None
    subject_ids: Optional[List[int]] = None
    camera_motion: Optional[str] = None
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    is_cut: Optional[int] = None
    dialog: Optional[List[DialogLine]] = None
    sound: Optional[str] = None
    brief: Optional[str] = None
    prompt: Optional[str] = None
    prompt_locked: Optional[int] = None
    prompt_source: Optional[str] = None
    composition: Optional[str] = None
    light_quality: Optional[str] = None
    emotional_intent: Optional[str] = None
    reveals: Optional[str] = None
    movement_motivation: Optional[str] = None
    # selected_image_id deliberately NOT exposed: keeper selection toggles
    # media.hidden via db.select_beat_image. Use POST /beats/{id}/select.


class SelectRequest(BaseModel):
    image_id: Optional[int] = None


# ---- storyboards ------------------------------------------------------------


@router.get("")
async def list_storyboards() -> List[Dict[str, Any]]:
    return await _service().list_storyboards()


@router.post("")
async def create_storyboard(body: StoryboardCreate) -> Dict[str, int]:
    try:
        bucket_dims(body.aspect_ratio, body.target_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    base_seed = (
        body.base_seed if body.base_seed is not None else random.randint(0, 2**31 - 1)
    )
    batch_size = max(1, min(16, body.batch_size))

    storyboard_id = await _service().create_storyboard(
        name=body.name,
        target_model=body.target_model,
        architecture=body.architecture,
        aspect_ratio=body.aspect_ratio,
        style_block=body.style_block,
        negative=body.negative,
        preset_id=body.preset_id,
        base_seed=base_seed,
        batch_size=batch_size,
        notes=body.notes,
    )
    return {"id": storyboard_id}


@router.get("/{storyboard_id}")
async def get_storyboard(storyboard_id: int) -> Dict[str, Any]:
    tree = await _service().get_storyboard_tree(storyboard_id)
    if tree is None:
        raise HTTPException(status_code=404, detail=f"No storyboard {storyboard_id}")
    return tree


@router.patch("/{storyboard_id}")
async def patch_storyboard(storyboard_id: int, body: StoryboardPatch) -> Dict[str, str]:
    svc = _service()
    existing = await svc.get_storyboard(storyboard_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No storyboard {storyboard_id}")

    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _STORYBOARD_NOT_NULLABLE)
    if "pacing" in fields and fields["pacing"] not in PACING_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"pacing must be one of: {', '.join(PACING_VALUES)}",
        )
    if "story_scale" in fields and fields["story_scale"] not in STORY_SCALES:
        raise HTTPException(
            status_code=400,
            detail=f"story_scale must be one of: {', '.join(STORY_SCALES)}",
        )
    if "aspect_ratio" in fields or "target_model" in fields:
        effective_aspect = fields.get("aspect_ratio", existing["aspect_ratio"])
        effective_target = fields.get("target_model", existing["target_model"])
        try:
            bucket_dims(effective_aspect, effective_target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "batch_size" in fields:
        fields["batch_size"] = max(1, min(16, fields["batch_size"]))
    if (
        fields.get("video_target") is not None
        and fields["video_target"] not in _VIDEO_TARGETS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"video_target {fields['video_target']!r} is not supported "
            "-- only 'minimax' is available ('ltx' reserved for a future target)",
        )
    if (
        fields.get("video_mode") is not None
        and fields["video_mode"] not in _VIDEO_MODES
    ):
        raise HTTPException(
            status_code=400,
            detail=f"video_mode {fields['video_mode']!r} is not one of "
            f"{sorted(_VIDEO_MODES)}",
        )
    if fields.get("video_preset_id") is not None:
        preset = await ComfyService(get_db()).get_preset(fields["video_preset_id"])
        if preset is None:
            raise HTTPException(
                status_code=400,
                detail=f"no workflow preset with id {fields['video_preset_id']}",
            )
    if fields.get("video_output_dir") is not None:
        # Must be one of the configured library directories — the point of
        # the setting is that rendered clips land inside the library.
        configured = {
            str(d.get("filepath"))
            for d in (load_app_config().get("directories") or [])
            if isinstance(d, dict) and d.get("filepath")
        }
        if fields["video_output_dir"] not in configured:
            raise HTTPException(
                status_code=400,
                detail=f"video_output_dir {fields['video_output_dir']!r} is not "
                "a configured library directory",
            )

    if fields:
        await svc.update_storyboard(storyboard_id, **fields)
    return {"status": "updated"}


@router.delete("/{storyboard_id}")
async def delete_storyboard(
    storyboard_id: int, purge_images: bool = False
) -> Dict[str, str]:
    """Delete a storyboard, its library folder, and (optionally) its
    generated images.

    Default: generated images are released into the library (unhidden).
    With ``purge_images=true`` their media rows are deleted and the files
    moved to the OS trash. The folder_deleted broadcast here is a route
    responsibility like backend/api/folders.py's -- it is not one of the
    runner-owned events the module docstring forbids re-broadcasting.
    """
    ok, folder_id = await _service().delete_storyboard(storyboard_id, purge_images)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No storyboard {storyboard_id}")
    if folder_id is not None:
        ws_manager.broadcast_sync("folders", "folder_deleted", {"id": folder_id})
    return {"status": "deleted"}


# ---- runner-backed endpoints ------------------------------------------------


@router.post("/{storyboard_id}/parse")
async def parse_storyboard(storyboard_id: int, body: ParseRequest) -> Dict[str, Any]:
    runner = _require_runner()
    try:
        return await runner.parse(storyboard_id, body.text, confirm=body.confirm)
    except ConfirmRequiredError as exc:
        # Must be caught before StoryboardError -- it's a subclass.
        raise HTTPException(
            status_code=409,
            detail={"code": "confirm_required", "message": str(exc)},
        ) from exc
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StoryboardError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{storyboard_id}/synthesize", status_code=202)
async def synthesize_storyboard(
    storyboard_id: int, body: SynthesizeRequest
) -> Dict[str, Any]:
    runner = _require_runner()
    tree = await _service().get_storyboard_tree(storyboard_id)
    if tree is None:
        raise HTTPException(status_code=404, detail=f"No storyboard {storyboard_id}")

    all_beat_ids = [
        b["id"]
        for scene in tree["scenes"]
        for p in scene["panels"]
        for b in (p.get("beats") or [])
    ]
    if body.beat_ids is not None:
        wanted = set(body.beat_ids)
        total = len([bid for bid in all_beat_ids if bid in wanted])
    else:
        total = len(all_beat_ids)

    task = asyncio.create_task(
        runner.synthesize(storyboard_id, beat_ids=body.beat_ids, force=body.force)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "started", "total": total}


@router.post("/{storyboard_id}/compile", status_code=202)
async def compile_storyboard(
    storyboard_id: int, body: CompileRequest
) -> Dict[str, Any]:
    runner = _require_runner()
    svc = _service()
    storyboard = await svc.get_storyboard(storyboard_id)
    if storyboard is None:
        raise HTTPException(status_code=404, detail=f"No storyboard {storyboard_id}")
    if storyboard.get("video_target") != "minimax":
        raise HTTPException(
            status_code=400,
            detail=f"storyboard {storyboard_id} has video_target "
            f"{storyboard.get('video_target')!r} -- the H3 compiler only "
            "supports 'minimax'",
        )

    tree = await svc.get_storyboard_tree(storyboard_id)
    all_panels = [p for scene in tree["scenes"] for p in scene["panels"]]
    explicit_ids = set(body.panel_ids) if body.panel_ids is not None else None

    total = 0
    for panel in all_panels:
        if explicit_ids is not None and panel["id"] not in explicit_ids:
            continue
        forced_override = (
            body.force and explicit_ids is not None and panel["id"] in explicit_ids
        )
        if panel["video_prompt_locked"] and not forced_override:
            continue
        total += 1

    task = asyncio.create_task(
        runner.compile_video(storyboard_id, panel_ids=body.panel_ids, force=body.force)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "started", "total": total}


@router.post("/{storyboard_id}/generate")
async def generate_storyboard(
    storyboard_id: int, body: GenerateRequest
) -> Dict[str, Any]:
    runner = _require_runner()
    try:
        job_ids = await runner.generate(
            storyboard_id,
            beat_ids=body.beat_ids,
            only_failed=body.only_failed,
        )
    except StoryboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": job_ids}


@router.post("/{storyboard_id}/generate-video")
async def generate_storyboard_video(
    storyboard_id: int, body: GenerateVideoRequest
) -> Dict[str, Any]:
    """Submit ref2v (H3/MiniMax) jobs for a storyboard's panels.

    Synchronous await, mirroring /generate (not the 202 fire-and-forget
    pattern used by synthesize/compile/compose) -- jobs are only queued
    here, not run in-request. Returns the runner's
    {"jobs": [...], "skipped": [...]} verbatim.
    """
    runner = _require_runner()
    try:
        result = await runner.generate_video(
            storyboard_id, panel_ids=body.panel_ids, only_failed=body.only_failed
        )
    except StoryboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComfyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@router.post("/{storyboard_id}/compose", status_code=202)
async def compose_storyboard(
    storyboard_id: int, body: ComposeRequest
) -> Dict[str, str]:
    runner = _require_runner()
    from metascan.core import storyboard_story as story

    stages = tuple(body.stages) if body.stages else story.STAGES
    try:
        await runner.check_compose_gates(
            storyboard_id, stages, body.scene_ids, body.panel_ids, body.confirm
        )
    except ConfirmRequiredError as exc:
        # Must be caught before StoryboardError -- it's a subclass.
        raise HTTPException(
            status_code=409,
            detail={"code": "confirm_required", "message": str(exc)},
        ) from exc
    except StoryboardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = asyncio.create_task(
        runner.compose_story(
            storyboard_id,
            stages=stages,
            scene_ids=body.scene_ids,
            panel_ids=body.panel_ids,
            confirm=body.confirm,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "started"}


@router.post("/{storyboard_id}/cancel")
async def cancel_storyboard(storyboard_id: int) -> Dict[str, int]:
    runner = _require_runner()
    try:
        cancelled = await runner.cancel(storyboard_id)
    except StoryboardError as exc:
        # cancel() only raises StoryboardError for an unknown storyboard id.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"cancelled": cancelled}


# ---- subjects -----------------------------------------------------------


@router.post("/{storyboard_id}/subjects")
async def create_subject(storyboard_id: int, body: SubjectCreate) -> Dict[str, int]:
    try:
        subject_id = await _service().create_subject(
            storyboard_id,
            name=body.name,
            description=body.description,
            lora_name=body.lora_name,
            lora_strength=body.lora_strength,
            reference_path=body.reference_path,
            reference_path_2=body.reference_path_2,
            sort_order=body.sort_order,
            voice_ref_path=body.voice_ref_path,
        )
    except ParentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": subject_id}


@router.patch("/subjects/{subject_id}")
async def patch_subject(subject_id: int, body: SubjectPatch) -> Dict[str, str]:
    svc = _service()
    if not await svc.subject_exists(subject_id):
        raise HTTPException(status_code=404, detail=f"No subject {subject_id}")
    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _SUBJECT_NOT_NULLABLE)
    if "sheet_ref" in fields and fields["sheet_ref"] not in (0, 1):
        raise HTTPException(status_code=400, detail="sheet_ref must be 0 or 1")
    if fields:
        try:
            await svc.update_subject(subject_id, **fields)
        except InvalidReferenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated"}


@router.delete("/subjects/{subject_id}")
async def delete_subject(subject_id: int) -> Dict[str, str]:
    ok = await _service().delete_subject(subject_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No subject {subject_id}")
    return {"status": "deleted"}


@router.post("/subjects/{subject_id}/describe")
async def describe_subject(subject_id: int) -> Dict[str, Any]:
    """VLM-describe a subject from its reference image(s).

    Review-only -- never writes description/voice back to the DB; the
    caller decides whether to accept the suggestion via a normal PATCH.
    """
    from backend.api.vlm import get_vlm_client

    vlm = get_vlm_client()
    if vlm is None:
        raise HTTPException(status_code=503, detail="no VLM client configured")
    svc = _service()
    subject = await svc.get_subject(subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="subject not found")
    refs = [
        p for p in (subject.get("reference_path"), subject.get("reference_path_2")) if p
    ]
    if not refs:
        raise HTTPException(status_code=400, detail="no reference image set")
    try:
        model_id = pick_vlm_model(vlm)
    except VlmSelectError as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        await vlm.ensure_started(model_id)
        raw = await vlm.generate_text(
            system_prompt=rd.REF_DESCRIBE_SUBJECT_SYSTEM,
            user_prompt=rd.SUBJECT_USER_PROMPT,
            image_paths=[Path(to_native_path(p)) for p in refs],
            grammar=rd.SUBJECT_DESCRIBE_GRAMMAR,
            temperature=0.3,
            max_tokens=400,
            timeout=180.0,
        )
        return rd.validate_subject_describe(raw)
    except (VlmError, TimeoutError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except rd.DescribeError as e:
        raise HTTPException(status_code=502, detail=f"unusable VLM output: {e}")


# ---- scenes ---------------------------------------------------------------


@router.post("/{storyboard_id}/scenes")
async def create_scene(storyboard_id: int, body: SceneCreate) -> Dict[str, int]:
    try:
        scene_id = await _service().create_scene(
            storyboard_id,
            name=body.name,
            sort_order=body.sort_order,
            subtitle=body.subtitle,
            setting=body.setting,
            location=body.location,
            time_of_day=body.time_of_day,
            mood=body.mood,
            lighting=body.lighting,
            notes=body.notes,
            reference_path=body.reference_path,
        )
    except ParentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReferenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": scene_id}


@router.patch("/scenes/{scene_id}")
async def patch_scene(scene_id: int, body: ScenePatch) -> Dict[str, str]:
    svc = _service()
    if not await svc.scene_exists(scene_id):
        raise HTTPException(status_code=404, detail=f"No scene {scene_id}")
    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _SCENE_NOT_NULLABLE)
    if fields:
        try:
            await svc.update_scene(scene_id, **fields)
        except InvalidReferenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated"}


@router.delete("/scenes/{scene_id}")
async def delete_scene(scene_id: int, purge_images: bool = False) -> Dict[str, str]:
    ok = await _service().delete_scene(scene_id, purge_images)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No scene {scene_id}")
    return {"status": "deleted"}


@router.post("/scenes/{scene_id}/describe")
async def describe_scene(scene_id: int) -> Dict[str, Any]:
    """VLM-describe a scene from its reference image.

    Review-only -- never writes setting/lighting/mood back to the DB; the
    caller decides whether to accept the suggestion via a normal PATCH.
    """
    from backend.api.vlm import get_vlm_client

    vlm = get_vlm_client()
    if vlm is None:
        raise HTTPException(status_code=503, detail="no VLM client configured")
    svc = _service()
    scene = await svc.get_scene(scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    ref = scene.get("reference_path")
    if not ref:
        raise HTTPException(status_code=400, detail="no reference image set")
    try:
        model_id = pick_vlm_model(vlm)
    except VlmSelectError as e:
        raise HTTPException(status_code=503, detail=str(e))
    try:
        await vlm.ensure_started(model_id)
        raw = await vlm.generate_text(
            system_prompt=rd.REF_DESCRIBE_SETTING_SYSTEM,
            user_prompt=rd.SETTING_USER_PROMPT,
            image_paths=[Path(to_native_path(ref))],
            grammar=rd.SETTING_DESCRIBE_GRAMMAR,
            temperature=0.3,
            max_tokens=400,
            timeout=180.0,
        )
        return rd.validate_setting_describe(raw)
    except (VlmError, TimeoutError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except rd.DescribeError as e:
        raise HTTPException(status_code=502, detail=f"unusable VLM output: {e}")


# ---- panels -----------------------------------------------------------------


@router.post("/scenes/{scene_id}/panels")
async def create_panel(scene_id: int, body: PanelCreate) -> Dict[str, int]:
    try:
        panel_id = await _service().create_panel(
            scene_id,
            action=body.action,
            sort_order=body.sort_order,
            duration_s=body.duration_s,
        )
    except ParentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": panel_id}


@router.patch("/panels/{panel_id}")
async def patch_panel(panel_id: int, body: PanelPatch) -> Dict[str, Any]:
    svc = _service()
    existing = await svc.get_panel(panel_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No panel {panel_id}")

    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _PANEL_NOT_NULLABLE)
    # exclude_unset propagates into nested models, silently dropping the
    # defaulted `strength` from a lora entry -- re-dump the entries in full.
    for key in ("image_loras", "video_loras"):
        if fields.get(key) is not None:
            fields[key] = [e.model_dump() for e in getattr(body, key) or []]
    if (
        fields.get("video_anchor") is not None
        and fields["video_anchor"] not in _VIDEO_ANCHORS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"video_anchor {fields['video_anchor']!r} is not one of "
            f"{sorted(_VIDEO_ANCHORS)}",
        )
    if "video_prompt" in fields:
        if fields["video_prompt"] is not None:
            # Server wins: mirror the prompt block above. A user edit also
            # clears any stale lint warnings from a prior compile (spec
            # §6: warnings are "cleared on user edit").
            fields["video_prompt_locked"] = 1
            fields["video_prompt_source"] = "user"
            fields["video_prompt_warnings"] = None
        else:
            # Explicit `video_prompt: null` clears the whole video-prompt
            # block, not just the text.
            fields["video_prompt_source"] = None
            fields["video_prompt_locked"] = 0
            fields["video_prompt_warnings"] = None

    if fields:
        await svc.update_panel(panel_id, **fields)
    updated = await svc.get_panel(panel_id)
    return updated if updated is not None else existing


@router.delete("/panels/{panel_id}")
async def delete_panel(panel_id: int, purge_images: bool = False) -> Dict[str, str]:
    ok = await _service().delete_panel(panel_id, purge_images)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No panel {panel_id}")
    return {"status": "deleted"}


@router.delete("/videos/{video_id}")
async def delete_panel_video(video_id: int) -> Dict[str, str]:
    """Fully remove one rendered clip: panel_videos row, media row
    (unless still referenced elsewhere), and the file to the OS trash."""
    ok = await _service().delete_panel_video(video_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No panel video {video_id}")
    return {"status": "deleted"}


# ---- beats ------------------------------------------------------------------


@router.post("/panels/{panel_id}/beats")
async def create_beat(panel_id: int, body: BeatCreate) -> Dict[str, int]:
    svc = _service()
    if not await svc.panel_exists(panel_id):
        raise HTTPException(status_code=404, detail=f"No panel {panel_id}")
    fields = body.model_dump()
    beat_id = await svc.create_beat(panel_id, **fields)
    return {"id": beat_id}


@router.patch("/beats/{beat_id}")
async def patch_beat(beat_id: int, body: BeatPatch) -> Dict[str, Any]:
    svc = _service()
    existing = await svc.get_beat(beat_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No beat {beat_id}")

    fields = body.model_dump(exclude_unset=True)
    _reject_null_for_required(fields, _BEAT_NOT_NULLABLE)
    if body.prompt is not None:
        # Server wins: a user-supplied prompt always locks, regardless of
        # whatever prompt_locked/prompt_source the caller also sent.
        fields["prompt_locked"] = 1
        fields["prompt_source"] = "user"

    if fields:
        await svc.update_beat(beat_id, **fields)
    updated = await svc.get_beat(beat_id)
    return updated if updated is not None else existing


@router.post("/beats/{beat_id}/select")
async def select_beat_image(beat_id: int, body: SelectRequest) -> Dict[str, Any]:
    svc = _service()
    ok = await svc.select_beat_image(beat_id, body.image_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No beat {beat_id}, or image {body.image_id} does not "
            "belong to it",
        )
    updated = await svc.get_beat(beat_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No beat {beat_id}")
    return updated


@router.delete("/beats/{beat_id}")
async def delete_beat(beat_id: int, purge_images: bool = False) -> Dict[str, str]:
    ok = await _service().delete_beat(beat_id, purge_images)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No beat {beat_id}")
    return {"status": "deleted"}
