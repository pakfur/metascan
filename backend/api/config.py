"""Configuration management endpoints."""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.config import load_app_config, save_app_config
from backend.dependencies import get_db
from backend.services import upscale_folders

router = APIRouter(prefix="/api", tags=["config"])


# The sections the library's folders are read from.
_LIBRARY_KEYS = frozenset({"directories", "comfy", "i2v", "t2i", "upscale"})


def _check_original_folder(current: Dict[str, Any], body: Dict[str, Any]) -> None:
    """Refuse a change that puts the Original folder in the library, or that
    moves it past the originals already there. A conflict that was already
    there is not this save's fault, and blocking it would block every save."""
    merged = {**current, **body}
    before = upscale_folders.original_folder_conflict(current)
    after = upscale_folders.original_folder_conflict(merged)
    if after is not None and after != before:
        raise HTTPException(status_code=400, detail=after)
    moved = upscale_folders.original_root(merged) != upscale_folders.original_root(
        current
    )
    if moved and get_db().list_upscale_records():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "original_folder_has_files",
                "message": "The Original folder holds files: move it from "
                "Configuration, Directories.",
            },
        )


@router.get("/config")
async def get_config():
    """Get the current application configuration."""
    return load_app_config()


@router.put("/config")
async def update_config(body: dict):
    """Update the application configuration."""
    current = load_app_config()
    if _LIBRARY_KEYS.intersection(body):
        await asyncio.to_thread(_check_original_folder, current, body)
    current.update(body)
    save_app_config(current)
    return current


@router.get("/config/themes")
async def list_themes():
    """List available themes."""
    # qt-material themes that have web equivalents
    return {
        "themes": [
            "light_blue_500",
            "dark_blue_500",
            "light_amber_500",
            "dark_amber_500",
            "light_teal_500",
            "dark_teal_500",
            "light_purple_500",
            "dark_purple_500",
        ]
    }


def _list_dirs(path: Path) -> List[Dict[str, str]]:
    """Visible subdirectories, case-insensitively sorted. Entries that
    cannot be stat'ed (dangling links, permission holes) are skipped."""
    dirs: List[Dict[str, str]] = []
    with os.scandir(path) as it:
        for entry in it:
            if entry.name.startswith("."):
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            dirs.append({"name": entry.name, "path": str(path / entry.name)})
    dirs.sort(key=lambda d: d["name"].lower())
    return dirs


@router.get("/config/browse")
def browse_directories(path: Optional[str] = None) -> Dict[str, Any]:
    """One level of the SERVER's directory tree, for the directory picker.

    A browser cannot hand the backend a filesystem path (an
    ``<input type=file>`` yields file contents, never a server-side
    location), so the picker walks the server's tree through this route.
    Directories only -- no files are listed and nothing is read. No path
    starts at the server user's home directory.

    Deliberately a sync route: FastAPI runs it in the threadpool, keeping
    stat/scandir on a slow mount (WSL2 /mnt/<drive>) off the event loop.
    """
    if path is None or not path.strip():
        target = Path.home()
    else:
        target = Path(path.strip())
        if not target.is_absolute():
            raise HTTPException(status_code=400, detail="path must be absolute")
    target = target.resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"No such directory: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
    try:
        dirs = _list_dirs(target)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail=f"Permission denied: {target}"
        ) from exc
    parent = target.parent
    return {
        "path": str(target),
        "parent": str(parent) if parent != target else None,
        "dirs": dirs,
    }
