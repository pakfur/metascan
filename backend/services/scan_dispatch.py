"""Helpers that decide how the scanner routes per-file work."""

from __future__ import annotations

from typing import Optional

from metascan.core.hardware import HardwareReport, feature_gates
from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path


def should_tag_with_vlm(report: HardwareReport) -> bool:
    """Return True iff a recommended VLM model is available AND its weights +
    binary are installed on disk.

    When False, the scanner falls back to CLIP tagging. This naturally
    returns False on CI / fresh checkouts where neither the llama-server
    binary nor the GGUF weights have been installed yet (see Task 23,
    ``setup_models.py --qwen3vl``).
    """
    if not binary_path().exists():
        return False
    gates = feature_gates(report)
    recommended = [
        mid for mid, g in gates.items() if mid.startswith("qwen3vl-") and g.recommended
    ]
    if not recommended:
        return False
    spec = REGISTRY[recommended[0]]
    vlm_dir = get_data_dir() / "models" / "vlm"
    return (vlm_dir / spec.gguf_filename).exists() and (
        vlm_dir / spec.mmproj_filename
    ).exists()


def recommended_vlm_model_id(report: HardwareReport) -> Optional[str]:
    """Return the recommended VLM model id for this hardware, or None.

    Iterates ``feature_gates`` and returns the first ``qwen3vl-*`` key
    that is marked recommended. Returns None for CPU-only hosts (where no
    VLM size is recommended by ``feature_gates``).
    """
    gates = feature_gates(report)
    for mid, g in gates.items():
        if mid.startswith("qwen3vl-") and g.recommended:
            return mid
    return None
