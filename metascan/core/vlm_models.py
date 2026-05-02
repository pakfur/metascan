"""Registry of Qwen3-VL Abliterated model variants supported by metascan.

Each entry pins a HuggingFace repo + GGUF filename that ships an Abliterated
remix at the chosen quantization. The repos can be overridden at runtime via
``config.models.qwen3vl_repos.<model_id>`` for users who want a different
remix — but the GGUF/mmproj filenames must match.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VlmModelSpec:
    model_id: str
    display_name: str
    hf_repo: str
    gguf_filename: str
    mmproj_filename: str
    quant: str
    approx_vram_gb: float
    min_vram_gb: float
    parallel_slots: int


# Repo / filename strings are the current best-known abliterated publishers.
# Verify these resolve at first download; a user override via
# config.models.qwen3vl_repos can replace the repo for any id.
REGISTRY: dict[str, VlmModelSpec] = {
    "qwen3vl-2b": VlmModelSpec(
        model_id="qwen3vl-2b",
        display_name="Qwen3-VL 2B (Abliterated)",
        hf_repo="huihui-ai/Qwen3-VL-2B-Instruct-abliterated-GGUF",
        gguf_filename="Qwen3-VL-2B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-Qwen3-VL-2B-Instruct-abliterated-f16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=3.5,
        min_vram_gb=3.0,
        parallel_slots=2,
    ),
    "qwen3vl-4b": VlmModelSpec(
        model_id="qwen3vl-4b",
        display_name="Qwen3-VL 4B (Abliterated)",
        hf_repo="huihui-ai/Qwen3-VL-4B-Instruct-abliterated-GGUF",
        gguf_filename="Qwen3-VL-4B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-Qwen3-VL-4B-Instruct-abliterated-f16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=6.0,
        min_vram_gb=5.0,
        parallel_slots=2,
    ),
    "qwen3vl-8b": VlmModelSpec(
        model_id="qwen3vl-8b",
        display_name="Qwen3-VL 8B (Abliterated)",
        hf_repo="huihui-ai/Qwen3-VL-8B-Instruct-abliterated-GGUF",
        gguf_filename="Qwen3-VL-8B-Instruct-abliterated-Q5_K_M.gguf",
        mmproj_filename="mmproj-Qwen3-VL-8B-Instruct-abliterated-f16.gguf",
        quant="Q5_K_M",
        approx_vram_gb=9.5,
        min_vram_gb=9.0,
        parallel_slots=4,
    ),
    "qwen3vl-30b-a3b": VlmModelSpec(
        model_id="qwen3vl-30b-a3b",
        display_name="Qwen3-VL 30B-A3B (Abliterated, MoE)",
        hf_repo="huihui-ai/Qwen3-VL-30B-A3B-Instruct-abliterated-GGUF",
        gguf_filename="Qwen3-VL-30B-A3B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-Qwen3-VL-30B-A3B-Instruct-abliterated-f16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=22.0,
        min_vram_gb=20.0,
        parallel_slots=4,
    ),
}


def get_spec(model_id: str) -> VlmModelSpec:
    """Return the registry entry for ``model_id``. Raises ``KeyError``."""
    return REGISTRY[model_id]


def resolve_repo(model_id: str, override: dict[str, str] | None = None) -> str:
    """Apply optional config override on top of the registry's default repo."""
    if override and model_id in override:
        return override[model_id]
    return REGISTRY[model_id].hf_repo


__all__ = ["VlmModelSpec", "REGISTRY", "get_spec", "resolve_repo"]
