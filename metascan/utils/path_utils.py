"""
Path utilities for cross-platform path translation between Windows and WSL/POSIX.

Paths are stored in POSIX format (forward slashes, /mnt/x/ style for Windows drives)
and converted to the native platform format when read.
"""

import re
import sys
from pathlib import Path
from typing import Union


def is_windows() -> bool:
    """Check if the current platform is Windows."""
    return sys.platform == "win32"


def to_posix_path(path: Union[str, Path]) -> str:
    """
    Convert a path to POSIX format for storage.

    Windows paths like C:\\Users\\foo are converted to /mnt/c/Users/foo.
    POSIX paths are returned unchanged (with forward slashes).

    Args:
        path: A file path string or Path object

    Returns:
        POSIX-formatted path string
    """
    path_str = str(path)

    # Check if it's a Windows-style path (drive letter)
    # Match patterns like C:\ or C:/
    windows_drive_pattern = r"^([A-Za-z]):[/\\](.*)$"
    match = re.match(windows_drive_pattern, path_str)

    if match:
        drive_letter = match.group(1).lower()
        rest_of_path = match.group(2)
        # Convert backslashes to forward slashes
        rest_of_path = rest_of_path.replace("\\", "/")
        return f"/mnt/{drive_letter}/{rest_of_path}"

    # Already POSIX or relative path - just normalize slashes
    return path_str.replace("\\", "/")


def to_native_path(path: Union[str, Path]) -> str:
    """
    Convert a POSIX path to the native platform format.

    On Windows: /mnt/c/Users/foo -> C:\\Users\\foo
    On POSIX systems: paths are returned unchanged

    Args:
        path: A file path string or Path object (expected to be in POSIX format)

    Returns:
        Native platform path string
    """
    path_str = str(path)

    if not is_windows():
        # On POSIX systems, just return as-is (ensure forward slashes)
        return path_str.replace("\\", "/")

    # On Windows, convert /mnt/x/... to X:\...
    wsl_path_pattern = r"^/mnt/([a-zA-Z])/(.*)$"
    match = re.match(wsl_path_pattern, path_str)

    if match:
        drive_letter = match.group(1).upper()
        rest_of_path = match.group(2)
        # Convert forward slashes to backslashes for Windows
        rest_of_path = rest_of_path.replace("/", "\\")
        return f"{drive_letter}:\\{rest_of_path}"

    # Not a WSL-style path, just normalize for Windows
    return path_str.replace("/", "\\")


def to_native_path_object(path: Union[str, Path]) -> Path:
    """
    Convert a POSIX path to a native Path object.

    Args:
        path: A file path string or Path object (expected to be in POSIX format)

    Returns:
        Native platform Path object
    """
    return Path(to_native_path(path))
