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
        if report.machine != "arm64":
            raise NotImplementedError(
                "macOS Intel (x86_64) llama-server builds are not published "
                "by upstream; only macOS arm64 is supported."
            )
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
