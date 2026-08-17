"""Registry of VLM GGUF variants supported by metascan (Qwen3-VL Abliterated
+ Qwen3.8 Abliterated).

Each entry pins a HuggingFace repo + GGUF filename that ships an Abliterated
remix at the chosen quantization. The repos can be overridden at runtime via
``config.models.vlm_repos.<model_id>`` for users who want a different
remix — but the GGUF/mmproj filenames must match. The legacy key
``config.models.qwen3vl_repos.<model_id>`` is still honored (Task 7 wires
the override lookup).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VlmModelSpec:
    """Static metadata for one Qwen3-VL Abliterated GGUF variant.

    Field units:
      - ``approx_vram_gb`` / ``min_vram_gb``: gigabytes (GB).
      - ``parallel_slots``: integer count passed to llama-server's
        ``--parallel`` flag.
      - ``hf_repo``: HuggingFace repo id; can be overridden at runtime
        via ``config.models.qwen3vl_repos.<model_id>``.

    Filename fields:
      - ``gguf_filename``: identical upstream + local (each repo's GGUF
        is uniquely named, so no collision on disk).
      - ``mmproj_filename``: the LOCAL filename. Model-specific (e.g.
        ``mmproj-qwen3vl-4b-F16.gguf``) so multiple VLM weights can
        coexist under ``data/models/vlm/`` without clobbering.
      - ``mmproj_repo_filename``: the filename inside the HF repo. All
        noctrex requantizations name it ``mmproj-F16.gguf``; the
        downloader renames on write to the local form.
      - ``ctx_size``: TOTAL --ctx-size budget, split across
        ``parallel_slots`` (each slot sees ctx_size / parallel_slots).
      - ``extra_args``: extra llama-server argv appended verbatim
        (KV-cache quant, reasoning control, …).
      - ``cuda_gate_vram_gb``: CUDA availability floor for feature_gates;
        None means "use min_vram_gb".
    """

    model_id: str
    display_name: str
    hf_repo: str
    gguf_filename: str
    mmproj_filename: str
    quant: str
    approx_vram_gb: float
    min_vram_gb: float
    parallel_slots: int
    ctx_size: int = 32768
    extra_args: tuple[str, ...] = ()
    cuda_gate_vram_gb: Optional[float] = None
    mmproj_repo_filename: str = "mmproj-F16.gguf"


# Source weights for the qwen3vl-* entries live under the ``huihui-ai``
# namespace as safetensors; noctrex publishes consistent GGUF
# requantizations of the four sizes we target. Verified May 2026. A user
# override via ``config.models.vlm_repos.<model_id>`` (legacy
# ``qwen3vl_repos`` also honored) can replace the repo for any id (drop-in
# remix expected to keep matching filenames). The 27B entry (qwen38-27b)
# ships from chimingw's OrcaRouter GGUF repo instead, with its mmproj
# nested under an ``AUX/`` subdir upstream.
REGISTRY: dict[str, VlmModelSpec] = {
    "qwen3vl-2b": VlmModelSpec(
        model_id="qwen3vl-2b",
        display_name="Qwen3-VL 2B (Abliterated)",
        hf_repo="noctrex/Huihui-Qwen3-VL-2B-Instruct-abliterated-GGUF",
        gguf_filename="Huihui-Qwen3-VL-2B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-qwen3vl-2b-F16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=3.5,
        min_vram_gb=3.0,
        parallel_slots=2,
    ),
    "qwen3vl-4b": VlmModelSpec(
        model_id="qwen3vl-4b",
        display_name="Qwen3-VL 4B (Abliterated)",
        hf_repo="noctrex/Huihui-Qwen3-VL-4B-Instruct-abliterated-GGUF",
        gguf_filename="Huihui-Qwen3-VL-4B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-qwen3vl-4b-F16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=6.0,
        min_vram_gb=5.0,
        parallel_slots=2,
    ),
    "qwen3vl-8b": VlmModelSpec(
        model_id="qwen3vl-8b",
        display_name="Qwen3-VL 8B (Abliterated)",
        hf_repo="noctrex/Huihui-Qwen3-VL-8B-Instruct-abliterated-GGUF",
        gguf_filename="Huihui-Qwen3-VL-8B-Instruct-abliterated-Q5_K_M.gguf",
        mmproj_filename="mmproj-qwen3vl-8b-F16.gguf",
        quant="Q5_K_M",
        approx_vram_gb=9.5,
        min_vram_gb=9.0,
        parallel_slots=4,
        cuda_gate_vram_gb=10.0,
    ),
    "qwen3vl-30b-a3b": VlmModelSpec(
        model_id="qwen3vl-30b-a3b",
        display_name="Qwen3-VL 30B-A3B (Abliterated, MoE)",
        hf_repo="noctrex/Huihui-Qwen3-VL-30B-A3B-Instruct-abliterated-GGUF",
        gguf_filename="Huihui-Qwen3-VL-30B-A3B-Instruct-abliterated-Q4_K_M.gguf",
        mmproj_filename="mmproj-qwen3vl-30b-a3b-F16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=22.0,
        min_vram_gb=20.0,
        parallel_slots=4,
        extra_args=("--cache-type-k", "q8_0", "--cache-type-v", "q8_0"),
        cuda_gate_vram_gb=24.0,
    ),
    "qwen38-27b": VlmModelSpec(
        model_id="qwen38-27b",
        display_name="Qwen3.8 27B (Abliterated)",
        hf_repo="chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF",
        gguf_filename="Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf",
        mmproj_filename="mmproj-qwen38-27b-F16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=20.0,
        min_vram_gb=18.0,
        parallel_slots=4,
        ctx_size=65536,
        # Hybrid Gated DeltaNet model: KV cache is ~64 KB/token (only 16 of
        # 64 layers are full attention), so 65536 ctx costs ~4 GB. Reasoning
        # MUST stay disabled: llama.cpp grammar enforcement is inactive
        # while thinking is enabled (ggml-org/llama.cpp#20345) and every
        # metascan call is grammar-constrained. Jinja chat templating is
        # default-enabled as of b10456, so no explicit --jinja flag is
        # needed. Requires llama.cpp >= b10450 (DeltaNet CUDA fix) — see
        # utils/llama_server.LLAMA_CPP_RELEASE.
        extra_args=("--reasoning", "off"),
        cuda_gate_vram_gb=20.0,
        mmproj_repo_filename="AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf",
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
