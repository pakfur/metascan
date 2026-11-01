"""OS compatibility layer for the always-on import (spec section 12).

Everything that differs per OS lives here; callers use these functions only.
"""

import re
import sys
import unicodedata
from typing import Callable, Optional

_CASE_INSENSITIVE_PLATFORMS = ("win32", "darwin")

# OS junk names, compared case-insensitively.
_IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store", ".spotlight-v100"})

# Same set on every OS; ".download" is macOS (Safari) but harmless elsewhere.
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")


# Linux filesystems whose changes can happen where inotify never hears of them:
# a Windows drive under WSL2 (the watch is accepted and then nothing arrives,
# measured on /mnt/d, even for writes from inside WSL), or another machine.
_SILENT_FSTYPES = frozenset(
    {"9p", "v9fs", "drvfs", "nfs", "nfs4", "cifs", "smb3", "smbfs", "virtiofs"}
    | {"vboxsf", "fuse.sshfs", "fuse.rclone"}
)

_DRIVE_REMOTE = 4  # GetDriveTypeW


def path_key(path: str, platform: str = sys.platform) -> str:
    """Comparison key for a path: NFC everywhere, casefolded on win32/darwin."""
    key = unicodedata.normalize("NFC", path)
    if platform in _CASE_INSENSITIVE_PLATFORMS:
        # casefold can leave the string un-normalised, so normalise again.
        key = unicodedata.normalize("NFC", key.casefold())
    return key


def is_ignored_name(name: str) -> bool:
    """True for OS junk on every OS: .DS_Store, ._* (AppleDouble),
    .Spotlight-V100, Thumbs.db, desktop.ini (case-insensitive) and ~$* lock
    files. Other dotfiles (.sketch.png) are real media and are not ignored."""
    return name.startswith(("._", "~$")) or name.casefold() in _IGNORED_NAMES


def is_partial_download(name: str) -> bool:
    """True for a file a browser or copy tool is still writing."""
    return name.casefold().endswith(_PARTIAL_SUFFIXES)


def can_open_for_read(path: str) -> bool:
    """True when the file can be opened for reading right now.

    A Windows sharing violation surfaces as PermissionError; a missing file or
    any other OSError is also False. Contents are not read.
    """
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _read_mountinfo() -> str:
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _mount_fstype(path: str, mountinfo: str) -> Optional[str]:
    """The filesystem type of the deepest mount holding ``path``."""
    best, best_len = None, -1
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split(" ")
        if not sep or len(fields) < 5 or not right:
            continue
        # mountinfo escapes space, tab, newline and backslash as \ooo.
        point = re.sub(
            r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]
        ).rstrip("/")
        if (path == point or path.startswith(point + "/")) and len(point) > best_len:
            best, best_len = right.split(" ")[0], len(point)
    return best


def _windows_drive_type(root: str) -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]


def native_watch_reliable(
    path: str,
    platform: str = sys.platform,
    mountinfo: Optional[str] = None,
    drive_type: Callable[[str], int] = _windows_drive_type,
) -> bool:
    """Whether a native watcher on ``path`` can be expected to hear changes.
    False leaves the folder to the poller alone, which always works."""
    if platform == "win32":
        if path.startswith(("\\\\", "//")):
            return False
        try:
            return drive_type(path[:3]) != _DRIVE_REMOTE
        except OSError:
            return False
    if platform == "darwin":
        return True
    fstype = _mount_fstype(path, _read_mountinfo() if mountinfo is None else mountinfo)
    return fstype is not None and fstype not in _SILENT_FSTYPES
