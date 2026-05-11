"""REST endpoints for VLM-backed prompt generation, transformation, and cleanup.

Decoupled from the playground UI — these endpoints are reusable by
ComfyUI custom nodes, batch tooling, and right-click actions. They are
non-streaming (the frontend shows "generating…" and renders the final
block) and abortable via standard client-disconnect propagation.

The VlmClient singleton is shared with backend.api.vlm; if no model is
active the endpoints return 503. Use POST /api/vlm/active first.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies import get_db

from backend.api import vlm as vlm_api
from metascan.core.meta_prompt_templates import (
    Architecture,
    ExtraOption,
    TargetModel,
    compose_generate_prompts,
    elements_for,
    parse_output,
)

# Legacy module — still consulted by transform / clean. Keep this import
# even though unused for ``generate``: the meta-prompts only redesign
# image-to-prompt generation.
from metascan.core.prompt_templates import (
    ExtraOption as _LegacyExtraOption,
    compose_clean_prompts,
    compose_transform_prompts,
)
from metascan.core.vlm_client import STATE_READY, VlmError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


# ---- Request / response models -------------------------------------------


class GenerateRequest(BaseModel):
    file_path: str
    target_model: TargetModel
    architecture: Architecture
    extras: List[ExtraOption] = Field(default_factory=list)
    # Per-element body overrides from the playground's editable table.
    # Index-aligned with ``elements_for(target_model)``; a ``None`` (or a
    # missing tail) means "use the default for that row". The legacy
    # behaviour — no table edits at all — is the empty list.
    element_overrides: List[Optional[str]] = Field(default_factory=list)
    temperature: float = 0.6
    max_tokens: int = 250


class MetaElementOut(BaseModel):
    """Shape returned by ``GET /api/prompt/elements``."""

    title: str
    default_body: str


class ElementsResponse(BaseModel):
    target_model: TargetModel
    elements: List[MetaElementOut]


class TransformRequest(BaseModel):
    source_prompt: str
    target_model: TargetModel
    architecture: Architecture
    extras: List[ExtraOption] = Field(default_factory=list)
    file_path: Optional[str] = None  # optional image grounding
    temperature: float = 0.6
    max_tokens: int = 250


class CleanRequest(BaseModel):
    source_prompt: str
    temperature: float = 0.4
    max_tokens: int = 250


class GenerateResponse(BaseModel):
    prompt: str
    vlm_model_id: str
    elapsed_ms: int
    # Populated for SDXL / Pony / Chroma / Qwen-Image when the meta-prompt
    # asks Qwen3 to emit a "Negative:" block. ``None`` for Flux.1 / Flux.2 /
    # Z-Image, whose meta-prompts do not request a negative.
    negative: Optional[str] = None


# Map the new two-option vocabulary onto legacy ExtraOption strings used
# by ``prompt_templates`` for transform / clean. Anything not in the
# table is dropped silently — the legacy builders ignore unknown keys.
_NEW_TO_LEGACY_EXTRA: Dict[ExtraOption, _LegacyExtraOption] = {
    "includeSafety": "keepPG",
    "includeUncensored": "includeUncensored",
}


def _to_legacy_extras(extras: List[ExtraOption]) -> List[_LegacyExtraOption]:
    out: List[_LegacyExtraOption] = []
    for x in extras:
        legacy = _NEW_TO_LEGACY_EXTRA.get(x)
        if legacy is not None:
            out.append(legacy)
    return out


# ---- Helpers --------------------------------------------------------------


def _require_ready_client() -> Any:
    client = vlm_api.get_vlm_client()
    if client is None or client.state != STATE_READY:
        raise HTTPException(
            status_code=503,
            detail="VLM not ready — activate Qwen3-VL first",
        )
    return client


def _require_existing_file(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {path_str}")
    return p


async def _run_generation(
    client: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    image_path: Optional[Path],
    temperature: float,
    max_tokens: int,
    target_model: Optional[TargetModel] = None,
) -> GenerateResponse:
    """Common timing + error-mapping wrapper around VlmClient.generate_text.

    When ``target_model`` is supplied, the response is run through
    :func:`metascan.core.meta_prompt_templates.parse_output` to peel off
    the ``Negative:`` block (if any). Without ``target_model`` the raw
    text becomes the prompt and ``negative`` stays ``None`` — the legacy
    transform / clean paths use this mode since their builders don't ask
    for a negative.

    Maps VlmError -> HTTP 502; returns wall-clock elapsed_ms and echoes
    the active model id for the response.
    """
    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=image_path,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)

    if target_model is not None:
        positive, negative = parse_output(target_model, text)
    else:
        positive, negative = text, None

    return GenerateResponse(
        prompt=positive,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
        negative=negative,
    )


# ---- Generation endpoints ------------------------------------------------


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest) -> GenerateResponse:
    client = _require_ready_client()
    p = _require_existing_file(body.file_path)
    # An empty ``element_overrides`` list is the "no edits" default and
    # is normalised to ``None`` so :func:`compose_generate_prompts`
    # short-circuits the substitution pass.
    overrides = list(body.element_overrides) if body.element_overrides else None
    system, user = compose_generate_prompts(
        body.target_model,
        body.architecture,
        list(body.extras),
        element_overrides=overrides,
    )
    return await _run_generation(
        client,
        system_prompt=system,
        user_prompt=user,
        image_path=p,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        target_model=body.target_model,
    )


@router.get("/elements", response_model=ElementsResponse)
async def list_elements(target_model: TargetModel) -> ElementsResponse:
    """Return the editable numbered-list elements for a target model.

    The playground's element table populates from this endpoint and
    refreshes whenever the user picks a different target. The response
    order matches the order returned by
    :func:`metascan.core.meta_prompt_templates.elements_for` so that
    indices line up with ``GenerateRequest.element_overrides``.
    """
    elements = [
        MetaElementOut(title=e.title, default_body=e.default_body)
        for e in elements_for(target_model)
    ]
    return ElementsResponse(target_model=target_model, elements=elements)


@router.post("/transform", response_model=GenerateResponse)
async def transform(body: TransformRequest) -> GenerateResponse:
    client = _require_ready_client()
    image_path = _require_existing_file(body.file_path) if body.file_path else None
    # Caption-length was removed from the public API. The legacy transform
    # builder still accepts it; pin to "Medium" for parity with the old
    # default, since the new UI no longer surfaces a control.
    system, user = compose_transform_prompts(
        body.source_prompt,
        body.target_model,
        body.architecture,
        _to_legacy_extras(list(body.extras)),
        "Medium",
    )
    return await _run_generation(
        client,
        system_prompt=system,
        user_prompt=user,
        image_path=image_path,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )


@router.post("/clean", response_model=GenerateResponse)
async def clean(body: CleanRequest) -> GenerateResponse:
    client = _require_ready_client()
    system, user = compose_clean_prompts(body.source_prompt)
    return await _run_generation(
        client,
        system_prompt=system,
        user_prompt=user,
        image_path=None,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )


# ---- Saved-prompt CRUD ---------------------------------------------------


class SaveRequest(BaseModel):
    """Persist a generated/transformed prompt against an image.

    target_model / architecture / styles are intentionally typed as plain
    str / List[str] rather than the Literal types from prompt_templates,
    so saved rows survive future enum extensions without a migration.
    `mode` stays Literal because it's also enforced by the DB CHECK
    constraint (extending it requires a coordinated DB + Pydantic edit).
    """

    file_path: str
    name: str
    prompt: str
    target_model: str
    architecture: str
    styles: List[str] = Field(default_factory=list)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    source_prompt: Optional[str] = None
    mode: Literal["generate", "transform", "clean"]
    negative: Optional[str] = None
    vlm_model_id: Optional[str] = None


class SavedPromptOut(BaseModel):
    id: int
    file_path: str
    name: str
    prompt: str
    negative: Optional[str]
    target_model: str
    architecture: str
    styles: List[str]
    temperature: Optional[float]
    max_tokens: Optional[int]
    source_prompt: Optional[str]
    mode: str
    vlm_model_id: Optional[str]
    created_at: str
    updated_at: str


@router.post("/save")
async def save_prompt(body: SaveRequest) -> Dict[str, int]:
    db = get_db()
    try:
        new_id = await asyncio.to_thread(
            db.save_prompt,
            file_path=body.file_path,
            name=body.name,
            prompt=body.prompt,
            target_model=body.target_model,
            architecture=body.architecture,
            styles=list(body.styles),
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            source_prompt=body.source_prompt,
            mode=body.mode,
            negative=body.negative,
            vlm_model_id=body.vlm_model_id,
        )
    except sqlite3.IntegrityError as e:
        # FK violation: file_path doesn't exist in media. Other operational
        # failures fall through to FastAPI's default 500 handler.
        logger.warning("save_prompt FK violation for %s: %s", body.file_path, e)
        raise HTTPException(
            status_code=400,
            detail=f"saved prompt requires file_path to exist in media: {body.file_path}",
        )
    return {"id": new_id}


@router.get("/by-image", response_model=List[SavedPromptOut])
async def list_by_image(file_path: str) -> List[SavedPromptOut]:
    db = get_db()
    rows = await asyncio.to_thread(db.list_saved_prompts, file_path)
    return [SavedPromptOut(**r) for r in rows]


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: int) -> Dict[str, str]:
    db = get_db()
    deleted = await asyncio.to_thread(db.delete_saved_prompt, prompt_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"saved prompt {prompt_id} not found"
        )
    return {"status": "deleted"}
