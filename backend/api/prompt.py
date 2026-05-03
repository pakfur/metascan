"""REST endpoints for VLM-backed prompt generation, transformation, and cleanup.

Decoupled from the playground UI — these endpoints are reusable by
ComfyUI custom nodes, batch tooling, and right-click actions. They are
non-streaming (the frontend shows "generating…" and renders the final
block) and abortable via standard client-disconnect propagation.

The VlmClient singleton is shared with backend.api.vlm; if no model is
active the endpoints return 503. Use POST /api/vlm/active first.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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

    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=p,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )


@router.post("/transform", response_model=GenerateResponse)
async def transform(body: TransformRequest) -> GenerateResponse:
    client = _require_ready_client()
    image_path = _require_existing_file(body.file_path) if body.file_path else None
    system, user = compose_transform_prompts(
        body.source_prompt, body.target_model, body.architecture
    )
    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=image_path,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )


@router.post("/clean", response_model=GenerateResponse)
async def clean(body: CleanRequest) -> GenerateResponse:
    client = _require_ready_client()
    system, user = compose_clean_prompts(body.source_prompt)
    start = time.monotonic()
    try:
        text = await client.generate_text(
            system_prompt=system,
            user_prompt=user,
            image_path=None,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except VlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
    elapsed = int((time.monotonic() - start) * 1000)
    return GenerateResponse(
        prompt=text,
        vlm_model_id=client.model_id or "",
        elapsed_ms=elapsed,
    )
