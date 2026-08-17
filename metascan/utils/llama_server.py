"""Path resolver + release-asset picker for the bundled llama-server binary.

The binary is downloaded once at install time (or on first VLM use) by
``setup_models.py`` from the ``ggml-org/llama.cpp`` GitHub releases (the repo
was transferred from ``ggerganov/llama.cpp`` in 2025; the old URL still
redirects but the new path is canonical). This module is the single source of
truth for "where the binary lives" and "which upstream asset matches the
current host."

Note on Linux+CUDA: upstream does not ship a Linux CUDA prebuilt — only
Vulkan and CPU. A Linux host with an NVIDIA card therefore falls back to the
Vulkan build when a real Vulkan device is detected, else CPU. Users who want
CUDA acceleration on Linux must build llama.cpp from source.
"""

from __future__ import annotations

from pathlib import Path

from metascan.core.hardware import HardwareReport, detect_hardware
from metascan.utils.app_paths import get_data_dir

# Pinned upstream release. Bump only deliberately — model compatibility and
# command-line flags evolve between releases. b10456 (2026-08-17) is pinned
# because Qwen3.8's Gated DeltaNet CUDA kernels were broken before ≈b10450:
# older builds load the model, use normal VRAM, and silently emit corrupted
# tokens (ggml-org/llama.cpp discussion #27164). b10456 retains Qwen3-VL
# support. From this release line Linux/macOS assets are ``.tar.gz`` and
# Windows stays ``.zip`` — ``setup_models._ensure_target`` handles both.
LLAMA_CPP_RELEASE = "b10456"


def binary_filename() -> str:
    """Platform-correct filename of the llama-server executable."""
    rpt = detect_hardware()
    if rpt.os == "Windows":
        return "llama-server.exe"
    return "llama-server"


def binary_path() -> Path:
    """Absolute path to the installed llama-server binary.

    A user-built binary at ``data/bin/local/<name>`` takes precedence over
    the bundled binary downloaded from the upstream release. This is the
    escape hatch for hosts where the upstream prebuilt either doesn't
    exist (Linux + CUDA) or doesn't include the accelerator the user
    wants. ``scripts/build_llama_server.sh`` populates that directory.

    Because ``_vlm_status_rows`` and the bundle downloader both check
    ``binary_path().exists()``, the override naturally suppresses the
    bundled-asset download — see ``docs/build-llama-server.md``.
    """
    name = binary_filename()
    base = get_data_dir() / "bin"
    local = base / "local" / name
    if local.exists():
        return local
    return base / name


def pick_release_asset(report: HardwareReport) -> str:
    """Return the upstream release-asset filename matching ``report``.

    Picks the most-accelerated build that upstream actually publishes:
    CUDA > Vulkan > CPU on Windows; Vulkan > CPU on Linux (no Linux CUDA
    prebuilt exists upstream); Metal-bundled arm64 build on macOS arm64.
    """
    rel = LLAMA_CPP_RELEASE
    if report.os == "Darwin":
        if report.machine != "arm64":
            raise NotImplementedError(
                "macOS Intel (x86_64) llama-server builds are not published "
                "by upstream; only macOS arm64 is supported."
            )
        return f"llama-{rel}-bin-macos-arm64.tar.gz"
    if report.os == "Windows":
        if report.cuda is not None:
            # b6500 ships only the cu12.4 variant for Windows CUDA.
            return f"llama-{rel}-bin-win-cuda-12.4-x64.zip"
        if report.vulkan and report.vulkan.has_real_device:
            return f"llama-{rel}-bin-win-vulkan-x64.zip"
        return f"llama-{rel}-bin-win-cpu-x64.zip"
    # Linux: no upstream CUDA prebuilt — Vulkan first, then CPU.
    if report.vulkan and report.vulkan.has_real_device:
        return f"llama-{rel}-bin-ubuntu-vulkan-x64.tar.gz"
    return f"llama-{rel}-bin-ubuntu-x64.tar.gz"


def release_url(asset: str) -> str:
    """GitHub releases download URL for the given asset filename."""
    return (
        f"https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{LLAMA_CPP_RELEASE}/{asset}"
    )


__all__ = [
    "binary_filename",
    "binary_path",
    "pick_release_asset",
    "release_url",
    "LLAMA_CPP_RELEASE",
]
