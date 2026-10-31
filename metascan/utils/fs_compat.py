"""OS compatibility layer for the always-on import (spec section 12).

Everything that differs per OS lives here; callers use these functions only.
"""

import sys
import unicodedata

_CASE_INSENSITIVE_PLATFORMS = ("win32", "darwin")

# Windows shell droppings, compared case-insensitively.
_IGNORED_NAMES = frozenset({"thumbs.db", "desktop.ini"})

# Same set on every OS; ".download" is macOS (Safari) but harmless elsewhere.
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")


def path_key(path: str, platform: str = sys.platform) -> str:
    """Comparison key for a path: NFC everywhere, casefolded on win32/darwin."""
    key = unicodedata.normalize("NFC", path)
    if platform in _CASE_INSENSITIVE_PLATFORMS:
        key = key.casefold()
    return key


def is_ignored_name(name: str) -> bool:
    """True for OS metadata files: dotfiles (.DS_Store, ._*, .Spotlight-V100),
    Thumbs.db, desktop.ini and Office lock files (~$*)."""
    return (
        name.startswith(".")
        or name.startswith("~$")
        or name.casefold() in _IGNORED_NAMES
    )


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
