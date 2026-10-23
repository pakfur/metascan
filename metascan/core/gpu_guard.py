"""Process-wide GPU reservation (writer LoRA training).

While the training manager holds the GPU, nothing else may load a model on
it: no VLM llama-server, no CLIP inference worker, no embedding build. The
backend installs the reservation probe (``set_gpu_reservation``) from the
FastAPI lifespan; the clients consult :func:`gpu_reserved` right before they
spawn, as a last line of defence behind the routes' 409
``gpu_busy_training``.

Stdlib only and free of backend imports (core modules may use it).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The 409 detail code every GPU consumer answers while training holds the GPU.
GPU_BUSY_TRAINING = "gpu_busy_training"
GPU_BUSY_MESSAGE = "GPU busy: training"

_probe: Optional[Callable[[], bool]] = None


class GpuReservedError(RuntimeError):
    """A model load was refused because training holds the GPU."""

    code = GPU_BUSY_TRAINING

    def __init__(self, what: str = "") -> None:
        super().__init__(GPU_BUSY_MESSAGE + (f" ({what})" if what else ""))


def set_gpu_reservation(probe: Optional[Callable[[], bool]]) -> None:
    """Install (or clear, with None) the "training holds the GPU" probe."""
    global _probe
    _probe = probe


def gpu_reserved() -> bool:
    """True while training holds the GPU. A probe that cannot answer counts
    as NOT reserved (the routes and the manager's own state still guard)."""
    probe = _probe
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:
        logger.debug("GPU reservation probe failed", exc_info=True)
        return False
