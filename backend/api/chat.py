"""Streaming multi-turn chat with the active Qwen VLM.

Backs the chat window opened from an image's context menu ("Prompt
Playground…"). Deliberately separate from ``backend/api/prompt.py``: the
prompt endpoints are single-shot generate / transform / clean calls with
fixed meta-prompts, while this endpoint is a free-form conversation where
the client owns the system prompt and the full message history.

The response is NDJSON (``application/x-ndjson``), one JSON object per
line:

* ``{"type": "delta", "text": "..."}`` — answer tokens
* ``{"type": "reasoning", "text": "..."}`` — thinking tokens (reasoning
  models only)
* ``{"type": "done", "elapsed_ms": int, "vlm_model_id": str}`` — success
* ``{"type": "error", "message": str}`` — failure after streaming began

Errors that can be detected before the stream starts (VLM not ready,
missing file, bad history) are returned as ordinary HTTP errors so the
frontend's error handling matches every other endpoint.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api import vlm as vlm_api
from metascan.core.vlm_client import STATE_READY, VlmClient, VlmError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(min_length=1)
    system_prompt: str = ""
    # When set (and ``include_image`` is true) the image is attached to
    # the first user message of the conversation.
    file_path: Optional[str] = None
    include_image: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=8192)


def _require_ready_client() -> Any:
    client = vlm_api.get_vlm_client()
    if client is None or client.state != STATE_READY:
        raise HTTPException(
            status_code=503,
            detail="VLM not ready — activate a Qwen VLM first",
        )
    return client


def _ndjson(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


@router.post("")
async def chat(body: ChatRequest) -> StreamingResponse:
    client = _require_ready_client()

    if body.messages[-1].role != "user":
        raise HTTPException(
            status_code=422, detail="last message must be from the user"
        )

    image_path: Optional[Path] = None
    if body.include_image:
        if not body.file_path:
            raise HTTPException(
                status_code=422, detail="include_image requires file_path"
            )
        image_path = Path(body.file_path)
        if not image_path.is_file():
            raise HTTPException(
                status_code=404, detail=f"file not found: {body.file_path}"
            )
        if not VlmClient.is_image_path(image_path):
            raise HTTPException(
                status_code=422,
                detail=f"not an image the VLM can read: {image_path.suffix}",
            )

    messages: List[Dict[str, str]] = []
    if body.system_prompt.strip():
        messages.append({"role": "system", "content": body.system_prompt})
    messages.extend({"role": m.role, "content": m.content} for m in body.messages)

    async def stream() -> AsyncIterator[bytes]:
        start = time.monotonic()
        try:
            async for kind, text in client.chat_stream(
                messages=messages,
                image_path=image_path,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
            ):
                yield _ndjson(
                    {"type": "delta" if kind == "content" else kind, "text": text}
                )
        except VlmError as e:
            logger.warning("chat stream failed: %s", e)
            yield _ndjson({"type": "error", "message": str(e)})
            return
        elapsed = int((time.monotonic() - start) * 1000)
        yield _ndjson(
            {
                "type": "done",
                "elapsed_ms": elapsed,
                "vlm_model_id": client.model_id or "",
            }
        )

    return StreamingResponse(stream(), media_type="application/x-ndjson")
