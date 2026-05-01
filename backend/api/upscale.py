"""Upscale queue management endpoints."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upscale", tags=["upscale"])

# Match worker expectations (metascan/workers/upscale_worker.py)
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}

# Lazy-loaded queue reference + background poller task
_upscale_queue = None
_poll_task: Optional[asyncio.Task] = None
_POLL_INTERVAL_SECONDS = 1.0


def _get_queue():
    """Get or create the upscale queue singleton."""
    global _upscale_queue
    if _upscale_queue is None:
        from metascan.core.upscale_queue_process import ProcessUpscaleQueue
        from metascan.utils.app_paths import get_data_dir

        queue_dir = get_data_dir() / "upscale_queue"
        _upscale_queue = ProcessUpscaleQueue(queue_dir)
        _attach_callbacks(_upscale_queue)
    return _upscale_queue


def _task_payload(task: Any) -> Dict[str, Any]:
    """Convert an UpscaleTask to the JSON payload the frontend expects."""
    return {
        "task_id": task.id,
        "file_path": task.file_path,
        "file_name": Path(task.file_path).name,
        "output_path": task.output_path,
        "file_type": task.file_type,
        "scale": task.scale,
        "model": task.model,
        "face_enhance": task.face_enhance,
        "interpolate_frames": task.interpolate_frames,
        "interpolation_factor": task.interpolation_factor,
        "fps_override": task.fps_override,
        "replace_original": task.replace_original,
        "status": task.status.value,
        "progress": task.progress,
        "error": task.error_message,
        "created_at": task.created_at,
        "last_updated": task.last_updated,
    }


def _attach_callbacks(queue) -> None:
    """Wire queue callbacks to WebSocket broadcasts on the 'upscale' channel."""

    def on_added(task):
        ws_manager.broadcast_sync("upscale", "task_added", _task_payload(task))

    def on_updated(task):
        ws_manager.broadcast_sync("upscale", "task_updated", _task_payload(task))

    def on_removed(task_id: str):
        ws_manager.broadcast_sync("upscale", "task_removed", {"task_id": task_id})

    queue.on_task_added = on_added
    queue.on_task_updated = on_updated
    queue.on_task_removed = on_removed


def _detect_file_type(file_path: str) -> str:
    """Return 'video' or 'image' based on extension."""
    return "video" if Path(file_path).suffix.lower() in _VIDEO_EXTENSIONS else "image"


async def _poll_loop() -> None:
    """Periodically drive the queue: spawn pending workers, gather progress."""
    queue = _get_queue()
    logger.info("Upscale poller started")
    try:
        while True:
            try:
                await asyncio.to_thread(queue.poll_updates)
            except Exception as e:  # don't let one failure kill the loop
                logger.exception(f"Upscale poll_updates failed: {e}")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Upscale poller cancelled")
        raise


def init_upscale_queue() -> None:
    """Startup hook: initialize queue singleton + start the poll loop."""
    global _poll_task
    _get_queue()  # Creates queue and registers callbacks
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_poll_loop())


async def shutdown_upscale_queue() -> None:
    """Shutdown hook: cancel the poller and terminate any workers."""
    global _poll_task
    if _poll_task is not None:
        _poll_task.cancel()
        try:
            await _poll_task
        except (asyncio.CancelledError, Exception):
            pass
        _poll_task = None
    if _upscale_queue is not None:
        try:
            await asyncio.to_thread(_upscale_queue.shutdown)
        except Exception as e:
            logger.warning(f"Error shutting down upscale queue: {e}")


class UpscaleTaskRequest(BaseModel):
    file_path: str
    scale_factor: int = 2
    model_type: str = "general"
    face_enhance: bool = False
    interpolate_frames: bool = False
    fps_multiplier: int = 2
    custom_fps: Optional[float] = None
    replace_original: bool = False
    preserve_metadata: bool = True


class UpscaleSubmitRequest(BaseModel):
    tasks: List[UpscaleTaskRequest]
    concurrent_workers: int = 1


@router.post("")
async def submit_upscale(body: UpscaleSubmitRequest):
    """Add upscale tasks to the queue and start processing."""
    queue = _get_queue()

    # Update worker concurrency (clamped to [1,4] inside the queue)
    queue.max_workers = max(1, min(int(body.concurrent_workers), 4))

    task_ids: List[str] = []
    for task in body.tasks:
        file_type = _detect_file_type(task.file_path)
        task_id = await asyncio.to_thread(
            queue.add_task,
            task.file_path,
            file_type,
            task.scale_factor,
            task.replace_original,
            task.face_enhance,
            task.interpolate_frames,
            task.fps_multiplier,
            task.model_type,
            task.custom_fps,
            task.preserve_metadata,
        )
        task_ids.append(task_id)

    # Kick off any pending workers immediately; the poller also does this on
    # its next tick, but this makes the first worker start without delay.
    try:
        await asyncio.to_thread(queue.start_processing)
    except Exception as e:
        logger.exception(f"Failed to start upscale processing: {e}")

    return {"task_ids": task_ids, "status": "queued"}


@router.get("/queue")
async def get_queue():
    """List all upscale tasks with their status."""
    queue = _get_queue()
    tasks = await asyncio.to_thread(queue.get_all_tasks)
    return {"tasks": [_task_payload(t) for t in tasks]}


@router.patch("/{task_id}")
async def update_task(task_id: str, body: dict):
    """Update a task (cancel/pause/resume)."""
    queue = _get_queue()
    action = body.get("action")
    if action == "cancel":
        ok = await asyncio.to_thread(queue.cancel_task, task_id)
        if not ok:
            raise HTTPException(
                status_code=404, detail=f"Task {task_id} not found or already finished"
            )
        return {"status": "cancelled"}
    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@router.delete("/{task_id}")
async def remove_task(task_id: str):
    """Remove a task from the queue."""
    queue = _get_queue()
    ok = await asyncio.to_thread(queue.remove_task, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"status": "removed"}


@router.post("/pause-all")
async def pause_all():
    """Pause all processing."""
    queue = _get_queue()
    count = await asyncio.to_thread(queue.pause_queue)
    return {"status": "paused", "count": count}


@router.post("/resume-all")
async def resume_all():
    """Resume processing."""
    queue = _get_queue()
    count = await asyncio.to_thread(queue.resume_queue)
    return {"status": "resumed", "count": count}


@router.post("/clear-completed")
async def clear_completed():
    """Clear all completed tasks from the queue."""
    queue = _get_queue()
    await asyncio.to_thread(queue.clear_completed)
    return {"status": "cleared"}
