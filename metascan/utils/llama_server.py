"""Path resolver and downloader for the bundled llama-server binary.

Stub. Full implementation in Task 3.
"""

from __future__ import annotations

from pathlib import Path

from metascan.utils.app_paths import get_data_dir


def binary_path() -> Path:
    """Return the platform-specific path where llama-server is/will be installed."""
    return get_data_dir() / "bin" / "llama-server"
