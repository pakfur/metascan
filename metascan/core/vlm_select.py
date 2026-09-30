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


def vlm_model_installed(model_id: str) -> bool:
    """True when ``model_id`` can be spawned: its GGUF and multimodal
    projector are in the models directory and the llama-server binary exists.

    ``pick_vlm_model`` only knows what the hardware recommends, so a fresh
    install on a good GPU is offered a model it does not have yet. An id the
    registry does not know is not blocked: there is nothing to check it
    against, and the client will say what is wrong.
    """
    from metascan.core.vlm_models import REGISTRY
    from metascan.utils.app_paths import get_data_dir
    from metascan.utils.llama_server import binary_path

    spec = REGISTRY.get(model_id)
    if spec is None:
        return True
    vlm_dir = get_data_dir() / "models" / "vlm"
    return (
        (vlm_dir / spec.gguf_filename).exists()
        and (vlm_dir / spec.mmproj_filename).exists()
        and binary_path().exists()
    )
