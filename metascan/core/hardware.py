"""Hardware detection + tier classification.

Probes CPU / RAM / CUDA / MPS / Vulkan / glibc / NLTK at startup and exposes a
:class:`HardwareReport`. Used by the ``/api/models/hardware`` endpoint and by
:mod:`metascan.core.embedding_manager` to pick a torch device.

All probes are best-effort: if a probe raises, we log at DEBUG and leave the
field at its default. This module never raises on import or on
:func:`detect_hardware` — callers can rely on a populated dataclass.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from functools import lru_cache  # noqa: F401  -- used by detect_hardware() in Task 6
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CudaInfo:
    name: str
    vram_gb: float
    capability: str  # e.g. "8.6"


@dataclass
class VulkanInfo:
    available: bool
    devices: List[str]  # e.g. ["NVIDIA GeForce RTX 3080", "llvmpipe"]
    has_real_device: bool  # True iff at least one non-llvmpipe device is present


@dataclass
class HardwareReport:
    os: str = ""
    machine: str = ""
    python: str = ""
    is_wsl: bool = False
    cpu_count: Optional[int] = None
    ram_gb: Optional[float] = None
    glibc: Optional[str] = None
    cuda: Optional[CudaInfo] = None
    mps: bool = False
    vulkan: Optional[VulkanInfo] = None
    nltk_version: Optional[str] = None
    torch_version: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _platform_info() -> dict:
    is_wsl = False
    try:
        if sys.platform == "linux":
            with open("/proc/version", "rt", encoding="utf-8") as f:
                is_wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    return {
        "os": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "is_wsl": is_wsl,
        "cpu_count": os.cpu_count(),
    }
