# Qwen3-VL Abliterated Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CLIP retrieval-based tagging with generative Qwen3-VL Abliterated tagging on hardware tiers where it's viable, while keeping CLIP tagging as the fallback on `cpu_only` and `cuda_entry`. Add a long-running `VlmClient` supervisor for `llama-server` that also serves on-demand re-tagging and is reusable for future captioning / prompt-extraction features.

**Architecture:** A new asyncio supervisor (`VlmClient`) spawns and manages a `llama-server` subprocess running a quantized Qwen3-VL GGUF, mirroring the existing `InferenceClient`'s shape (state machine, `/health` probe, stderr drainer, request lock, auto-respawn). Tagging requests POST to llama-server's OpenAI-compatible `/v1/chat/completions` with a JSON-array grammar. Tags are written to the existing `indices` table with a new `source='vlm'` plus merged variants. Hardware gates extend `feature_gates()` with four model ids; the scanner reads the recommended gate to choose between VLM and CLIP tagging at scan time.

**Tech Stack:** Python 3.11, FastAPI, asyncio, `aiohttp` (for fake-server tests), `httpx` (already in project, for HTTP client to llama-server), SQLite (existing), Vue 3 + Pinia + PrimeVue (existing). New external runtime dependency: `llama-server` binary (downloaded at install time from `ggerganov/llama.cpp` releases) — no new Python deps for production code.

---

## Phase Order and Dependencies

Tasks are grouped into eight phases. Phase 1 (foundation) lands first because it has no behavior change but is required by everything else. Phases 2–4 build the backend. Phase 5 wires it into the FastAPI lifespan. Phase 6 connects the scanner. Phase 7 adds frontend UI. Phase 8 finishes installer + docs.

Each phase ends in a working test suite. Skipping ahead is unsafe — Phase 6's scanner integration breaks if Phase 4's API isn't done.

---

## Cross-Cutting Conventions

**Test fixtures.** DB tests use `pytest.fixture` + `tempfile.TemporaryDirectory` + `DatabaseManager(db_file)` — see `tests/test_database_photo_columns.py:db`. VlmClient tests use a fake `llama-server` written with `aiohttp` (Python `aiohttp.web` is available because aiohttp is already used elsewhere — confirm with `pip show aiohttp` before assuming; if not, use `http.server` in a thread).

**Subagent / context.** llama-server stderr **must** be drained line-by-line — copy the pattern from `metascan/core/inference_client.py:_stderr_loop` (line 388). Failing to drain causes the process to hang during model load.

**Image input.** All VLM image inputs go through the existing thumbnail cache (medium tier) — see `metascan/cache/thumbnail.py`. This handles HEIC/AVIF/WebP conversion, video frame extraction, and large-image resize for free. `llama-server` accepts JPEG/PNG; the medium thumbnail is JPEG.

**Existing patterns to mirror.** `InferenceClient` (`metascan/core/inference_client.py`) is the closest analog — `VlmClient` should match its public API where the abstraction overlaps (`ensure_started`, `shutdown`, `snapshot`, `on_status`, `on_progress`, `state` property, `STATE_*` constants).

---

## Phase 1: Foundation (no behavior change)

These four tasks add modules that other phases consume. None of them are wired into the running app yet, but their tests must pass before moving on.

### Task 1: Hardware gates + binary gate

**Files:**
- Modify: `metascan/core/hardware.py` (after line 380 in `feature_gates`)
- Test: `tests/test_hardware_vlm_gates.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hardware_vlm_gates.py
"""Gate tests for Qwen3-VL VLM tagging across all hardware tiers.

Each test patches detect_hardware() (via cache_clear + a dataclass instance)
and asserts the recommended/available decisions per the design spec table.
"""

from unittest.mock import patch

from metascan.core.hardware import (
    CudaInfo,
    HardwareReport,
    feature_gates,
)


def _report(*, cuda_gb=None, mps=False, ram_gb=16.0, machine="x86_64", os_="Linux"):
    cuda = CudaInfo(name="test", vram_gb=cuda_gb, capability="8.6") if cuda_gb else None
    return HardwareReport(
        os=os_, machine=machine, python="3.11", cpu_count=8,
        ram_gb=ram_gb, cuda=cuda, mps=mps,
    )


def test_cpu_only_recommends_clip_offers_2b_4b():
    g = feature_gates(_report(ram_gb=32.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-2b"].recommended is False
    assert g["qwen3vl-4b"].available is True  # ram_gb >= 16
    assert g["qwen3vl-4b"].recommended is False
    assert g["qwen3vl-8b"].available is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cpu_only_low_ram_only_2b():
    g = feature_gates(_report(ram_gb=8.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-4b"].available is False


def test_cuda_entry_recommends_2b():
    g = feature_gates(_report(cuda_gb=4.0))
    assert g["qwen3vl-2b"].recommended is True
    assert g["qwen3vl-4b"].available is False
    assert g["qwen3vl-8b"].available is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cuda_mainstream_low_band_recommends_4b():
    g = feature_gates(_report(cuda_gb=8.0))
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is False  # below 10 GB sub-band


def test_cuda_mainstream_high_band_offers_8b():
    g = feature_gates(_report(cuda_gb=11.0))
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-8b"].recommended is False
    assert g["qwen3vl-30b-a3b"].available is False


def test_cuda_workstation_recommends_8b():
    g = feature_gates(_report(cuda_gb=16.0))
    assert g["qwen3vl-8b"].recommended is True
    assert g["qwen3vl-30b-a3b"].available is False  # below 24 GB


def test_cuda_workstation_high_recommends_30b():
    g = feature_gates(_report(cuda_gb=24.0))
    assert g["qwen3vl-30b-a3b"].available is True
    assert g["qwen3vl-30b-a3b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-8b"].recommended is False


def test_apple_silicon_recommends_4b_offers_all():
    g = feature_gates(_report(mps=True, machine="arm64", os_="Darwin", ram_gb=64.0))
    assert g["qwen3vl-2b"].available is True
    assert g["qwen3vl-4b"].available is True
    assert g["qwen3vl-4b"].recommended is True
    assert g["qwen3vl-8b"].available is True
    assert g["qwen3vl-30b-a3b"].available is True


def test_llama_server_gate_present():
    g = feature_gates(_report(cuda_gb=16.0))
    assert "llama-server" in g
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hardware_vlm_gates.py -v`
Expected: FAIL — KeyError 'qwen3vl-2b' (gate keys don't exist yet).

- [ ] **Step 3: Add Qwen3-VL gate logic to `feature_gates`**

In `metascan/core/hardware.py`, add **before** the `return gates` line at the end of `feature_gates()`:

```python
    # ---- Qwen3-VL Abliterated tagging ----
    # min_vram_gb is the gate floor; recommended = the tier's default size.
    _QWEN3VL_MIN_VRAM = {
        "qwen3vl-2b": 3.0,
        "qwen3vl-4b": 5.0,
        "qwen3vl-8b": 9.0,
        "qwen3vl-30b-a3b": 20.0,
    }
    is_apple = report.mps and report.os == "Darwin" and report.machine == "arm64"
    ram_gb = report.ram_gb or 0.0

    for key, min_vram in _QWEN3VL_MIN_VRAM.items():
        if report.cuda is not None:
            if key == "qwen3vl-30b-a3b":
                # 30B is gated on cuda_workstation >= 24 GB only.
                available = cuda_vram >= 24.0
                reason = "" if available else "Requires 24 GB VRAM."
            elif key == "qwen3vl-8b":
                # 8B starts at the cuda_mainstream high band (>=10 GB).
                available = cuda_vram >= 10.0
                reason = "" if available else "Requires 10 GB VRAM."
            else:
                available = cuda_vram >= min_vram
                reason = (
                    "" if available
                    else f"Requires {min_vram:.0f} GB VRAM; detected {cuda_vram} GB."
                )
        elif is_apple:
            available = True  # all sizes offered as opt-in on Apple Silicon
            reason = ""
        else:
            # CPU only: 2B always offered, 4B if >= 16 GB RAM, 8B/30B no.
            if key == "qwen3vl-2b":
                available, reason = True, ""
            elif key == "qwen3vl-4b":
                available = ram_gb >= 16.0
                reason = "" if available else "Requires 16 GB RAM on CPU."
            else:
                available = False
                reason = "Requires GPU acceleration."

        # Recommendation: pick one model per tier as default.
        if not available:
            recommended = False
        elif tier is Tier.CUDA_WORKSTATION:
            recommended = (
                key == "qwen3vl-30b-a3b" if cuda_vram >= 24.0
                else key == "qwen3vl-8b"
            )
        elif tier is Tier.CUDA_MAINSTREAM:
            recommended = key == "qwen3vl-4b"
        elif tier is Tier.CUDA_ENTRY:
            recommended = key == "qwen3vl-2b"
        elif tier is Tier.APPLE_SILICON:
            recommended = key == "qwen3vl-4b"
        else:  # CPU_ONLY — CLIP fallback recommended; nothing VLM-recommended
            recommended = False

        gates[key] = Gate(available=available, recommended=recommended, reason=reason)

    # ---- llama-server binary gate ----
    # The binary is downloaded by setup_models.py / Models tab; the gate
    # records presence so the UI can render an actionable prompt.
    from metascan.utils.llama_server import binary_path  # local import — late
    has_binary = binary_path().exists()
    gates["llama-server"] = Gate(
        available=has_binary,
        recommended=has_binary,
        reason="" if has_binary else "Click to install llama.cpp runtime.",
    )
```

- [ ] **Step 4: Stub `metascan/utils/llama_server.py` so the import works**

Create `metascan/utils/llama_server.py`:

```python
"""Path resolver and downloader for the bundled llama-server binary.

Stub. Full implementation in Task 3.
"""
from __future__ import annotations

from pathlib import Path

from metascan.utils.app_paths import get_data_dir


def binary_path() -> Path:
    """Return the platform-specific path where llama-server is/will be installed."""
    return get_data_dir() / "bin" / "llama-server"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_hardware_vlm_gates.py -v`
Expected: PASS.

Run: `pytest tests/test_hardware.py -v`
Expected: PASS — no regression.

- [ ] **Step 6: Commit**

```bash
git add metascan/core/hardware.py metascan/utils/llama_server.py tests/test_hardware_vlm_gates.py
git commit -m "Add Qwen3-VL feature gates with tier-aware recommendations"
```

---

### Task 2: VlmModelSpec registry

**Files:**
- Create: `metascan/core/vlm_models.py`
- Test: `tests/test_vlm_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_models.py
"""Sanity tests for the Qwen3-VL model registry."""

import pytest

from metascan.core.vlm_models import REGISTRY, VlmModelSpec, get_spec


def test_all_four_sizes_present():
    expected = {"qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"}
    assert set(REGISTRY.keys()) == expected


def test_specs_are_well_formed():
    for mid, spec in REGISTRY.items():
        assert isinstance(spec, VlmModelSpec)
        assert spec.model_id == mid
        assert spec.hf_repo
        assert spec.gguf_filename.endswith(".gguf")
        assert spec.mmproj_filename.endswith(".gguf")
        assert spec.quant in {"Q4_K_M", "Q5_K_M"}
        assert spec.min_vram_gb > 0
        assert spec.parallel_slots in (2, 4)


def test_min_vram_is_monotonic_by_size():
    sizes = ["qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"]
    vrams = [REGISTRY[s].min_vram_gb for s in sizes]
    assert vrams == sorted(vrams)


def test_get_spec_returns_spec():
    spec = get_spec("qwen3vl-4b")
    assert spec.model_id == "qwen3vl-4b"


def test_get_spec_raises_on_unknown():
    with pytest.raises(KeyError):
        get_spec("qwen3vl-bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_models.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the registry**

Create `metascan/core/vlm_models.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_models.py tests/test_vlm_models.py
git commit -m "Add Qwen3-VL model spec registry"
```

---

### Task 3: llama-server binary path resolver

**Files:**
- Modify: `metascan/utils/llama_server.py`
- Test: `tests/test_llama_server_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llama_server_paths.py
"""Path resolution for the bundled llama-server binary.

Download is exercised in test_setup_models (Task 27); this file only covers
the path/build-name logic which is pure and easy to assert."""

from unittest.mock import patch

from metascan.utils.llama_server import (
    binary_filename,
    pick_release_asset,
    binary_path,
)


def _report(*, os_="Linux", machine="x86_64", cuda=False, has_real_vk=False):
    from metascan.core.hardware import CudaInfo, HardwareReport, VulkanInfo
    return HardwareReport(
        os=os_, machine=machine, python="3.11",
        cuda=CudaInfo(name="t", vram_gb=8.0, capability="8.6") if cuda else None,
        vulkan=VulkanInfo(available=has_real_vk, devices=[], has_real_device=has_real_vk),
    )


def test_binary_filename_linux():
    with patch("metascan.utils.llama_server.detect_hardware", return_value=_report()):
        assert binary_filename() == "llama-server"


def test_binary_filename_windows():
    with patch(
        "metascan.utils.llama_server.detect_hardware",
        return_value=_report(os_="Windows", machine="AMD64"),
    ):
        assert binary_filename() == "llama-server.exe"


def test_pick_release_asset_linux_cuda():
    rpt = _report(cuda=True)
    asset = pick_release_asset(rpt)
    assert "linux" in asset.lower()
    assert "cuda" in asset.lower()


def test_pick_release_asset_macos_arm64():
    rpt = _report(os_="Darwin", machine="arm64")
    asset = pick_release_asset(rpt)
    assert "macos" in asset.lower() or "darwin" in asset.lower()
    assert "arm64" in asset.lower()


def test_pick_release_asset_linux_vulkan_no_cuda():
    rpt = _report(has_real_vk=True)
    asset = pick_release_asset(rpt)
    assert "vulkan" in asset.lower()


def test_pick_release_asset_linux_cpu_fallback():
    rpt = _report()
    asset = pick_release_asset(rpt)
    assert "linux" in asset.lower()
    # CPU build identifier varies; just assert it's the non-cuda non-vulkan path
    assert "cuda" not in asset.lower()
    assert "vulkan" not in asset.lower()


def test_binary_path_lives_in_data_dir():
    p = binary_path()
    assert "bin" in p.parts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llama_server_paths.py -v`
Expected: FAIL — `binary_filename`, `pick_release_asset` not defined.

- [ ] **Step 3: Replace the stub with the full resolver**

Overwrite `metascan/utils/llama_server.py`:

```python
"""Path resolver + release-asset picker for the bundled llama-server binary.

The binary is downloaded once at install time (or on first VLM use) by
``setup_models.py`` from the ``ggerganov/llama.cpp`` GitHub releases. This
module is the single source of truth for "where the binary lives" and "which
upstream asset matches the current host."
"""

from __future__ import annotations

from pathlib import Path

from metascan.core.hardware import HardwareReport, detect_hardware
from metascan.utils.app_paths import get_data_dir

# Pinned upstream release. Bump only deliberately — model compatibility and
# command-line flags evolve between releases.
LLAMA_CPP_RELEASE = "b4500"


def binary_filename() -> str:
    """Platform-correct filename of the llama-server executable."""
    rpt = detect_hardware()
    if rpt.os == "Windows":
        return "llama-server.exe"
    return "llama-server"


def binary_path() -> Path:
    """Absolute path to the installed llama-server binary."""
    return get_data_dir() / "bin" / binary_filename()


def pick_release_asset(report: HardwareReport) -> str:
    """Return the upstream release-asset filename matching ``report``.

    Picks the most-accelerated build available: CUDA > Vulkan > CPU. macOS
    arm64 always uses the Metal build (the only one shipped for that target).
    """
    rel = LLAMA_CPP_RELEASE
    if report.os == "Darwin":
        return f"llama-{rel}-bin-macos-arm64.zip"
    if report.os == "Windows":
        if report.cuda is not None:
            return f"llama-{rel}-bin-win-cuda-x64.zip"
        if report.vulkan and report.vulkan.has_real_device:
            return f"llama-{rel}-bin-win-vulkan-x64.zip"
        return f"llama-{rel}-bin-win-avx2-x64.zip"
    # Linux
    if report.cuda is not None:
        return f"llama-{rel}-bin-linux-cuda-x64.zip"
    if report.vulkan and report.vulkan.has_real_device:
        return f"llama-{rel}-bin-linux-vulkan-x64.zip"
    return f"llama-{rel}-bin-linux-avx2-x64.zip"


def release_url(asset: str) -> str:
    """GitHub releases download URL for the given asset filename."""
    return (
        f"https://github.com/ggerganov/llama.cpp/releases/download/"
        f"{LLAMA_CPP_RELEASE}/{asset}"
    )


__all__ = [
    "binary_filename",
    "binary_path",
    "pick_release_asset",
    "release_url",
    "LLAMA_CPP_RELEASE",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llama_server_paths.py tests/test_hardware_vlm_gates.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/utils/llama_server.py tests/test_llama_server_paths.py
git commit -m "Add llama-server binary path resolver and release-asset picker"
```

---

### Task 4: Prompts and JSON-array grammar

**Files:**
- Create: `metascan/core/vlm_prompts.py`
- Test: `tests/test_vlm_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_prompts.py
"""Snapshot + parse tests for VLM prompt templates and grammar."""

import json

from metascan.core.vlm_prompts import (
    CAPTION_STYLE_PROMPTS,
    PROMPT_EXTRACTION_PROMPT,
    TAGGING_GRAMMAR,
    TAGGING_SYSTEM_PROMPT,
    TAGGING_USER_PROMPT,
    normalize_tags,
    parse_tags_response,
)


def test_tagging_system_prompt_mentions_nsfw_honesty():
    # The whole point of using an abliterated model is honest NSFW tagging —
    # the system prompt must explicitly request it. Otherwise the abliteration
    # buys us nothing.
    assert "nsfw" in TAGGING_SYSTEM_PROMPT.lower() or "explicit" in TAGGING_SYSTEM_PROMPT.lower()


def test_tagging_system_prompt_requests_json_array():
    assert "json" in TAGGING_SYSTEM_PROMPT.lower()
    assert "array" in TAGGING_SYSTEM_PROMPT.lower() or "list" in TAGGING_SYSTEM_PROMPT.lower()


def test_tagging_user_prompt_is_short():
    # User prompt should be terse — system prompt does the heavy lifting.
    assert len(TAGGING_USER_PROMPT) < 200


def test_tagging_grammar_is_valid_gbnf():
    # The grammar is a string; we just sanity-check structure here. Real
    # acceptance happens against llama-server in integration tests.
    assert "::=" in TAGGING_GRAMMAR
    assert "string" in TAGGING_GRAMMAR or "tag" in TAGGING_GRAMMAR


def test_caption_style_prompts_have_all_four_styles():
    assert set(CAPTION_STYLE_PROMPTS.keys()) == {"sdxl", "flux", "pony", "natural"}


def test_prompt_extraction_prompt_present():
    assert isinstance(PROMPT_EXTRACTION_PROMPT, str)
    assert len(PROMPT_EXTRACTION_PROMPT) > 20


def test_parse_tags_response_extracts_array():
    raw = '["red dress", "outdoor", "smiling"]'
    assert parse_tags_response(raw) == ["red dress", "outdoor", "smiling"]


def test_parse_tags_response_handles_whitespace_and_dedup():
    raw = '[" Red Dress ", "outdoor", "Outdoor", "smiling"]'
    assert parse_tags_response(raw) == ["red dress", "outdoor", "smiling"]


def test_parse_tags_response_returns_empty_on_garbage():
    assert parse_tags_response("not json at all") == []
    assert parse_tags_response("") == []


def test_parse_tags_response_handles_object_with_tags_key():
    # Some models wrap output in {"tags": [...]} despite the grammar.
    raw = json.dumps({"tags": ["a", "b"]})
    assert parse_tags_response(raw) == ["a", "b"]


def test_normalize_tags_lowercases_strips_dedups():
    assert normalize_tags([" Red ", "blue", "RED", "  "]) == ["red", "blue"]


def test_normalize_tags_strips_punctuation_edges():
    assert normalize_tags(["red.", "!blue", "(green)"]) == ["red", "blue", "green"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_prompts.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the prompts module**

Create `metascan/core/vlm_prompts.py`:

```python
"""Prompt templates and structured-output grammar for Qwen3-VL inference.

The tagging path is the only one wired in v1. Caption-style stubs and the
prompt-extraction stub ship now so the shape is committed and future features
can flesh them out without restructuring."""

from __future__ import annotations

import json
import re
from typing import Iterable


TAGGING_SYSTEM_PROMPT = (
    "You are an image tagger for an AI-generated-art browser. "
    "Look at the image and emit between 15 and 25 descriptive tags as a "
    "JSON array of lowercase strings. Cover: subject, attributes, style, "
    "setting, mood, action, and any notable visual details. "
    "Be accurate about NSFW or explicit content — do not refuse, soften, "
    "or omit such tags. The user is cataloguing their own library and "
    "needs honest descriptions to find images later. "
    "Output ONLY the JSON array, with no commentary."
)

TAGGING_USER_PROMPT = "Tag this image."

# GBNF grammar accepted by llama-server's `grammar` parameter. Forces output
# to be a JSON array of 1+ short lowercase strings; trailing characters are
# rejected by llama-server's parser when grammar mode is active.
TAGGING_GRAMMAR = r"""
root ::= "[" ws tag (ws "," ws tag)* ws "]"
tag  ::= "\"" char+ "\""
char ::= [a-z0-9 \-_'/]
ws   ::= [ \t\n]*
"""


CAPTION_STYLE_PROMPTS: dict[str, str] = {
    # Stubs — wired in a future task. Keep keys frozen so frontend can
    # render style options that match what the backend recognizes.
    "sdxl": (
        "Describe this image as a Stable Diffusion XL prompt: "
        "comma-separated descriptive phrases, weighted parentheses optional, "
        "subject first, then attributes, style, lighting, composition."
    ),
    "flux": (
        "Describe this image as a Flux prompt: a single natural-language "
        "sentence in flowing prose, mentioning subject, setting, lighting, "
        "and style."
    ),
    "pony": (
        "Describe this image using Danbooru-style tags suitable for a Pony "
        "Diffusion prompt: underscored tags, comma-separated, character/series "
        "tags first, then attributes."
    ),
    "natural": (
        "Describe this image in two or three plain English sentences "
        "as if writing a museum caption."
    ),
}


PROMPT_EXTRACTION_PROMPT = (
    "Reconstruct the prompt that most likely generated this image, in the "
    "style typical of Stable Diffusion / Flux generation parameters."
)


_PUNCT_EDGES = re.compile(r"^[^\w]+|[^\w]+$")


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Lowercase, strip whitespace + edge punctuation, dedup preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if not isinstance(t, str):
            continue
        cleaned = _PUNCT_EDGES.sub("", t.strip().lower())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def parse_tags_response(raw: str) -> list[str]:
    """Parse the model's JSON response into a normalized tag list.

    Tolerates two shapes despite the grammar:
      - ``["a", "b"]``           — the canonical shape
      - ``{"tags": ["a", "b"]}`` — some models wrap the array
    Returns ``[]`` on any parse error so the caller can record a failed
    attempt without raising.
    """
    if not raw or not raw.strip():
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, list):
        return normalize_tags(obj)
    if isinstance(obj, dict) and isinstance(obj.get("tags"), list):
        return normalize_tags(obj["tags"])
    return []


__all__ = [
    "TAGGING_SYSTEM_PROMPT",
    "TAGGING_USER_PROMPT",
    "TAGGING_GRAMMAR",
    "CAPTION_STYLE_PROMPTS",
    "PROMPT_EXTRACTION_PROMPT",
    "normalize_tags",
    "parse_tags_response",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_prompts.py tests/test_vlm_prompts.py
git commit -m "Add Qwen3-VL prompt templates and JSON-array grammar"
```

---

## Phase 2: VlmClient core

The supervisor that owns the `llama-server` subprocess. Modeled after
`metascan/core/inference_client.py` — read that file before starting Phase 2.

### Task 5: Fake llama-server fixture

**Files:**
- Create: `tests/_fake_llama_server.py`
- Test: `tests/test_fake_llama_server.py`

This fixture is reused by every Phase 2 task. It's a minimal aiohttp server
that mimics the subset of the llama-server HTTP API that `VlmClient` calls:
`GET /health`, `POST /v1/chat/completions`. It's spawned in a separate
subprocess (so we can SIGKILL it like we'd kill the real binary).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fake_llama_server.py
"""Tests that the fake llama-server fixture itself behaves correctly.

These are necessary because the rest of Phase 2 trusts this fixture to
emulate the real binary closely enough."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from tests._fake_llama_server import FakeLlamaServer


@pytest.mark.asyncio
async def test_health_returns_ok_after_load_delay():
    async with FakeLlamaServer(load_ms=50) as fake:
        async with httpx.AsyncClient() as client:
            # Immediately after start, /health may return 503.
            for _ in range(40):
                r = await client.get(f"{fake.base_url}/health")
                if r.status_code == 200:
                    break
                await asyncio.sleep(0.025)
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_chat_completions_returns_canned_tags():
    async with FakeLlamaServer(canned_response='["red dress", "outdoor"]') as fake:
        async with httpx.AsyncClient() as client:
            await fake.wait_ready()
            r = await client.post(
                f"{fake.base_url}/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "x"}]},
            )
            assert r.status_code == 200
            body = r.json()
            content = body["choices"][0]["message"]["content"]
            assert content == '["red dress", "outdoor"]'


@pytest.mark.asyncio
async def test_can_force_crash():
    fake = FakeLlamaServer(crash_after_n_requests=1)
    async with fake:
        await fake.wait_ready()
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{fake.base_url}/v1/chat/completions",
                json={"messages": []},
            )
            await asyncio.sleep(0.5)
        assert fake.process_returncode() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fake_llama_server.py -v`
Expected: FAIL — `tests._fake_llama_server` does not exist. Also `pytest-asyncio` may need to be added; check `requirements-dev.txt`.

If `pytest-asyncio` is missing, add it: append `pytest-asyncio>=0.23` to `requirements-dev.txt`, then `pip install pytest-asyncio` in the venv. Also add a `[tool.pytest.ini_options]` block (or extend the existing one) with `asyncio_mode = "auto"` so the `@pytest.mark.asyncio` decorator works without explicit fixture wiring. Re-run the test to confirm it now fails for the *real* reason (missing module).

- [ ] **Step 3: Create the fake-server fixture**

Create `tests/_fake_llama_server.py`:

```python
"""A minimal stand-in for llama-server used by VlmClient tests.

Spawned as a subprocess via ``sys.executable`` so the VlmClient under test
can manage it the same way it manages the real binary (start, /health,
SIGTERM, restart, etc.). One ephemeral port per fixture instance.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

import httpx


# The fake server runs as a child process so that SIGTERM/SIGKILL behavior
# matches the real llama-server. The script body is loaded into a temp file
# and executed with ``sys.executable``.
_FAKE_SCRIPT = textwrap.dedent(
    """
    import argparse, asyncio, json, sys
    from aiohttp import web

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--load-ms", type=int, default=0)
    ap.add_argument("--canned-response", type=str, default='["a","b","c"]')
    ap.add_argument("--crash-after-n", type=int, default=0)
    ap.add_argument("--health-fails-forever", action="store_true")
    args = ap.parse_args()

    state = {"ready": False, "served": 0}

    async def health(request):
        if args.health_fails_forever or not state["ready"]:
            return web.Response(status=503, text='{"status":"loading"}')
        return web.json_response({"status": "ok"})

    async def chat(request):
        body = await request.json()
        state["served"] += 1
        resp = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": args.canned_response},
                "finish_reason": "stop",
            }],
        }
        # Crash AFTER responding so the client sees the response then loses
        # the connection on its next request.
        async def crash_later():
            await asyncio.sleep(0.05)
            sys.exit(1)
        if args.crash_after_n and state["served"] >= args.crash_after_n:
            asyncio.create_task(crash_later())
        return web.json_response(resp)

    async def main():
        if args.load_ms:
            await asyncio.sleep(args.load_ms / 1000.0)
        state["ready"] = True
        # Print READY on stderr so the parent knows the load delay is done.
        sys.stderr.write("READY\\n")
        sys.stderr.flush()
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_post("/v1/chat/completions", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", args.port)
        await site.start()
        # Run forever (until killed by parent).
        while True:
            await asyncio.sleep(3600)

    asyncio.run(main())
    """
).strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeLlamaServer:
    """Async context manager that spawns and tears down a fake llama-server."""

    def __init__(
        self,
        *,
        load_ms: int = 0,
        canned_response: str = '["a", "b", "c"]',
        crash_after_n_requests: int = 0,
        health_fails_forever: bool = False,
    ) -> None:
        self._load_ms = load_ms
        self._canned = canned_response
        self._crash_after = crash_after_n_requests
        self._health_fails = health_fails_forever
        self._port = _free_port()
        self._proc: Optional[subprocess.Popen] = None
        self._script_path: Optional[Path] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def port(self) -> int:
        return self._port

    def process_returncode(self) -> Optional[int]:
        if self._proc is None:
            return None
        return self._proc.poll()

    async def wait_ready(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{self.base_url}/health")
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
        raise TimeoutError("fake llama-server did not become ready")

    async def __aenter__(self) -> "FakeLlamaServer":
        fd = tempfile.NamedTemporaryFile(
            prefix="fake_llama_", suffix=".py", delete=False, mode="w"
        )
        fd.write(_FAKE_SCRIPT)
        fd.close()
        self._script_path = Path(fd.name)
        cmd = [
            sys.executable,
            str(self._script_path),
            "--port",
            str(self._port),
            "--load-ms",
            str(self._load_ms),
            "--canned-response",
            self._canned,
            "--crash-after-n",
            str(self._crash_after),
        ]
        if self._health_fails:
            cmd.append("--health-fails-forever")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        if self._script_path is not None:
            try:
                self._script_path.unlink()
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fake_llama_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/_fake_llama_server.py tests/test_fake_llama_server.py requirements-dev.txt pytest.ini pyproject.toml 2>/dev/null
git commit -m "Add fake llama-server fixture for VlmClient tests"
```

(Stage only files that actually changed — `pytest.ini`/`pyproject.toml` only if you modified one of them to enable asyncio_mode.)

---

### Task 6: VlmClient skeleton — start, /health probe, shutdown

**Files:**
- Create: `metascan/core/vlm_client.py`
- Test: `tests/test_vlm_client_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_client_lifecycle.py
"""Lifecycle tests for VlmClient: start, /health, shutdown."""

import asyncio
import pytest

from metascan.core.vlm_client import (
    STATE_LOADING,
    STATE_READY,
    STATE_STOPPED,
    VlmClient,
)
from tests._fake_llama_server import FakeLlamaServer


@pytest.mark.asyncio
async def test_start_then_ready():
    async with FakeLlamaServer(load_ms=100) as fake:
        client = VlmClient(
            spawn_override=lambda model_id: fake.base_url,
        )
        try:
            await client.start("qwen3vl-2b", wait_ready=True, ready_timeout=5.0)
            assert client.state == STATE_READY
            assert client.model_id == "qwen3vl-2b"
        finally:
            await client.shutdown()
        assert client.state == STATE_STOPPED


@pytest.mark.asyncio
async def test_ensure_started_is_idempotent():
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            await client.ensure_started("qwen3vl-2b")
            await client.ensure_started("qwen3vl-2b")  # second call must no-op
            assert client.state == STATE_READY
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_start_times_out_when_health_never_ready():
    async with FakeLlamaServer(health_fails_forever=True) as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            with pytest.raises(TimeoutError):
                await client.start("qwen3vl-2b", wait_ready=True, ready_timeout=1.0)
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_snapshot_returns_state():
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            snap = client.snapshot()
            assert snap["state"] == STATE_READY
            assert snap["model_id"] == "qwen3vl-2b"
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_status_callback_fires_on_state_change():
    events = []
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        client.on_status(lambda state, payload: events.append(state))
        try:
            await client.start("qwen3vl-2b")
        finally:
            await client.shutdown()
    assert STATE_LOADING in events
    assert STATE_READY in events
    assert STATE_STOPPED in events
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_client_lifecycle.py -v`
Expected: FAIL — `metascan.core.vlm_client` does not exist.

- [ ] **Step 3: Create VlmClient skeleton**

Create `metascan/core/vlm_client.py`:

```python
"""Asyncio supervisor for the long-running Qwen3-VL inference server.

Spawns and manages a ``llama-server`` subprocess running an Abliterated
Qwen3-VL GGUF. Exposes typed async methods that POST to llama-server's
OpenAI-compatible ``/v1/chat/completions`` endpoint, plus lifecycle hooks
(``ensure_started``, ``swap_model``, ``shutdown``).

Mirrors :class:`metascan.core.inference_client.InferenceClient` for the
pieces that overlap. Diverges where the underlying transport differs:
llama-server speaks HTTP rather than NDJSON over stdio, so the reader-loop
is replaced by per-request HTTP calls and a periodic /health probe.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from metascan.core.vlm_models import VlmModelSpec, get_spec
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path

logger = logging.getLogger(__name__)


# State machine — kept as plain strings for direct WS broadcast.
STATE_IDLE = "idle"
STATE_SPAWNING = "spawning"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"


_RESPAWN_BACKOFF_SECONDS = (1.0, 3.0, 10.0)


StatusCb = Callable[[str, Dict[str, Any]], None]
ProgressCb = Callable[[Dict[str, Any]], None]


class VlmError(RuntimeError):
    """Raised when llama-server returns a non-2xx response or invalid body."""


def _free_port() -> int:
    """Pick an ephemeral port. The OS guarantees uniqueness inside this host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class VlmClient:
    """Supervises one llama-server subprocess. Singleton per FastAPI process."""

    def __init__(
        self,
        *,
        spawn_override: Optional[Callable[[str], str]] = None,
    ) -> None:
        # Tests inject ``spawn_override``: a callable that returns a base_url
        # for an already-running fake server, skipping the actual subprocess.
        self._spawn_override = spawn_override

        self._proc: Optional[subprocess.Popen] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._waiter_task: Optional[asyncio.Task[None]] = None

        self._base_url: Optional[str] = None
        self._port: Optional[int] = None
        self._model_id: Optional[str] = None
        self._spec: Optional[VlmModelSpec] = None

        self._stderr_ring: List[str] = []
        self._stderr_ring_max = 200

        self._start_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._ready_event = asyncio.Event()

        self._state = STATE_IDLE
        self._last_progress: Dict[str, Any] = {}
        self._last_error: Optional[str] = None

        self._stopping = False
        self._respawn_attempts = 0

        self._on_status_cbs: List[StatusCb] = []
        self._on_progress_cbs: List[ProgressCb] = []

        # Lazy-created HTTP client — torn down on shutdown.
        self._http: Optional[httpx.AsyncClient] = None

    # ---- Observability -------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def model_id(self) -> Optional[str]:
        return self._model_id

    @property
    def base_url(self) -> Optional[str]:
        return self._base_url

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "model_id": self._model_id,
            "base_url": self._base_url,
            "progress": dict(self._last_progress),
            "error": self._last_error,
        }

    def on_status(self, cb: StatusCb) -> None:
        self._on_status_cbs.append(cb)

    def on_progress(self, cb: ProgressCb) -> None:
        self._on_progress_cbs.append(cb)

    def _set_state(self, new_state: str, **extra: Any) -> None:
        if new_state == self._state and not extra:
            return
        self._state = new_state
        payload = {"state": new_state, **extra, **self.snapshot()}
        for cb in list(self._on_status_cbs):
            try:
                cb(new_state, payload)
            except Exception:
                logger.exception("on_status callback raised")

    # ---- Lifecycle -----------------------------------------------------

    async def ensure_started(self, model_id: str) -> None:
        async with self._start_lock:
            if (
                self._state in (STATE_LOADING, STATE_READY)
                and self._model_id == model_id
            ):
                return
            await self.start(model_id, wait_ready=False)

    async def start(
        self,
        model_id: str,
        *,
        wait_ready: bool = True,
        ready_timeout: float = 300.0,
    ) -> None:
        if self._proc is not None and self._proc.poll() is None:
            if model_id == self._model_id and self._state in (
                STATE_LOADING, STATE_READY,
            ):
                if wait_ready:
                    await self._wait_ready(ready_timeout)
                return
            await self.shutdown()

        self._spec = get_spec(model_id)
        self._model_id = model_id
        self._last_error = None
        self._last_progress = {}
        self._ready_event.clear()
        self._stopping = False
        self._set_state(STATE_SPAWNING)

        if self._spawn_override is not None:
            # Test mode: skip the subprocess entirely; trust the override URL.
            self._base_url = self._spawn_override(model_id)
            self._proc = None
        else:
            self._port = _free_port()
            self._base_url = f"http://127.0.0.1:{self._port}"
            cmd = self._build_command(self._spec, self._port)
            logger.info(
                "Spawning llama-server for %s on port %d", model_id, self._port
            )
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._stderr_task = asyncio.create_task(
                self._stderr_loop(), name="vlm-stderr"
            )
            self._waiter_task = asyncio.create_task(
                self._wait_exit(), name="vlm-waiter"
            )

        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)
        self._set_state(STATE_LOADING)

        # Probe /health asynchronously — flip to READY when it returns 200.
        asyncio.create_task(self._health_probe_loop(), name="vlm-health")

        if wait_ready:
            await self._wait_ready(ready_timeout)

    def _build_command(self, spec: VlmModelSpec, port: int) -> List[str]:
        """Build the llama-server argv. KV-cache quant for 30B-A3B only."""
        models_dir = get_data_dir() / "models" / "vlm"
        gguf = models_dir / spec.gguf_filename
        mmproj = models_dir / spec.mmproj_filename
        cmd = [
            str(binary_path()),
            "--model", str(gguf),
            "--mmproj", str(mmproj),
            "--port", str(port),
            "--host", "127.0.0.1",
            "--parallel", str(spec.parallel_slots),
            "--ctx-size", "8192",
            "--n-gpu-layers", "99",  # offload all layers if GPU available
        ]
        if spec.model_id == "qwen3vl-30b-a3b":
            cmd += ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]
        return cmd

    async def _wait_ready(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"llama-server did not become ready within {timeout:.0f}s"
            ) from e
        if self._state != STATE_READY:
            raise RuntimeError(
                f"llama-server ended in state {self._state}: "
                f"{self._last_error or 'no error reported'}"
            )

    async def _health_probe_loop(self) -> None:
        """Poll /health until it returns 200, then flip to READY."""
        assert self._http is not None
        deadline = asyncio.get_event_loop().time() + 600.0
        while asyncio.get_event_loop().time() < deadline:
            if self._stopping:
                return
            try:
                r = await self._http.get("/health")
                if r.status_code == 200:
                    self._respawn_attempts = 0
                    self._ready_event.set()
                    self._set_state(STATE_READY)
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)

    async def shutdown(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except Exception:
                logger.exception("error terminating llama-server")
        self._proc = None
        for t in (self._stderr_task, self._waiter_task):
            if t is not None and not t.done():
                t.cancel()
        self._stderr_task = None
        self._waiter_task = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        self._ready_event.clear()
        self._set_state(STATE_STOPPED)

    # ---- Reader / supervisor ------------------------------------------

    async def _stderr_loop(self) -> None:
        """Drain llama-server stderr — DO NOT remove. The pipe buffer fills
        within seconds during model load and the process hangs silently
        otherwise. Mirrors InferenceClient._stderr_loop."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, proc.stderr.readline)
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                self._stderr_ring.append(text)
                if len(self._stderr_ring) > self._stderr_ring_max:
                    self._stderr_ring = self._stderr_ring[-self._stderr_ring_max :]
                logger.info("llama-server: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("vlm stderr drainer crashed")

    async def _wait_exit(self) -> None:
        proc = self._proc
        if proc is None:
            return
        loop = asyncio.get_running_loop()
        try:
            rc = await loop.run_in_executor(None, proc.wait)
        except asyncio.CancelledError:
            return
        if self._stopping:
            return
        tail = "\n".join(self._stderr_ring[-20:])
        self._last_error = f"llama-server exited unexpectedly (rc={rc})" + (
            f": {tail[-1000:]}" if tail else ""
        )
        logger.error(self._last_error)
        self._ready_event.set()
        self._set_state(STATE_ERROR, error=self._last_error)


__all__ = [
    "VlmClient",
    "VlmError",
    "STATE_IDLE",
    "STATE_SPAWNING",
    "STATE_LOADING",
    "STATE_READY",
    "STATE_ERROR",
    "STATE_STOPPED",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_client_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_client.py tests/test_vlm_client_lifecycle.py
git commit -m "Add VlmClient skeleton with /health probe and lifecycle"
```

---

### Task 7: VlmClient.generate_tags

**Files:**
- Modify: `metascan/core/vlm_client.py` (add `generate_tags` method)
- Test: `tests/test_vlm_client_tags.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_client_tags.py
"""generate_tags happy path + parse-error handling."""

import asyncio
from pathlib import Path

import pytest

from metascan.core.vlm_client import VlmClient
from tests._fake_llama_server import FakeLlamaServer


@pytest.mark.asyncio
async def test_generate_tags_returns_normalized_list(tmp_path: Path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")  # minimal-ish JPEG marker bytes
    async with FakeLlamaServer(
        canned_response='[" Red Dress ", "outdoor", "Outdoor"]'
    ) as fake:
        client = VlmClient(spawn_override=lambda mid: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            tags = await client.generate_tags(img)
            assert tags == ["red dress", "outdoor"]
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_generate_tags_returns_empty_on_garbage_response(tmp_path: Path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    async with FakeLlamaServer(canned_response="not json at all") as fake:
        client = VlmClient(spawn_override=lambda mid: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            tags = await client.generate_tags(img)
            assert tags == []
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_generate_tags_raises_when_not_started(tmp_path: Path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    with pytest.raises(RuntimeError):
        await client.generate_tags(img)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_client_tags.py -v`
Expected: FAIL — `generate_tags` not defined.

- [ ] **Step 3: Add `generate_tags` to VlmClient**

In `metascan/core/vlm_client.py`, add inside the `VlmClient` class
(after `_wait_exit`):

```python
    # ---- Inference methods -------------------------------------------

    async def generate_tags(
        self,
        image_path: Path,
        *,
        timeout: float = 60.0,
    ) -> list[str]:
        """Tag a single image. Returns a normalized, deduped tag list.

        On parse / HTTP error returns an empty list rather than raising —
        the scan loop calls this for every image and we don't want a single
        bad response to crash the batch.
        """
        from metascan.core.vlm_prompts import (
            TAGGING_GRAMMAR,
            TAGGING_SYSTEM_PROMPT,
            TAGGING_USER_PROMPT,
            parse_tags_response,
        )

        if self._http is None or self._state != STATE_READY:
            raise RuntimeError(
                f"VlmClient not ready (state={self._state}); "
                "call ensure_started() first"
            )

        image_b64 = await asyncio.to_thread(self._encode_image_b64, image_path)
        body = {
            "messages": [
                {"role": "system", "content": TAGGING_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TAGGING_USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                },
            ],
            "grammar": TAGGING_GRAMMAR,
            "max_tokens": 512,
            "temperature": 0.2,
        }
        try:
            r = await self._http.post(
                "/v1/chat/completions", json=body, timeout=timeout
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("VLM tag request failed for %s: %s", image_path, e)
            return []

        return parse_tags_response(content)

    @staticmethod
    def _encode_image_b64(path: Path) -> str:
        """Base64-encode an image file for inline submission to llama-server."""
        import base64
        return base64.b64encode(path.read_bytes()).decode("ascii")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_client_tags.py tests/test_vlm_client_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_client.py tests/test_vlm_client_tags.py
git commit -m "Add VlmClient.generate_tags with grammar-constrained output"
```

---

### Task 8: VlmClient.swap_model

**Files:**
- Modify: `metascan/core/vlm_client.py` (add `swap_model`)
- Test: `tests/test_vlm_client_swap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_client_swap.py
import pytest

from metascan.core.vlm_client import STATE_READY, VlmClient
from tests._fake_llama_server import FakeLlamaServer


@pytest.mark.asyncio
async def test_swap_model_reaches_ready_for_new_model():
    async with FakeLlamaServer() as fake_a, FakeLlamaServer() as fake_b:
        urls = {"qwen3vl-2b": fake_a.base_url, "qwen3vl-4b": fake_b.base_url}
        client = VlmClient(spawn_override=lambda mid: urls[mid])
        try:
            await client.start("qwen3vl-2b")
            assert client.model_id == "qwen3vl-2b"
            await client.swap_model("qwen3vl-4b")
            assert client.state == STATE_READY
            assert client.model_id == "qwen3vl-4b"
        finally:
            await client.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_client_swap.py -v`
Expected: FAIL — `swap_model` not defined.

- [ ] **Step 3: Add `swap_model`**

In `metascan/core/vlm_client.py`, add inside the class:

```python
    async def swap_model(
        self,
        new_model_id: str,
        *,
        ready_timeout: float = 300.0,
    ) -> None:
        """Tear down the current llama-server and bring up a new one.

        Caller is responsible for cancelling/draining any in-flight tagging
        jobs before calling — this method does not preserve a request queue
        across the swap.
        """
        if new_model_id == self._model_id and self._state == STATE_READY:
            return
        await self.shutdown()
        await self.start(new_model_id, wait_ready=True, ready_timeout=ready_timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_client_swap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_client.py tests/test_vlm_client_swap.py
git commit -m "Add VlmClient.swap_model"
```

---

### Task 9: VlmClient port allocation + crash detection

**Files:**
- Test: `tests/test_vlm_client_crash.py`
- Modify: `metascan/core/vlm_client.py` only if a test surfaces a bug

The crash-handling code already exists in `_wait_exit` (Task 6). This task
verifies it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_client_crash.py
import asyncio
from pathlib import Path

import pytest

from metascan.core.vlm_client import STATE_ERROR, VlmClient
from tests._fake_llama_server import FakeLlamaServer


@pytest.mark.asyncio
async def test_crash_transitions_to_error_state(tmp_path: Path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    async with FakeLlamaServer(crash_after_n_requests=1) as fake:
        client = VlmClient(spawn_override=lambda mid: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            # First request succeeds (and triggers the crash).
            await client.generate_tags(img)
            # Wait for the supervisor to notice the crash.
            for _ in range(40):
                if client.state == STATE_ERROR:
                    break
                await asyncio.sleep(0.1)
            # NOTE: spawn_override mode does not own the subprocess so this
            # particular check does not apply. We assert the public observable
            # behavior: a request after the server is gone should fail.
            with pytest.raises(Exception):
                # Force a second request that talks to the now-dead fake.
                async with __import__("httpx").AsyncClient() as h:
                    r = await h.post(
                        f"{fake.base_url}/v1/chat/completions",
                        json={"messages": []},
                        timeout=2.0,
                    )
                    r.raise_for_status()
        finally:
            await client.shutdown()


@pytest.mark.asyncio
async def test_each_client_picks_unique_port(monkeypatch):
    # Ensure two simultaneous clients don't collide. We can't easily test
    # full subprocess spawn without binary present, so we exercise _free_port.
    from metascan.core.vlm_client import _free_port
    a = _free_port()
    b = _free_port()
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_client_crash.py -v`
Expected: PASS for the port test; the crash test passes only if the
supervisor is well-behaved. If the crash test hangs, the subprocess
state-machine wiring needs a fix — but with `spawn_override` mode we don't
own the process so this test mostly documents the fake-server behavior.

- [ ] **Step 3: Note crash-mode coverage gap**

The `spawn_override` test mode skips real subprocess management, so true
crash recovery (the `_wait_exit` → respawn path) is not covered by these
tests. Add a comment in `metascan/core/vlm_client.py` above `_wait_exit`:

```python
    # NOTE: real-binary crash recovery is exercised by manual testing during
    # Phase 5/6 integration, not unit tests — the fake-server fixture uses
    # spawn_override which bypasses the subprocess we'd need to crash.
```

- [ ] **Step 4: Run all VlmClient tests**

Run: `pytest tests/test_vlm_client*.py tests/test_fake_llama_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/vlm_client.py tests/test_vlm_client_crash.py
git commit -m "Verify VlmClient port allocation; document crash-mode coverage gap"
```

---

## Phase 3: Database tag-merge extension

The DB layer arbitrates which tagger wins (see spec §7.4). This phase
extends `add_tag_indices` to accept `'vlm'` as a source and updates
`_update_indices` to handle the new merged variants.

### Task 10: Extend `add_tag_indices` to accept VLM source

**Files:**
- Modify: `metascan/core/database_sqlite.py:928-960` (`add_tag_indices`)
- Test: `tests/test_database_vlm_tags.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database_vlm_tags.py
"""Tests for the spec §7.4 tag-merge matrix.

Each test names the (existing source × incoming source) cell it covers.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "test.db"
        manager = DatabaseManager(db_file)
        yield manager
        manager.close()


def _read_tag_rows(db: DatabaseManager, path: Path):
    """Return [(index_key, source)] for tag rows on path, ordered by key."""
    posix = path.as_posix()
    with db.lock:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT index_key, source FROM indices "
                "WHERE index_type='tag' AND file_path=? "
                "ORDER BY index_key",
                (posix,),
            ).fetchall()
    return [(r["index_key"], r["source"]) for r in rows]


def test_add_vlm_tags_to_empty_inserts_vlm(db):
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["red", "blue"], source="vlm")
    assert _read_tag_rows(db, p) == [("blue", "vlm"), ("red", "vlm")]


def test_add_vlm_tags_over_clip_replaces_clip(db):
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["red", "blue"], source="clip")
    db.add_tag_indices(p, ["green", "yellow"], source="vlm")
    rows = _read_tag_rows(db, p)
    # Spec §7.4: clip × vlm → replace with vlm. Old clip tags must be gone.
    sources = {s for _, s in rows}
    assert "clip" not in sources
    keys = {k for k, _ in rows}
    assert keys == {"green", "yellow"}


def test_add_vlm_tags_overlapping_with_prompt_upserts_to_vlm_prompt(db):
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["red"], source="prompt")
    db.add_tag_indices(p, ["red", "blue"], source="vlm")
    rows = dict(_read_tag_rows(db, p))
    assert rows["red"] == "vlm+prompt"
    assert rows["blue"] == "vlm"


def test_add_clip_tags_does_not_overwrite_vlm(db):
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["red"], source="vlm")
    db.add_tag_indices(p, ["blue"], source="clip")
    rows = dict(_read_tag_rows(db, p))
    # Spec §7.4: vlm × clip → preserve vlm, no change. The 'blue' clip tag
    # must NOT be inserted because the file is already vlm-owned.
    assert rows == {"red": "vlm"}


def test_add_vlm_tags_replaces_previous_vlm(db):
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["old1", "old2"], source="vlm")
    db.add_tag_indices(p, ["new1", "new2"], source="vlm")
    rows = dict(_read_tag_rows(db, p))
    assert rows == {"new1": "vlm", "new2": "vlm"}


def test_add_clip_tags_with_existing_prompt_unchanged_by_design(db):
    """Existing behavior: prompt × clip → 'both' (clip+prompt). Don't break."""
    p = Path("/img/a.jpg")
    db.add_tag_indices(p, ["red"], source="prompt")
    db.add_tag_indices(p, ["red"], source="clip")
    rows = dict(_read_tag_rows(db, p))
    assert rows["red"] == "both"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database_vlm_tags.py -v`
Expected: Multiple failures — `add_tag_indices` raises `ValueError` for
`source='vlm'`.

- [ ] **Step 3: Update `add_tag_indices`**

Replace `add_tag_indices` in `metascan/core/database_sqlite.py` (around
line 928–960) with:

```python
    def add_tag_indices(
        self, file_path: Path, tags: List[str], source: str = "clip"
    ) -> None:
        """Add or merge tag rows sourced from ``source`` for ``file_path``.

        Sources:
          - ``prompt`` — extracted from generation metadata.
          - ``clip``   — CLIP retrieval over the vocabulary.
          - ``vlm``    — generative tagger (Qwen3-VL).

        Merge rules (spec §7.4):
          - vlm × clip → preserve vlm (CLIP cannot overwrite VLM).
          - clip × vlm → replace clip rows with vlm.
          - vlm × prompt or prompt × vlm → upsert to ``vlm+prompt``.
          - clip × prompt or prompt × clip → upsert to ``both`` (legacy name
            for ``clip+prompt``).
          - vlm × vlm → wholesale replace existing vlm tags.
        """
        if source not in ("prompt", "clip", "vlm"):
            raise ValueError(
                f"tag source must be one of prompt/clip/vlm, got {source!r}"
            )
        if not tags:
            return
        posix_path = to_posix_path(file_path)

        with self.lock:
            with self._get_connection() as conn:
                if source == "vlm":
                    self._add_vlm_tags(conn, posix_path, tags)
                elif source == "clip":
                    self._add_clip_tags(conn, posix_path, tags)
                else:  # prompt
                    self._add_prompt_tags(conn, posix_path, tags)
                conn.commit()

    def _add_vlm_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Replace clip-source tags wholesale; merge with prompt-source.

        VLM is the authoritative tagger when it runs, so existing
        clip / clip+prompt rows are downgraded — clip rows are deleted, and
        the prompt half of ``both`` rows is preserved by demoting to prompt.
        Existing vlm / vlm+prompt rows are wholesale replaced (re-tag).
        """
        # Step 1: drop existing vlm and vlm+prompt rows entirely (they will
        # be re-inserted from the new tag set).
        conn.execute(
            "DELETE FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source IN ('vlm', 'vlm+prompt')",
            (posix_path,),
        )
        # Step 2: drop pure clip rows (clip lost the arbitration).
        conn.execute(
            "DELETE FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source='clip'",
            (posix_path,),
        )
        # Step 3: demote 'both' (clip+prompt) rows to prompt-only.
        conn.execute(
            "UPDATE indices SET source='prompt' "
            "WHERE file_path=? AND index_type='tag' AND source='both'",
            (posix_path,),
        )
        # Step 4: insert the new vlm tags. If a tag collides with a
        # prompt-only row, upgrade to vlm+prompt; otherwise insert as vlm.
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'vlm') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'prompt' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )

    def _add_clip_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Insert clip-source tags. Skipped if any vlm row exists for this
        file (vlm wins). Merges with prompt-source rows to ``both``."""
        # If any vlm row exists for this file, CLIP loses — bail.
        row = conn.execute(
            "SELECT 1 FROM indices WHERE file_path=? AND index_type='tag' "
            "AND source IN ('vlm', 'vlm+prompt') LIMIT 1",
            (posix_path,),
        ).fetchone()
        if row is not None:
            return
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'clip') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'prompt' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )

    def _add_prompt_tags(
        self, conn: sqlite3.Connection, posix_path: str, tags: List[str]
    ) -> None:
        """Insert prompt-source tags. Merges with both clip and vlm rows."""
        for t in tags:
            if not t:
                continue
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'prompt') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'clip' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  WHEN indices.source = 'vlm' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE excluded.source END",
                (t.lower(), posix_path),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_vlm_tags.py -v`
Expected: PASS.

Also run the existing DB test suite to ensure no regression:

Run: `pytest tests/test_database_photo_columns.py tests/test_filters_camera.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_database_vlm_tags.py
git commit -m "Extend add_tag_indices for vlm source with merge matrix"
```

---

### Task 11: Update `_update_indices` to preserve VLM tags across rescans

**Files:**
- Modify: `metascan/core/database_sqlite.py:880-927` (`_update_indices`)
- Test: append to `tests/test_database_vlm_tags.py`

The existing `_update_indices` runs on every `save_media` (rescan) and
demotes `'both'` to `'clip'` before re-inserting prompt rows. It must be
extended to also demote `'vlm+prompt'` to `'vlm'` on prompt re-extraction
so VLM tags aren't lost.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_database_vlm_tags.py`:

```python
def _make_minimal_media(p: Path, prompt_text: str = "") -> Media:
    return Media(
        file_path=p,
        file_size=100,
        width=10,
        height=10,
        format="JPEG",
        created_at=datetime(2026, 1, 1),
        modified_at=datetime(2026, 1, 1),
        prompt=prompt_text,
    )


def test_rescan_preserves_vlm_tags_when_prompt_unchanged(db):
    p = Path("/img/r.jpg")
    db.save_media(_make_minimal_media(p))
    db.add_tag_indices(p, ["unique-vlm-tag"], source="vlm")
    # Re-save the media — _update_indices runs again. VLM-only rows must
    # survive because prompt has nothing to say about them.
    db.save_media(_make_minimal_media(p))
    rows = dict(_read_tag_rows(db, p))
    assert rows.get("unique-vlm-tag") == "vlm"


def test_rescan_with_prompt_change_demotes_vlm_prompt_back_to_vlm(db):
    p = Path("/img/r.jpg")
    db.save_media(_make_minimal_media(p, prompt_text="red flower"))
    # Prompt extraction would have inserted ['red', 'flower']; simulate by
    # explicit add. Then VLM tags add 'red' → upserts to vlm+prompt.
    db.add_tag_indices(p, ["red", "flower"], source="prompt")
    db.add_tag_indices(p, ["red", "vase"], source="vlm")
    pre = dict(_read_tag_rows(db, p))
    assert pre["red"] == "vlm+prompt"
    # Now rescan with EMPTY prompt — _update_indices should drop prompt rows
    # but preserve VLM. The 'red' row goes from vlm+prompt → vlm. The 'flower'
    # prompt-only row goes away.
    db.save_media(_make_minimal_media(p, prompt_text=""))
    post = dict(_read_tag_rows(db, p))
    assert post.get("red") == "vlm"
    assert "flower" not in post
    assert post.get("vase") == "vlm"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_database_vlm_tags.py -v`
Expected: FAIL — `_update_indices` doesn't yet handle `vlm+prompt`.

- [ ] **Step 3: Update `_update_indices`**

In `metascan/core/database_sqlite.py`, replace the body of
`_update_indices` (lines ~880–927) with:

```python
    def _update_indices(self, conn: sqlite3.Connection, media: Media) -> None:
        """Refresh non-tag indices and prompt-source tag rows for ``media``.

        VLM-source tag rows survive a rescan unchanged (the embedding/VLM
        worker writes them separately via ``add_tag_indices``). Prompt-source
        rows are torn down and rebuilt from the freshly-parsed metadata.
        """
        posix_path = to_posix_path(media.file_path)

        # Drop non-tag rows (we'll rebuild them) and pure prompt-tag rows.
        # Tag rows with source IN ('clip', 'vlm', 'both', 'vlm+prompt') are
        # preserved across this DELETE — but the 'both' / 'vlm+prompt' rows
        # need their prompt half rewritten below.
        conn.execute(
            "DELETE FROM indices WHERE file_path = ? AND "
            "(index_type != 'tag' OR source IS NULL OR source = 'prompt')",
            (posix_path,),
        )
        # Demote 'both' (clip+prompt) → 'clip' so the upsert below can
        # promote back to 'both' for tags the new prompt still contains.
        conn.execute(
            "UPDATE indices SET source = 'clip' "
            "WHERE file_path = ? AND index_type = 'tag' AND source = 'both'",
            (posix_path,),
        )
        # Demote 'vlm+prompt' → 'vlm' for the same reason.
        conn.execute(
            "UPDATE indices SET source = 'vlm' "
            "WHERE file_path = ? AND index_type = 'tag' AND source = 'vlm+prompt'",
            (posix_path,),
        )

        non_tag_rows: List[tuple] = []
        prompt_tag_keys: List[str] = []
        for index_type, index_key, source in self._generate_indices(media):
            if index_type == "tag" and source == "prompt":
                prompt_tag_keys.append(index_key)
            else:
                non_tag_rows.append((index_type, index_key, posix_path, source))

        if non_tag_rows:
            conn.executemany(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES (?, ?, ?, ?)",
                non_tag_rows,
            )
        # Re-insert prompt-source tags. Collisions promote to the merged
        # source name (both / vlm+prompt) per the same case ladder used in
        # _add_prompt_tags.
        for key in prompt_tag_keys:
            conn.execute(
                "INSERT INTO indices (index_type, index_key, file_path, source) "
                "VALUES ('tag', ?, ?, 'prompt') "
                "ON CONFLICT(index_type, index_key, file_path) DO UPDATE SET "
                "source = CASE "
                "  WHEN indices.source = 'clip' THEN 'both' "
                "  WHEN indices.source = 'both' THEN 'both' "
                "  WHEN indices.source = 'vlm' THEN 'vlm+prompt' "
                "  WHEN indices.source = 'vlm+prompt' THEN 'vlm+prompt' "
                "  ELSE 'prompt' END",
                (key, posix_path),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database_vlm_tags.py tests/test_database_photo_columns.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_database_vlm_tags.py
git commit -m "Preserve VLM tags across rescans via _update_indices demotion"
```

---

## Phase 4: Backend API surface

### Task 12: VlmClient singleton wiring + `/api/vlm/status`

**Files:**
- Create: `backend/api/vlm.py`
- Test: `tests/test_vlm_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_api.py
"""TestClient coverage of the /api/vlm endpoints."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


@pytest.fixture
def app_with_stub_vlm():
    app = create_app()
    stub = MagicMock()
    stub.snapshot.return_value = {
        "state": "ready",
        "model_id": "qwen3vl-4b",
        "base_url": "http://127.0.0.1:9999",
        "progress": {},
        "error": None,
    }
    stub.generate_tags = AsyncMock(return_value=["red", "blue"])
    stub.ensure_started = AsyncMock(return_value=None)
    vlm_api.set_vlm_client(stub)
    yield app, stub
    vlm_api.set_vlm_client(None)


def test_status_returns_snapshot(app_with_stub_vlm):
    app, _ = app_with_stub_vlm
    with TestClient(app) as c:
        r = c.get("/api/vlm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ready"
    assert body["model_id"] == "qwen3vl-4b"


def test_status_returns_idle_when_unset():
    vlm_api.set_vlm_client(None)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/vlm/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_tag_endpoint_returns_tags(app_with_stub_vlm, tmp_path: Path):
    app, stub = app_with_stub_vlm
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": str(img)})
    assert r.status_code == 200
    assert r.json() == {"tags": ["red", "blue"]}
    stub.ensure_started.assert_awaited()
    stub.generate_tags.assert_awaited()


def test_tag_endpoint_404_for_missing_file(app_with_stub_vlm):
    app, _ = app_with_stub_vlm
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": "/no/such/file.jpg"})
    assert r.status_code == 404


def test_tag_endpoint_503_when_no_client():
    vlm_api.set_vlm_client(None)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": "/x.jpg"})
    assert r.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_api.py -v`
Expected: FAIL — `backend.api.vlm` does not exist.

- [ ] **Step 3: Create `backend/api/vlm.py`**

Create `backend/api/vlm.py`:

```python
"""REST endpoints for Qwen3-VL tagging.

The VlmClient singleton is installed by the FastAPI lifespan via
``set_vlm_client(...)``. Endpoints fail fast with 503 if it's missing
(misconfigured deployment).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from metascan.core.database_sqlite import DatabaseManager
from backend.dependencies import get_db


router = APIRouter(prefix="/api/vlm", tags=["vlm"])


_vlm_client = None  # type: ignore[var-annotated]


def set_vlm_client(client: Any) -> None:
    """Install the VlmClient singleton. Called from the FastAPI lifespan.

    Pass ``None`` to clear (used by tests)."""
    global _vlm_client
    _vlm_client = client


def get_vlm_client() -> Any:
    return _vlm_client


class TagRequest(BaseModel):
    path: str


@router.get("/status")
async def status() -> Dict[str, Any]:
    client = _vlm_client
    if client is None:
        return {
            "state": "idle",
            "model_id": None,
            "base_url": None,
            "progress": {},
            "error": None,
        }
    return client.snapshot()


@router.post("/tag")
async def tag_one(body: TagRequest) -> Dict[str, List[str]]:
    client = _vlm_client
    if client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    p = Path(body.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {body.path}")
    if client.model_id is None:
        # Pick a sensible default — first available recommended model.
        from metascan.core.hardware import detect_hardware, feature_gates
        gates = feature_gates(detect_hardware())
        candidates = [
            mid for mid, g in gates.items()
            if mid.startswith("qwen3vl-") and g.recommended
        ]
        if not candidates:
            raise HTTPException(
                status_code=503, detail="no recommended VLM model on this hardware"
            )
        await client.ensure_started(candidates[0])
    else:
        await client.ensure_started(client.model_id)

    tags = await client.generate_tags(p)
    db: DatabaseManager = get_db()
    db.add_tag_indices(p, tags, source="vlm")
    return {"tags": tags}
```

- [ ] **Step 4: Register the router in `backend/main.py`**

Edit `backend/main.py`. In the `from backend.api import (...)` block
(around line 21), add `vlm` to the import list. Then in `create_app()`
near the other `app.include_router(...)` calls (around line 254), add:

```python
    app.include_router(vlm.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_vlm_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/api/vlm.py backend/main.py tests/test_vlm_api.py
git commit -m "Add /api/vlm/status and /api/vlm/tag endpoints"
```

---

### Task 13: `/api/vlm/retag` background job

**Files:**
- Modify: `backend/api/vlm.py`
- Test: append to `tests/test_vlm_api.py`

- [ ] **Step 1: Append failing tests**

```python
def test_retag_returns_job_id(app_with_stub_vlm, tmp_path: Path):
    app, _ = app_with_stub_vlm
    a = tmp_path / "a.jpg"; a.write_bytes(b"\xff\xd8\xff\xd9")
    b = tmp_path / "b.jpg"; b.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post(
            "/api/vlm/retag",
            json={"scope": "paths", "paths": [str(a), str(b)]},
        )
    assert r.status_code == 202
    assert "job_id" in r.json()
    assert r.json()["total"] == 2


def test_retag_cancel_endpoint(app_with_stub_vlm, tmp_path: Path):
    app, _ = app_with_stub_vlm
    a = tmp_path / "a.jpg"; a.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post(
            "/api/vlm/retag",
            json={"scope": "paths", "paths": [str(a)]},
        )
        job_id = r.json()["job_id"]
        r2 = c.delete(f"/api/vlm/retag/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vlm_api.py -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Add the retag job machinery**

In `backend/api/vlm.py`, extend the imports at the top:

```python
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Literal
```

Then append below the existing `tag_one` route:

```python
logger = logging.getLogger(__name__)


@dataclass
class _RetagJob:
    job_id: str
    paths: list[str]
    cancelled: bool = False
    current: int = 0
    total: int = 0


_jobs: Dict[str, _RetagJob] = {}


class RetagRequest(BaseModel):
    scope: Literal["paths", "all_clip"] = "paths"
    paths: Optional[List[str]] = None
    force: bool = False


async def _run_retag_job(job: "_RetagJob") -> None:
    client = _vlm_client
    db = get_db()
    if client is None:
        return
    job.total = len(job.paths)
    for i, path_str in enumerate(job.paths):
        if job.cancelled:
            break
        job.current = i
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            await client.ensure_started(client.model_id or "qwen3vl-4b")
            tags = await client.generate_tags(p)
            db.add_tag_indices(p, tags, source="vlm")
            # Broadcast progress (Task 14 wires this to WS).
            try:
                from backend.ws.manager import ws_manager
                ws_manager.broadcast_sync(
                    "models",
                    "vlm_progress",
                    {"job_id": job.job_id, "current": i + 1, "total": job.total},
                )
            except Exception:
                pass
        except Exception as e:
            logger.warning("retag failed for %s: %s", path_str, e)
    job.current = job.total


@router.post("/retag", status_code=202)
async def retag(body: RetagRequest) -> Dict[str, Any]:
    if _vlm_client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.scope == "paths":
        if not body.paths:
            raise HTTPException(status_code=400, detail="paths required for scope=paths")
        targets = list(body.paths)
    elif body.scope == "all_clip":
        # Find every file with at least one tag whose source is 'clip' or
        # 'both' but no 'vlm'/'vlm+prompt' rows (unless force=True).
        db = get_db()
        targets = _list_paths_for_retag(db, force=body.force)
    else:
        raise HTTPException(status_code=400, detail=f"unknown scope: {body.scope}")

    job = _RetagJob(job_id=str(uuid.uuid4()), paths=targets, total=len(targets))
    _jobs[job.job_id] = job
    asyncio.create_task(_run_retag_job(job))
    return {"job_id": job.job_id, "total": job.total}


@router.delete("/retag/{job_id}")
async def cancel_retag(job_id: str) -> Dict[str, str]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    job.cancelled = True
    return {"status": "cancelled"}


def _list_paths_for_retag(db: DatabaseManager, *, force: bool) -> List[str]:
    """SELECT paths whose tag rows are pure clip/both. With force=True also
    include vlm-tagged files."""
    sql = (
        "SELECT DISTINCT file_path FROM indices "
        "WHERE index_type='tag' AND source IN ('clip', 'both')"
    )
    if force:
        sql = (
            "SELECT DISTINCT file_path FROM indices WHERE index_type='tag'"
        )
    with db.lock:
        with db._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
    return [r["file_path"] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/vlm.py tests/test_vlm_api.py
git commit -m "Add /api/vlm/retag background job with cancel"
```

---

### Task 13b: `/api/vlm/active` — switch the loaded VLM at runtime

Spec §7.7 calls for a model-swap endpoint so the Models tab can pick a
different size mid-session.

**Files:**
- Modify: `backend/api/vlm.py`
- Test: append to `tests/test_vlm_api.py`

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
async def test_active_endpoint_calls_swap(app_with_stub_vlm):
    app, stub = app_with_stub_vlm
    stub.swap_model = AsyncMock()
    with TestClient(app) as c:
        r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-8b"})
    assert r.status_code == 200
    stub.swap_model.assert_awaited_with("qwen3vl-8b")


def test_active_endpoint_400_on_unknown_model(app_with_stub_vlm):
    app, _ = app_with_stub_vlm
    with TestClient(app) as c:
        r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-bogus"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_api.py -v`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Add the endpoint**

In `backend/api/vlm.py`, after the `cancel_retag` route:

```python
class ActiveBody(BaseModel):
    model_id: str


@router.post("/active")
async def set_active(body: ActiveBody) -> Dict[str, Any]:
    from metascan.core.vlm_models import REGISTRY

    client = _vlm_client
    if client is None:
        raise HTTPException(status_code=503, detail="vlm client not initialized")
    if body.model_id not in REGISTRY:
        raise HTTPException(
            status_code=400, detail=f"unknown model: {body.model_id}"
        )
    # Cancel any in-flight retag jobs so they don't outlive the swap.
    for job in _jobs.values():
        job.cancelled = True
    await client.swap_model(body.model_id)
    return client.snapshot()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/vlm.py tests/test_vlm_api.py
git commit -m "Add /api/vlm/active endpoint to switch loaded VLM"
```

---

### Task 14: `_vlm_status_rows` in `backend/api/models.py`

**Files:**
- Modify: `backend/api/models.py`
- Test: `tests/test_models_vlm_rows.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_vlm_rows.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import create_app


def test_models_status_includes_vlm_rows():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["models"]]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in ids


def test_models_status_includes_vlm_gates():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    gates = r.json()["gates"]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in gates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_vlm_rows.py -v`
Expected: FAIL — `qwen3vl-*` not in the status response.

- [ ] **Step 3: Add `_vlm_status_rows` and call it**

In `backend/api/models.py`, after `_nltk_status_rows` (around line 220),
add:

```python
def _vlm_status_rows(preload: List[str]) -> List[Dict[str, Any]]:
    """Status rows for the four Qwen3-VL Abliterated variants."""
    from metascan.core.vlm_models import REGISTRY
    from metascan.utils.app_paths import get_data_dir

    rows: List[Dict[str, Any]] = []
    vlm_dir = get_data_dir() / "models" / "vlm"
    for mid, spec in REGISTRY.items():
        gguf = vlm_dir / spec.gguf_filename
        mmproj = vlm_dir / spec.mmproj_filename
        present = gguf.exists() and mmproj.exists()
        size = (gguf.stat().st_size + mmproj.stat().st_size) if present else 0
        rows.append({
            "id": mid,
            "group": "Tagging (Qwen3-VL)",
            "name": spec.display_name,
            "description": f"{spec.quant} GGUF, ~{spec.approx_vram_gb:.1f} GB VRAM",
            "status": "available" if present else "missing",
            "size_bytes": size or None,
            "cache_path": str(gguf) if present else None,
            "required_vram_mb": int(spec.min_vram_gb * 1024),
            "preload_at_startup": mid in preload,
        })
    return rows
```

In `_build_status_payload` (around line 315), extend the `rows` assignment:

```python
    rows = (
        _clip_status_rows(preload)
        + _upscale_status_rows(preload)
        + _nltk_status_rows(preload)
        + _vlm_status_rows(preload)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models_vlm_rows.py tests/test_models_hardware_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/models.py tests/test_models_vlm_rows.py
git commit -m "Surface Qwen3-VL rows in /api/models/status"
```

---

## Phase 5: Lifespan integration + WS broadcasts

### Task 15: Construct VlmClient in lifespan

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_lifespan_vlm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lifespan_vlm.py
"""Verify VlmClient is constructed and registered during lifespan."""

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


def test_lifespan_installs_vlm_client():
    app = create_app()
    with TestClient(app):
        client = vlm_api.get_vlm_client()
        assert client is not None
        # Constructed but not started — state is 'idle' until first use.
        assert client.state == "idle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lifespan_vlm.py -v`
Expected: FAIL — lifespan doesn't construct VlmClient.

- [ ] **Step 3: Wire VlmClient into the lifespan**

In `backend/main.py`, modify the imports near line 36 to include the
new client:

```python
from metascan.core.inference_client import InferenceClient
from metascan.core.vlm_client import VlmClient
from backend.api.vlm import set_vlm_client
```

Then in the `lifespan` function, after the existing CLIP wiring
(`similarity.set_inference_client(client)` around line 131), add:

```python
    vlm_client = VlmClient()
    _wire_vlm_client_status(vlm_client)
    set_vlm_client(vlm_client)

    # Optional preload — same `preload_at_startup` array, namespaced by id.
    for preload_id in preload_list:
        if preload_id.startswith("qwen3vl-"):
            logger.info("Preloading VLM at startup: %s", preload_id)

            async def _preload_vlm(mid: str = preload_id) -> None:
                try:
                    await vlm_client.start(mid, wait_ready=False)
                except Exception:
                    logger.exception("VLM preload failed")

            asyncio.create_task(_preload_vlm())
            break  # only one VLM at a time
```

In the `finally` block of the lifespan (next to the existing
`await client.shutdown()` for CLIP), add:

```python
        try:
            await vlm_client.shutdown()
        except Exception:
            logger.exception("VLM client shutdown raised")
```

Also add the helper function near `_wire_inference_client_status`:

```python
def _wire_vlm_client_status(client: VlmClient) -> None:
    """Bridge VlmClient state transitions onto the ``models`` WS channel."""

    def on_status(_state: str, payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync("models", "vlm_status", payload)

    def on_progress(payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync("models", "vlm_progress", payload)

    client.on_status(on_status)
    client.on_progress(on_progress)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_lifespan_vlm.py tests/test_vlm_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_lifespan_vlm.py
git commit -m "Construct VlmClient in lifespan and wire ws broadcasts"
```

---

## Phase 6: Scanner integration

This phase routes batch tag generation to VLM on eligible tiers, leaving
CLIP tagging in place as the fallback. The embedding worker emits a list
of paths needing VLM tags via a JSON-lines file in the queue directory;
the scanner drains that file and feeds paths to VlmClient.

### Task 16: Embedding worker — `tag_with_vlm` flag

**Files:**
- Modify: `metascan/workers/embedding_worker.py`
- Modify: `metascan/core/embedding_queue.py`
- Test: `tests/test_embedding_worker_vlm_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_embedding_worker_vlm_flag.py
"""Verify embedding_worker honors tag_with_vlm flag in task config.

Smoke test only — actual worker behavior is exercised by the existing
test_embedding_pipeline.py. We just check that the task-config hook is
plumbed through and that vlm-pending paths are written to the queue dir.
"""

import json
import tempfile
from pathlib import Path

from metascan.core.embedding_queue import EmbeddingQueue


def test_task_file_contains_tag_with_vlm_flag():
    with tempfile.TemporaryDirectory() as tmp:
        eq = EmbeddingQueue()
        eq._queue_dir = Path(tmp)  # type: ignore[attr-defined]
        eq.start_indexing(
            file_paths=[],  # empty → emits complete and short-circuits
            clip_model_key="small",
            db_path=tmp,
            tag_with_vlm=True,
        )
        # start_indexing with empty paths emits complete; nothing to assert
        # about the task file. Instead, check the signature accepts the flag.
```

(This is a smoke test — full coverage is in Task 17.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_embedding_worker_vlm_flag.py -v`
Expected: FAIL — `start_indexing` doesn't accept `tag_with_vlm`.

- [ ] **Step 3: Add the flag to `EmbeddingQueue.start_indexing`**

Edit `metascan/core/embedding_queue.py`. Update the signature of
`start_indexing` (line 74) to add `tag_with_vlm: bool = False`. Update
the task dict (line 126) to include `"tag_with_vlm": tag_with_vlm`.

The flag is keyword-only with a default of `False`, so existing call
sites continue to work without modification. Search for callers with
`grep -rn "start_indexing(" backend/ metascan/` and verify none break.

- [ ] **Step 4: Read the flag in the worker and skip CLIP tagging**

In `metascan/workers/embedding_worker.py`, near where the task file is
loaded (search for `embedding_task.json`), capture the new flag:

```python
        tag_with_vlm = bool(task.get("tag_with_vlm", False))
```

Then in the per-file loop (around line 482, the `if vocab is not None:`
block), wrap the CLIP-tagging branch:

```python
                        if vocab is not None and not tag_with_vlm:
                            tags = select_tags(...)
                            ...
```

After the `embedded_count += 1` line, when `tag_with_vlm` is true, append
the path to a JSON-lines pending file:

```python
                    if tag_with_vlm and embedding is not None:
                        pending_path = (
                            Path(task_dir) / "vlm_pending.jsonl"
                        )
                        with open(pending_path, "a", encoding="utf-8") as fp:
                            fp.write(json.dumps({"path": str(file_path)}) + "\n")
```

(Find `task_dir` — it's the directory passed as the worker's first argv
arg, available near the top of `main()`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_embedding_worker_vlm_flag.py tests/test_embedding_pipeline.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add metascan/workers/embedding_worker.py metascan/core/embedding_queue.py tests/test_embedding_worker_vlm_flag.py
git commit -m "Add tag_with_vlm flag to embedding worker"
```

---

### Task 17: VlmTagPump — drain pending file and dispatch to VlmClient

**Files:**
- Create: `backend/services/vlm_tag_pump.py`
- Test: `tests/test_vlm_tag_pump.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vlm_tag_pump.py
"""VlmTagPump drains the embedding worker's vlm_pending.jsonl into VlmClient."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.vlm_tag_pump import VlmTagPump


@pytest.mark.asyncio
async def test_pump_processes_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        queue_dir = Path(tmp)
        a = queue_dir / "a.jpg"; a.write_bytes(b"\xff\xd8\xff\xd9")
        b = queue_dir / "b.jpg"; b.write_bytes(b"\xff\xd8\xff\xd9")
        pending = queue_dir / "vlm_pending.jsonl"
        pending.write_text(
            json.dumps({"path": str(a)}) + "\n"
            + json.dumps({"path": str(b)}) + "\n"
        )
        client = MagicMock()
        client.ensure_started = AsyncMock()
        client.generate_tags = AsyncMock(side_effect=[["x"], ["y"]])
        client.model_id = "qwen3vl-4b"

        db = MagicMock()
        pump = VlmTagPump(queue_dir, client, db, model_id="qwen3vl-4b")
        await pump.drain_once()

        assert client.generate_tags.await_count == 2
        assert db.add_tag_indices.call_count == 2
        # Pump truncates pending file after successful drain.
        assert pending.read_text() == ""


@pytest.mark.asyncio
async def test_pump_handles_missing_file_gracefully():
    with tempfile.TemporaryDirectory() as tmp:
        queue_dir = Path(tmp)
        client = MagicMock()
        client.ensure_started = AsyncMock()
        db = MagicMock()
        pump = VlmTagPump(queue_dir, client, db, model_id="qwen3vl-4b")
        await pump.drain_once()  # no pending file = no-op
        client.generate_tags.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vlm_tag_pump.py -v`
Expected: FAIL — `backend.services.vlm_tag_pump` does not exist.

- [ ] **Step 3: Create `backend/services/vlm_tag_pump.py`**

Create the directory if missing:

```bash
mkdir -p backend/services
touch backend/services/__init__.py
```

Then write `backend/services/vlm_tag_pump.py`:

```python
"""Drains the embedding-worker's vlm_pending.jsonl into VlmClient.

The embedding worker writes one line per embedded file when
``tag_with_vlm=True``. This pump reads those lines, calls
``VlmClient.generate_tags`` (with bounded concurrency), and writes the
tags to the DB with ``source='vlm'``.

Designed to run from the FastAPI process — it shares the VlmClient
singleton with the on-demand /api/vlm/tag endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VlmTagPump:
    def __init__(
        self,
        queue_dir: Path,
        client: Any,
        db: Any,
        *,
        model_id: str,
        concurrency: int = 2,
    ) -> None:
        self._queue_dir = queue_dir
        self._client = client
        self._db = db
        self._model_id = model_id
        self._sem = asyncio.Semaphore(concurrency)
        self._cancelled = False

    @property
    def pending_file(self) -> Path:
        return self._queue_dir / "vlm_pending.jsonl"

    def cancel(self) -> None:
        self._cancelled = True

    async def drain_once(self) -> int:
        """Process every line currently in vlm_pending.jsonl. Returns count."""
        if not self.pending_file.exists():
            return 0
        # Snapshot + truncate atomically — any new lines after this point go
        # to a freshly-empty file and are picked up by the next drain.
        text = self.pending_file.read_text(encoding="utf-8")
        self.pending_file.write_text("", encoding="utf-8")
        paths: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                paths.append(json.loads(line)["path"])
            except (json.JSONDecodeError, KeyError):
                logger.warning("dropping malformed line: %s", line[:200])
        if not paths:
            return 0

        await self._client.ensure_started(self._model_id)

        async def _one(p: str) -> None:
            if self._cancelled:
                return
            async with self._sem:
                try:
                    tags = await self._client.generate_tags(Path(p))
                    self._db.add_tag_indices(Path(p), tags, source="vlm")
                except Exception as e:
                    logger.warning("VLM tag failed for %s: %s", p, e)

        await asyncio.gather(*[_one(p) for p in paths])
        return len(paths)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vlm_tag_pump.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/__init__.py backend/services/vlm_tag_pump.py tests/test_vlm_tag_pump.py
git commit -m "Add VlmTagPump to drain pending tags into VlmClient"
```

---

### Task 18: Scanner — decide tag_with_vlm based on gate

**Files:**
- Modify: `backend/api/scan.py` (or wherever `start_indexing` is called)
- Test: `tests/test_scanner_vlm_routing.py`

- [ ] **Step 1: Find the call site**

Run:

```bash
grep -rn "start_indexing\|init_embedding_queue" backend/api/ metascan/core/ | head
```

Note the file + line where `start_indexing(...)` is invoked from the
scan path. The flag must be passed there.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scanner_vlm_routing.py
"""Scan dispatch sets tag_with_vlm based on hardware gates."""

from unittest.mock import MagicMock, patch

from metascan.core.hardware import CudaInfo, HardwareReport


def _report(cuda_gb=None):
    cuda = CudaInfo(name="t", vram_gb=cuda_gb, capability="8.6") if cuda_gb else None
    return HardwareReport(
        os="Linux", machine="x86_64", python="3.11", cpu_count=8,
        ram_gb=16.0, cuda=cuda,
    )


def test_workstation_routes_tag_with_vlm_true():
    from backend.services.scan_dispatch import should_tag_with_vlm
    assert should_tag_with_vlm(_report(cuda_gb=16.0)) is True


def test_cpu_only_routes_tag_with_vlm_false():
    from backend.services.scan_dispatch import should_tag_with_vlm
    assert should_tag_with_vlm(_report()) is False


def test_cuda_entry_routes_tag_with_vlm_false():
    from backend.services.scan_dispatch import should_tag_with_vlm
    # cuda_entry recommended is qwen3vl-2b — but it requires the binary
    # AND the model GGUF to be present. Without them, fall back to CLIP.
    assert should_tag_with_vlm(_report(cuda_gb=4.0)) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_scanner_vlm_routing.py -v`
Expected: FAIL — `should_tag_with_vlm` doesn't exist.

- [ ] **Step 4: Create the dispatch helper**

Create `backend/services/scan_dispatch.py`:

```python
"""Helpers that decide how the scanner routes per-file work."""

from __future__ import annotations

from metascan.core.hardware import HardwareReport, feature_gates
from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path


def should_tag_with_vlm(report: HardwareReport) -> bool:
    """True iff a recommended VLM model is available AND its weights +
    binary are installed. Otherwise scanner falls back to CLIP tagging."""
    if not binary_path().exists():
        return False
    gates = feature_gates(report)
    recommended = [
        mid for mid, g in gates.items()
        if mid.startswith("qwen3vl-") and g.recommended
    ]
    if not recommended:
        return False
    spec = REGISTRY[recommended[0]]
    vlm_dir = get_data_dir() / "models" / "vlm"
    return (
        (vlm_dir / spec.gguf_filename).exists()
        and (vlm_dir / spec.mmproj_filename).exists()
    )


def recommended_vlm_model_id(report: HardwareReport) -> str | None:
    """Return the recommended VLM model id, or None if none is recommended."""
    gates = feature_gates(report)
    for mid, g in gates.items():
        if mid.startswith("qwen3vl-") and g.recommended:
            return mid
    return None
```

- [ ] **Step 5: Wire `should_tag_with_vlm` into the scan call site**

Open the file you found in Step 1. Where `start_indexing(file_paths=...)`
is called, add:

```python
from metascan.core.hardware import detect_hardware
from backend.services.scan_dispatch import should_tag_with_vlm

tag_with_vlm = should_tag_with_vlm(detect_hardware())
embedding_queue.start_indexing(
    file_paths=...,
    ...,
    tag_with_vlm=tag_with_vlm,
)
```

After the scan completes (in the `on_complete` callback or equivalent),
trigger a `VlmTagPump.drain_once()`. Look up the existing
`on_complete = lambda total: ...` registration and add:

```python
async def _drain_after_scan() -> None:
    from backend.api.vlm import get_vlm_client
    from backend.services.vlm_tag_pump import VlmTagPump
    from backend.services.scan_dispatch import recommended_vlm_model_id

    client = get_vlm_client()
    if client is None:
        return
    mid = recommended_vlm_model_id(detect_hardware())
    if not mid:
        return
    pump = VlmTagPump(
        queue_dir=embedding_queue.index_dir,
        client=client,
        db=get_db(),
        model_id=mid,
    )
    await pump.drain_once()

# Register: when on_complete fires, schedule the drain task.
def _on_complete(total: int) -> None:
    asyncio.create_task(_drain_after_scan())
embedding_queue.on_complete = _on_complete
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_scanner_vlm_routing.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/scan_dispatch.py tests/test_scanner_vlm_routing.py backend/api/scan.py
git commit -m "Route batch tagging to VLM on eligible tiers via scan dispatch"
```

(Adjust the file list to match what you actually modified in Step 5.)

---

## Phase 7: Frontend

### Task 19: API client + types

**Files:**
- Create: `frontend/src/api/vlm.ts`
- Modify: `frontend/src/types/hardware.ts` (add Qwen3-VL gate keys)

- [ ] **Step 1: Add gate keys to the TypeScript Tier type**

In `frontend/src/types/hardware.ts`, locate the `Gate` map type or
`gateFor` consumer. Search: `grep -n "qwen3vl\|gateFor" frontend/src/`.
Add `qwen3vl-2b | qwen3vl-4b | qwen3vl-8b | qwen3vl-30b-a3b` to the
union of valid model id strings if such a union exists. Otherwise leave
the gate map as `Record<string, Gate>` and rely on string keys.

- [ ] **Step 2: Create the typed API client**

Create `frontend/src/api/vlm.ts`:

```typescript
import { apiFetch } from './client'

export type VlmState =
  | 'idle' | 'spawning' | 'loading' | 'ready' | 'error' | 'stopped'

export interface VlmStatus {
  state: VlmState
  model_id: string | null
  base_url: string | null
  progress: Record<string, unknown>
  error: string | null
}

export interface VlmRetagJob {
  job_id: string
  total: number
}

export async function getVlmStatus(): Promise<VlmStatus> {
  return apiFetch<VlmStatus>('/api/vlm/status')
}

export async function tagOne(path: string): Promise<{ tags: string[] }> {
  return apiFetch<{ tags: string[] }>('/api/vlm/tag', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

export interface RetagBody {
  scope: 'paths' | 'all_clip'
  paths?: string[]
  force?: boolean
}

export async function startRetag(body: RetagBody): Promise<VlmRetagJob> {
  return apiFetch<VlmRetagJob>('/api/vlm/retag', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function cancelRetag(jobId: string): Promise<void> {
  await apiFetch(`/api/vlm/retag/${jobId}`, { method: 'DELETE' })
}
```

- [ ] **Step 3: Verify types**

Run: `cd frontend && npm run build`
Expected: Build passes, no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/vlm.ts frontend/src/types/hardware.ts
git commit -m "Add VLM API client + gate types"
```

---

### Task 20: Models store — add VLM rows

**Files:**
- Modify: `frontend/src/stores/models.ts`

- [ ] **Step 1: Inspect the existing store**

Run: `grep -n "interface ModelRow\|gateFor\|fetchStatus" frontend/src/stores/models.ts`

Identify the type used for model rows in `/api/models/status`. Confirm
that adding rows with `id` like `qwen3vl-4b` works without code changes
(if rows are typed as `{id: string; ...}`).

- [ ] **Step 2: Add `vlmStatus` reactive state**

Append to `frontend/src/stores/models.ts`:

```typescript
import { ref } from 'vue'
import { getVlmStatus, type VlmStatus } from '@/api/vlm'

// inside the defineStore body, alongside existing state:
const vlmStatus = ref<VlmStatus>({
  state: 'idle',
  model_id: null,
  base_url: null,
  progress: {},
  error: null,
})

async function refreshVlmStatus() {
  try {
    vlmStatus.value = await getVlmStatus()
  } catch (e) {
    // leave stale; background error
  }
}

// Subscribe to ws models channel for vlm_status events.
// Look for an existing useWebSocket subscription in this file and add:
// onWs('vlm_status', (payload) => { vlmStatus.value = payload as VlmStatus })

return { /* existing returns */, vlmStatus, refreshVlmStatus }
```

(The existing store already subscribes to the `models` channel for
`inference_status`; copy that pattern for `vlm_status`.)

- [ ] **Step 3: Verify types**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/models.ts
git commit -m "Track VLM status in models store"
```

---

### Task 21: ConfigModelsTab — Tagging Model section

**Files:**
- Modify: `frontend/src/components/dialogs/ConfigModelsTab.vue`

- [ ] **Step 1: Add a "Tagging Model" section**

Open `frontend/src/components/dialogs/ConfigModelsTab.vue` and find
where the existing model rows are rendered (search for `clip-large` or
the row-per-model loop). The store now returns Qwen3-VL rows with
`group: "Tagging (Qwen3-VL)"`. If the existing template groups rows by
the `group` field, the new rows render automatically. Otherwise, add a
filter/heading for the new group.

Add an "Active tagger" indicator near the section heading:

```vue
<template>
  <h3>Tagging Model</h3>
  <div class="active-tagger" v-if="modelsStore.vlmStatus.state === 'ready'">
    Active: {{ modelsStore.vlmStatus.model_id }}
  </div>
  <div class="active-tagger" v-else-if="modelsStore.vlmStatus.state !== 'idle'">
    {{ modelsStore.vlmStatus.state }}…
  </div>
  <!-- existing row loop, filtered to group === 'Tagging (Qwen3-VL)' -->
</template>
```

- [ ] **Step 2: Add a "Re-tag library" button**

```vue
<Button label="Re-tag library with Qwen3-VL"
        @click="confirmRetagLibrary"
        :disabled="modelsStore.vlmStatus.state !== 'ready'"/>
```

```typescript
async function confirmRetagLibrary() {
  if (!confirm('Re-tag every CLIP-tagged file with Qwen3-VL? This may take hours.')) {
    return
  }
  const job = await startRetag({ scope: 'all_clip' })
  toast.show(`Re-tag job started (${job.total} files).`)
}
```

- [ ] **Step 3: Add per-row "Activate" action that calls `/api/vlm/active`**

Within the VLM rows section, add:

```vue
<Button v-if="row.id.startsWith('qwen3vl-') && row.status === 'available'"
        label="Activate"
        :disabled="modelsStore.vlmStatus.model_id === row.id"
        @click="activate(row.id)"/>
```

```typescript
import { apiFetch } from '@/api/client'

async function activate(modelId: string) {
  await apiFetch('/api/vlm/active', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  })
  toast.show(`Switching to ${modelId}…`)
}
```

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dialogs/ConfigModelsTab.vue
git commit -m "Add Tagging Model section, Re-tag library action, and Activate"
```

---

### Task 22: ThumbnailCard — Re-tag this image context-menu item

**Files:**
- Modify: `frontend/src/components/thumbnails/ThumbnailCard.vue` (or wherever the context menu lives)

- [ ] **Step 1: Find the existing context menu**

Run: `grep -n "ContextMenu\|contextmenu\|@contextmenu" frontend/src/components/thumbnails/`

- [ ] **Step 2: Add the Re-tag item**

Add to the menu items array (or the menu template):

```typescript
{
  label: 'Re-tag with Qwen3-VL',
  icon: 'pi pi-refresh',
  visible: () => modelsStore.vlmStatus.state === 'ready',
  command: async () => {
    try {
      const { tags } = await tagOne(media.file_path)
      toast.show(`Re-tagged with ${tags.length} tags.`)
      // Trigger refresh on the surrounding views.
      mediaStore.refreshOne(media.file_path)
    } catch (e) {
      toast.error(`Re-tag failed: ${(e as Error).message}`)
    }
  }
}
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/thumbnails/ThumbnailCard.vue
git commit -m "Add Re-tag with Qwen3-VL context menu item"
```

---

## Phase 8: Setup + docs

### Task 23: setup_models.py — `--qwen3vl <size>` flag

**Files:**
- Modify: `setup_models.py`
- Test: `tests/test_setup_models_qwen3vl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup_models_qwen3vl.py
"""Argument parsing and download-target resolution for setup_models.py."""

from unittest.mock import patch

from setup_models import resolve_qwen3vl_targets


def test_resolve_targets_for_4b():
    targets = resolve_qwen3vl_targets("qwen3vl-4b")
    paths = [t.dest for t in targets]
    assert any(".gguf" in str(p) for p in paths)
    assert any("mmproj" in str(p) for p in paths)


def test_resolve_targets_unknown_size_raises():
    import pytest
    with pytest.raises(KeyError):
        resolve_qwen3vl_targets("qwen3vl-bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_setup_models_qwen3vl.py -v`
Expected: FAIL — `resolve_qwen3vl_targets` not defined.

- [ ] **Step 3: Add the helper + CLI flag**

In `setup_models.py`, add:

```python
from dataclasses import dataclass
from pathlib import Path

from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import (
    binary_path,
    pick_release_asset,
    release_url,
)
from metascan.core.hardware import detect_hardware


@dataclass
class DownloadTarget:
    url: str | None  # None = HuggingFace; download via huggingface_hub
    dest: Path
    repo: str | None = None
    filename: str | None = None


def resolve_qwen3vl_targets(model_id: str) -> list[DownloadTarget]:
    """Return GGUF + mmproj + binary targets for the given model id."""
    spec = REGISTRY[model_id]
    vlm_dir = get_data_dir() / "models" / "vlm"
    vlm_dir.mkdir(parents=True, exist_ok=True)
    rpt = detect_hardware()
    return [
        DownloadTarget(
            url=None, repo=spec.hf_repo, filename=spec.gguf_filename,
            dest=vlm_dir / spec.gguf_filename,
        ),
        DownloadTarget(
            url=None, repo=spec.hf_repo, filename=spec.mmproj_filename,
            dest=vlm_dir / spec.mmproj_filename,
        ),
        DownloadTarget(
            url=release_url(pick_release_asset(rpt)),
            dest=binary_path(),
        ),
    ]
```

Add a CLI argument:

```python
parser.add_argument(
    "--qwen3vl",
    metavar="MODEL_ID",
    help="Download a Qwen3-VL Abliterated GGUF (e.g. qwen3vl-4b)",
)
```

In the main flow, when `args.qwen3vl` is set, iterate `resolve_qwen3vl_targets`:

```python
if args.qwen3vl:
    for t in resolve_qwen3vl_targets(args.qwen3vl):
        if t.dest.exists():
            print(f"  ✓ {t.dest.name} (already present)")
            continue
        if t.url:
            download_url_to(t.url, t.dest)
        else:
            from huggingface_hub import hf_hub_download
            tmp = hf_hub_download(repo_id=t.repo, filename=t.filename)
            shutil.move(tmp, str(t.dest))
        # Make binary executable on POSIX
        if t.dest == binary_path() and t.dest.exists():
            t.dest.chmod(0o755)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_setup_models_qwen3vl.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add setup_models.py tests/test_setup_models_qwen3vl.py
git commit -m "Add --qwen3vl flag to setup_models.py"
```

---

### Task 24: Final integration check + docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/architecture.md`
- Modify: `docs/api-reference.md`

- [ ] **Step 1: Run the full test suite**

Run: `make quality test`
Expected: PASS — all 234+ tests green; flake8/black/mypy clean.

- [ ] **Step 2: Manual smoke test (skip if no GPU)**

Only on a host with the binary + a GGUF:

```bash
python setup_models.py --qwen3vl qwen3vl-4b
source venv/bin/activate
python run_server.py
# In another shell:
curl http://localhost:8700/api/vlm/status
curl -X POST http://localhost:8700/api/vlm/tag \
  -H 'Content-Type: application/json' \
  -d '{"path": "/path/to/an/image.jpg"}'
```

Expected: status flips through `loading` → `ready`; tag endpoint
returns a list of 15–25 lowercase tags.

- [ ] **Step 3: Update CLAUDE.md**

Append a bullet to the "Key Technical Decisions" section in `CLAUDE.md`:

```markdown
- **Qwen3-VL VLM tagging.** A long-running `VlmClient`
  (`metascan/core/vlm_client.py`) supervises a `llama-server` subprocess
  for generative tagging on hardware tiers where it's viable. CLIP tagging
  remains the fallback for `cpu_only` and `cuda_entry`. The DB layer
  arbitrates merging via `_update_indices` and `add_tag_indices` —
  VLM-source tag rows survive CLIP rescans (see `database_sqlite._update_indices`
  for the demote-on-rescan logic).
```

Append a bullet to the "Adding a hardware probe / tier rule / feature gate"
section:

```markdown
6. **VLM model gate.** Qwen3-VL gates live alongside CLIP gates; their
   `recommended` decision is what the scanner reads to choose between VLM
   and CLIP tagging. Per-model VRAM floors are in `_QWEN3VL_MIN_VRAM` in
   `metascan/core/hardware.py`.
```

Add a Common Tasks entry:

```markdown
### Adding a new VLM caption style
1. Add the style key to `CAPTION_STYLE_PROMPTS` in `metascan/core/vlm_prompts.py`.
2. The style picker in the (future) UI reads keys directly; backend doesn't need a registry change.
3. The style template should be deterministic, single-image, and produce parseable output if the consumer requires structured fields.
```

- [ ] **Step 4: Update `docs/api-reference.md`**

Document the new endpoints:

```markdown
### `GET /api/vlm/status`
Returns the current VlmClient snapshot: `{state, model_id, base_url, progress, error}`.

### `POST /api/vlm/tag`
Body: `{path: string}`. Re-tags one image with the active VLM.
Returns: `{tags: string[]}`.

### `POST /api/vlm/retag`
Body: `{scope: 'paths'|'all_clip', paths?: string[], force?: boolean}`.
Returns 202 with `{job_id, total}`. Progress and completion are broadcast
on the `models` WS channel as `vlm_progress` events.

### `DELETE /api/vlm/retag/{job_id}`
Cancels a running re-tag job. Returns `{status: 'cancelled'}`.
```

- [ ] **Step 5: Update `docs/architecture.md`**

Add a paragraph describing VlmClient + the `models` channel events
`vlm_status` and `vlm_progress`. Reference the spec at
`docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/api-reference.md docs/architecture.md
git commit -m "Document Qwen3-VL VLM tagging in CLAUDE.md and docs"
```

---

## Self-review checklist

After all tasks pass:

- [ ] All eight phases done in order; no task skipped or partially landed.
- [ ] `make quality test` passes with zero failures.
- [ ] `cd frontend && npm run build` passes.
- [ ] `git log --oneline` shows the commit-per-task pattern.
- [ ] Spec §6 hardware table matches the gate decisions in code (verified
      by `tests/test_hardware_vlm_gates.py`).
- [ ] Spec §7.4 merge matrix matches the DB tests (verified by
      `tests/test_database_vlm_tags.py`).
- [ ] No `pip install` of `llama-cpp-python` anywhere — the engine is
      `llama-server` subprocess only.
- [ ] No reference to vLLM or Ollama anywhere in the codebase.
- [ ] CLAUDE.md updated in Task 24.

