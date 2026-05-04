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
from metascan.core.prompt_templates import (
    Architecture,
    StyleEnhancement,
    TargetModel,
    compose_clean_prompts,
    compose_generate_prompts,
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
    styles: List[StyleEnhancement] = Field(default_factory=list)
    temperature: float = 0.6
    max_tokens: int = 250


class TransformRequest(BaseModel):
    source_prompt: str
    target_model: TargetModel
    architecture: Architecture
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
) -> GenerateResponse:
    """Common timing + error-mapping wrapper around VlmClient.generate_text.

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
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )


# ---- Generation endpoints ------------------------------------------------


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest) -> GenerateResponse:
    client = _require_ready_client()
    p = _require_existing_file(body.file_path)
    try:
        system, user = compose_generate_prompts(
            body.target_model, body.architecture, list(body.styles)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await _run_generation(
        client,
        system_prompt=system,
        user_prompt=user,
        image_path=p,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )


@router.post("/transform", response_model=GenerateResponse)
async def transform(body: TransformRequest) -> GenerateResponse:
    client = _require_ready_client()
    image_path = _require_existing_file(body.file_path) if body.file_path else None
    system, user = compose_transform_prompts(
        body.source_prompt, body.target_model, body.architecture
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
