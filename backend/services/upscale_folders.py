"""Where upscaling keeps its files, and the rule that keeps them out of the
library.

The Original folder holds the originals of upscaled files, which are not
library media and are read only to compare, re-upscale and revert. Import
watches every scan folder and output root, so a folder inside one would turn
them into library items. The candidates' staging folder has the same problem
but is not the owner's to move, so a run refuses instead."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import get_directories, get_upscale_config
from metascan.utils.app_paths import get_data_dir

DEFAULT_ORIGINALS = "_original"
STAGING = "upscale_candidates"

CODE_ORIGINAL_IN_LIBRARY = "original_folder_in_library"
CODE_STAGING_IN_LIBRARY = "staging_folder_in_library"


def original_root(config: Dict[str, Any]) -> Path:
    configured = get_upscale_config(config)["original_root"]
    return Path(configured) if configured else get_data_dir() / DEFAULT_ORIGINALS


def staging_root() -> Path:
    return get_data_dir() / STAGING


def _library_folders(config: Dict[str, Any]) -> List[str]:
    from backend.api import scan

    folders = [d.filepath for d in get_directories(config)]
    return list(dict.fromkeys([*folders, *scan._generated_output_roots(config)]))


def _clash(path: Path, config: Dict[str, Any]) -> Optional[str]:
    """The library folder ``path`` lies under, or that lies under it."""
    from backend.api import scan

    key = scan._absolute_key(str(path))
    for folder in _library_folders(config):
        other = scan._absolute_key(folder)
        if key.startswith(other) or other.startswith(key):
            return folder
    return None


def original_folder_conflict(config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Why this configuration's Original folder cannot be used, or None."""
    root = original_root(config)
    folder = _clash(root, config)
    if folder is None:
        return None
    return {
        "code": CODE_ORIGINAL_IN_LIBRARY,
        "message": (
            f"The Original folder {root} overlaps the library folder {folder}. "
            "Originals must stay out of the library: choose a folder outside "
            "it in Configuration, Directories."
        ),
    }


def staging_conflict(config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Why results cannot be staged with this configuration, or None."""
    root = staging_root()
    folder = _clash(root, config)
    if folder is None:
        return None
    return {
        "code": CODE_STAGING_IN_LIBRARY,
        "message": (
            f"Metascan's data folder overlaps the library folder {folder}, so "
            "upscale results would be imported as library files. Remove that "
            "folder from the scan list, or move Metascan's data folder."
        ),
    }
