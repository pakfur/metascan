"""The writer-training GPU lock as seen by the GPU consumer routes.

Every route that would load or use a model on the GPU (T2I / I2V generate
and Generate Prompt, storyboard synthesis, VLM tagging / retag, chat, the
prompt playground, similarity search / encode, embedding builds, ComfyUI
submits, upscaling) declares ``dependencies=GPU_FREE``: while the training
manager holds the GPU it answers 409 ``{"code": "gpu_busy_training"}``
("GPU busy: training") before the handler runs. Browsing and rating never
take it. The reservation itself lives in ``metascan.core.gpu_guard`` (the
lifespan points it at ``TrainingManager.gpu_locked``)."""

from __future__ import annotations

from typing import Any, List

from fastapi import Depends, HTTPException

from metascan.core.gpu_guard import GPU_BUSY_MESSAGE, GPU_BUSY_TRAINING, gpu_reserved


def gpu_busy_http_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": GPU_BUSY_TRAINING, "message": GPU_BUSY_MESSAGE},
    )


def require_gpu_free() -> None:
    """FastAPI dependency: 409 ``gpu_busy_training`` while training runs."""
    if gpu_reserved():
        raise gpu_busy_http_error()


# ``@router.post(..., dependencies=GPU_FREE)``
GPU_FREE: List[Any] = [Depends(require_gpu_free)]
