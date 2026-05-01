"""Tests for the backend upscale API wiring.

These verify that POST /api/upscale translates the frontend payload into the
correct ProcessUpscaleQueue.add_task arguments and that the queue.json file
gets populated with properly typed fields.
"""

import asyncio
import json

import pytest

from backend.api import upscale as upscale_api
from metascan.core.upscale_queue_process import ProcessUpscaleQueue


@pytest.fixture
def temp_queue(tmp_path, monkeypatch):
    queue_dir = tmp_path / "upscale_queue"
    queue = ProcessUpscaleQueue(queue_dir)
    # Don't actually spawn worker subprocesses during tests
    monkeypatch.setattr(queue, "start_processing", lambda: None)
    monkeypatch.setattr(upscale_api, "_upscale_queue", queue)
    yield queue, queue_dir


def test_submit_persists_correct_fields_for_video_and_image(temp_queue):
    queue, queue_dir = temp_queue

    body = upscale_api.UpscaleSubmitRequest(
        tasks=[
            upscale_api.UpscaleTaskRequest(
                file_path="/media/clip.mp4",
                scale_factor=4,
                model_type="realesr-animevideov3",
                face_enhance=True,
                interpolate_frames=True,
                fps_multiplier=3,
                custom_fps=60.0,
            ),
            upscale_api.UpscaleTaskRequest(
                file_path="/media/photo.png",
                scale_factor=2,
                model_type="general",
            ),
        ],
        concurrent_workers=2,
    )

    result = asyncio.run(upscale_api.submit_upscale(body))

    assert result["status"] == "queued"
    assert len(result["task_ids"]) == 2
    assert queue.max_workers == 2

    with open(queue_dir / "queue.json") as f:
        data = json.load(f)

    tasks = list(data["tasks"].values())
    assert len(tasks) == 2

    video = next(t for t in tasks if t["file_path"].endswith(".mp4"))
    assert video["file_type"] == "video"
    assert video["scale"] == 4
    assert video["model"] == "realesr-animevideov3"
    assert video["face_enhance"] is True
    assert video["interpolate_frames"] is True
    assert video["interpolation_factor"] == 3
    assert video["fps_override"] == 60.0
    assert video["status"] == "pending"

    image = next(t for t in tasks if t["file_path"].endswith(".png"))
    assert image["file_type"] == "image"
    assert image["scale"] == 2
    assert image["model"] == "general"
    assert image["face_enhance"] is False
    assert image["interpolate_frames"] is False


def test_pause_and_resume_use_correct_method_names(temp_queue):
    # Regression: previous code called queue.pause()/resume() which don't exist
    result = asyncio.run(upscale_api.pause_all())
    assert result["status"] == "paused"
    result = asyncio.run(upscale_api.resume_all())
    assert result["status"] == "resumed"


def test_get_queue_payload_uses_frontend_field_names(temp_queue):
    queue, _ = temp_queue
    queue.add_task("/m/test.png", "image", scale=2, model_type="general")

    result = asyncio.run(upscale_api.get_queue())

    assert len(result["tasks"]) == 1
    t = result["tasks"][0]
    # Frontend expects task_id (not id) and file_name
    assert "task_id" in t and t["task_id"].startswith("task_")
    assert t["file_name"] == "test.png"
    assert t["file_type"] == "image"
    assert t["status"] == "pending"


def test_remove_nonexistent_task_returns_404(temp_queue):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(upscale_api.remove_task("task_doesnotexist"))
    assert exc.value.status_code == 404


def test_webm_detected_as_video(temp_queue):
    queue, queue_dir = temp_queue
    body = upscale_api.UpscaleSubmitRequest(
        tasks=[
            upscale_api.UpscaleTaskRequest(file_path="/m/clip.WEBM", scale_factor=2),
        ]
    )
    asyncio.run(upscale_api.submit_upscale(body))
    with open(queue_dir / "queue.json") as f:
        data = json.load(f)
    task = next(iter(data["tasks"].values()))
    assert task["file_type"] == "video"
