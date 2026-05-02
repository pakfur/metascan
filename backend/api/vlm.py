"""REST endpoints for Qwen3-VL tagging.

The VlmClient singleton is installed by the FastAPI lifespan via
``set_vlm_client(...)``. Endpoints fail fast with 503 if it's missing
(misconfigured deployment).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

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


# ---------------------------------------------------------------------------
# Retag background job
# ---------------------------------------------------------------------------


@dataclass
class _RetagJob:
    job_id: str
    paths: List[str]
    cancelled: bool = False
    current: int = 0
    total: int = 0


_jobs: Dict[str, _RetagJob] = {}


class RetagRequest(BaseModel):
    scope: Literal["paths", "all_clip"] = "paths"
    paths: Optional[List[str]] = None
    force: bool = False


async def _run_retag_job(job: "_RetagJob") -> None:
    client = _vlm_client
    db = get_db()
    if client is None:
        return
    job.total = len(job.paths)
    for i, path_str in enumerate(job.paths):
        if job.cancelled:
            break
        job.current = i
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            await client.ensure_started(client.model_id or "qwen3vl-4b")
            tags = await client.generate_tags(p)
            db.add_tag_indices(p, tags, source="vlm")
            try:
                from backend.ws.manager import ws_manager

                ws_manager.broadcast_sync(
                    "models",
                    "vlm_progress",
                    {"job_id": job.job_id, "current": i + 1, "total": job.total},
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning("retag failed for %s: %s", path_str, e)
    job.current = job.total


@router.post("/retag", status_code=202)
async def retag(body: RetagRequest) -> Dict[str, Any]:
    if _vlm_client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.scope == "paths":
        if not body.paths:
            raise HTTPException(
                status_code=400, detail="paths required for scope=paths"
            )
        targets = list(body.paths)
    elif body.scope == "all_clip":
        db = get_db()
        targets = _list_paths_for_retag(db, force=body.force)
    else:
        raise HTTPException(status_code=400, detail=f"unknown scope: {body.scope}")

    job = _RetagJob(job_id=str(uuid.uuid4()), paths=targets, total=len(targets))
    _jobs[job.job_id] = job
    asyncio.create_task(_run_retag_job(job))
    return {"job_id": job.job_id, "total": job.total}


@router.delete("/retag/{job_id}")
async def cancel_retag(job_id: str) -> Dict[str, str]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    job.cancelled = True
    return {"status": "cancelled"}


class ActiveBody(BaseModel):
    model_id: str


@router.post("/active")
async def set_active(body: ActiveBody) -> Dict[str, Any]:
    from metascan.core.vlm_models import REGISTRY

    client = _vlm_client
    if client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.model_id not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"unknown model: {body.model_id}")
    for job in _jobs.values():
        job.cancelled = True
    await client.swap_model(body.model_id)
    return client.snapshot()


def _list_paths_for_retag(db: Any, *, force: bool) -> List[str]:
    """SELECT paths whose tag rows are pure clip/both. With force=True also
    include vlm-tagged files."""
    sql = (
        "SELECT DISTINCT file_path FROM indices "
        "WHERE index_type='tag' AND source IN ('clip', 'both')"
    )
    if force:
        sql = "SELECT DISTINCT file_path FROM indices WHERE index_type='tag'"
    with db.lock:
        with db._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
    return [r["file_path"] for r in rows]
