"""REST endpoints for Qwen3-VL tagging.

The VlmClient singleton is installed by the FastAPI lifespan via
``set_vlm_client(...)``. Endpoints fail fast with 503 if it's missing
(misconfigured deployment).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vlm", tags=["vlm"])


_vlm_client: Optional[Any] = None


def set_vlm_client(client: Any) -> None:
    """Install the VlmClient singleton. Called from the FastAPI lifespan.

    Pass ``None`` to clear (used by tests)."""
    global _vlm_client
    _vlm_client = client


def get_vlm_client() -> Any:
    return _vlm_client


class TagRequest(BaseModel):
    path: str


@router.get("/status")
async def status() -> Dict[str, Any]:
    """Return the current VlmClient state snapshot."""
    client = _vlm_client
    if client is None:
        return {
            "state": "idle",
            "model_id": None,
            "base_url": None,
            "progress": {},
            "error": None,
        }
    return client.snapshot()


@router.post("/tag")
async def tag_one(body: TagRequest) -> Dict[str, List[str]]:
    """Tag a single image file using the active VLM model.

    Raises 503 if the VlmClient is not initialized, 404 if the file does
    not exist on disk.
    """
    client = _vlm_client
    if client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")

    p = Path(body.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {body.path}")

    if client.model_id is None:
        from metascan.core.hardware import detect_hardware, feature_gates

        gates = feature_gates(detect_hardware())
        candidates = [
            mid
            for mid, g in gates.items()
            if mid.startswith("qwen3vl-") and g.recommended
        ]
        if not candidates:
            raise HTTPException(
                status_code=503,
                detail="no recommended VLM model on this hardware",
            )
        await client.ensure_started(candidates[0])
    else:
        await client.ensure_started(client.model_id)

    tags: List[str] = await client.generate_tags(p)

    db = get_db()
    try:
        db.add_tag_indices(p, tags, source="vlm")
    except Exception:
        logger.exception("Failed to persist VLM tags for %s", p)

    return {"tags": tags}
