"""Shared VLM model selection (runner + describe endpoints)."""

from __future__ import annotations

from typing import Any, Optional


class VlmSelectError(RuntimeError):
    """No VLM model is loadable on this hardware."""


def _recommended_vlm_gate() -> Optional[str]:
    from metascan.core.hardware import detect_hardware, feature_gates
    from metascan.core.vlm_models import REGISTRY

    gates = feature_gates(detect_hardware())
    for mid, gate in gates.items():
        if mid in REGISTRY and gate.recommended:
            return mid
    return None


def pick_vlm_model(vlm: Any) -> str:
    """Pick a model id for ``vlm.ensure_started``.

    Prefers whatever the client already has loaded (idempotent restart of
    the same model); otherwise the first hardware-recommended registered
    VLM gate.
    """
    model_id = getattr(vlm, "model_id", None)
    if model_id:
        return str(model_id)
    mid = _recommended_vlm_gate()
    if mid is not None:
        return mid
    raise VlmSelectError("no VLM model available on this hardware")
