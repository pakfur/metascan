"""Helpers that decide how the scanner routes per-file work."""

from __future__ import annotations

from typing import Optional

from metascan.core.hardware import HardwareReport, feature_gates
from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path


def _weights_installed(model_id: str) -> bool:
    spec = REGISTRY[model_id]
    vlm_dir = get_data_dir() / "models" / "vlm"
    return (vlm_dir / spec.gguf_filename).exists() and (
        vlm_dir / spec.mmproj_filename
    ).exists()


def recommended_vlm_model_id(report: HardwareReport) -> Optional[str]:
    """Return the recommended VLM model id for this hardware, or None.

    Iterates ``feature_gates`` and returns the first registered VLM key
    that is marked recommended. Returns None for CPU-only hosts (where no
    VLM size is recommended by ``feature_gates``).
    """
    gates = feature_gates(report)
    for mid, g in gates.items():
        if mid in REGISTRY and g.recommended:
            return mid
    return None


def preferred_vlm_model_id(report: HardwareReport) -> Optional[str]:
    """Model the scan/tag pipelines should actually load.

    The hardware-recommended model when its weights are installed;
    otherwise the largest gate-available model whose weights are on disk
    (an upgraded recommendation must not silently drop a host that has
    yesterday's model installed back to CLIP tagging). None when the tier
    has no recommended model at all (e.g. cpu_only) — mirrors the old
    behavior of never VLM-tagging there regardless of what's on disk.
    """
    recommended = recommended_vlm_model_id(report)
    if recommended is None:
        return None
    if _weights_installed(recommended):
        return recommended

    gates = feature_gates(report)
    installed_available = [
        mid
        for mid, g in gates.items()
        if mid in REGISTRY and g.available and _weights_installed(mid)
    ]
    if not installed_available:
        return None
    return max(installed_available, key=lambda m: REGISTRY[m].min_vram_gb)


def should_tag_with_vlm(report: HardwareReport) -> bool:
    """Return True iff a VLM model this host can run has its weights and the
    llama-server binary installed on disk.

    When False, the scanner falls back to CLIP tagging. This naturally
    returns False on CI / fresh checkouts where neither the llama-server
    binary nor any GGUF weights have been installed yet (see Task 23,
    ``setup_models.py --qwen3vl``).
    """
    if not binary_path().exists():
        return False
    return preferred_vlm_model_id(report) is not None
