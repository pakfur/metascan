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


def _ram_gb() -> Optional[float]:
    try:
        import psutil

        return round(float(psutil.virtual_memory().total) / (1024**3), 1)
    except Exception as e:
        logger.debug("ram probe failed: %s", e)
    # Linux fallback when psutil isn't installed
    try:
        if sys.platform == "linux":
            with open("/proc/meminfo", "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
    except (OSError, ValueError):
        pass
    return None


def _glibc_version() -> Optional[str]:
    if sys.platform != "linux":
        return None
    try:
        # confstr is Linux-only and python provides it on glibc systems
        val = os.confstr("CS_GNU_LIBC_VERSION")  # type: ignore[arg-type]
        if val:
            # "glibc 2.31" -> "2.31"
            parts = val.strip().split()
            return parts[-1] if parts else None
    except (ValueError, OSError, AttributeError):
        pass
    return None


def _nltk_version() -> Optional[str]:
    try:
        import nltk

        return getattr(nltk, "__version__", None)
    except Exception:
        return None


def _torch_version() -> Optional[str]:
    try:
        import torch

        return torch.__version__
    except Exception:
        return None


def _try_cuda() -> Optional[CudaInfo]:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        vram_gb = round(props.total_memory / (1024**3), 1)
        cap = f"{props.major}.{props.minor}"
        return CudaInfo(name=name, vram_gb=vram_gb, capability=cap)
    except Exception as e:
        logger.debug("cuda probe failed: %s", e)
        return None


def _mps_available() -> bool:
    try:
        import torch

        return bool(
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
            and torch.backends.mps.is_built()
        )
    except Exception:
        return False
