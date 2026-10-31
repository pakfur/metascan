"""OS compatibility layer for the always-on import (spec section 12).

Everything that differs per OS lives here; callers use these functions only.
"""

import sys
import unicodedata

_CASE_INSENSITIVE_PLATFORMS = ("win32", "darwin")

# OS junk names, compared case-insensitively.
_IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store", ".spotlight-v100"})

# Same set on every OS; ".download" is macOS (Safari) but harmless elsewhere.
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")


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
