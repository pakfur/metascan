"""Upscale queue management endpoints."""

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upscale", tags=["upscale"])

# Lazy-loaded queue reference
_upscale_queue = None


def _get_queue():
    """Get or create the upscale queue (lazy import to avoid PyQt at module level)."""
    global _upscale_queue
    if _upscale_queue is None:
        from metascan.core.upscale_queue_process import ProcessUpscaleQueue
        from metascan.utils.app_paths import get_data_dir

        queue_dir = get_data_dir() / "upscale_queue"
        _upscale_queue = ProcessUpscaleQueue(queue_dir)
    return _upscale_queue


class UpscaleTaskRequest(BaseModel):
    file_path: str
    scale_factor: int = 2
    model_type: str = "general"
    face_enhance: bool = False
    interpolate_frames: bool = False
    fps_multiplier: int = 2
    custom_fps: Optional[int] = None


class UpscaleSubmitRequest(BaseModel):
    tasks: List[UpscaleTaskRequest]
    concurrent_workers: int = 1


@router.post("")
async def submit_upscale(body: UpscaleSubmitRequest):
    """Add upscale tasks to the queue."""
    queue = _get_queue()
    task_ids = []
    for task in body.tasks:
        task_id = await asyncio.to_thread(
            queue.add_task,
            task.file_path,
            task.scale_factor,
            task.model_type,
            task.face_enhance,
            task.interpolate_frames,
            task.fps_multiplier,
            task.custom_fps,
        )
        task_ids.append(task_id)
    return {"task_ids": task_ids, "status": "queued"}


@router.get("/queue")
async def get_queue():
    """List all upscale tasks with their status."""
    queue = _get_queue()
    tasks = await asyncio.to_thread(queue.get_all_tasks)
    return {"tasks": tasks}


@router.patch("/{task_id}")
async def update_task(task_id: str, body: dict):
    """Update a task (pause/resume/cancel)."""
    queue = _get_queue()
    action = body.get("action")
    if action == "cancel":
        await asyncio.to_thread(queue.remove_task, task_id)
        return {"status": "cancelled"}
    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@router.delete("/{task_id}")
async def remove_task(task_id: str):
    """Remove a task from the queue."""
    queue = _get_queue()
    await asyncio.to_thread(queue.remove_task, task_id)
    return {"status": "removed"}


@router.post("/pause-all")
async def pause_all():
    """Pause all processing."""
    queue = _get_queue()
    await asyncio.to_thread(queue.pause)
    return {"status": "paused"}


@router.post("/resume-all")
async def resume_all():
    """Resume processing."""
    queue = _get_queue()
    await asyncio.to_thread(queue.resume)
    return {"status": "resumed"}


@router.post("/clear-completed")
async def clear_completed():
    """Clear all completed tasks from the queue."""
    queue = _get_queue()
    await asyncio.to_thread(queue.clear_completed)
    return {"status": "cleared"}
