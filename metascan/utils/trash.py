"""Move generated files to the OS trash (unlink as a fallback)."""

import logging
from pathlib import Path
from typing import List

from send2trash import send2trash

logger = logging.getLogger(__name__)


def trash_file(path: Path) -> None:
    """Move one file to the OS trash, unlinking it when the trash refuses
    (no trash on that drive, say). A missing file is fine. Raises OSError
    when the file could not be removed either way: the T2I / I2V deletes
    trash before their DB delete, so the row is kept."""
    if not path.exists():
        return
    try:
        send2trash(str(path))
        return
    except Exception:
        logger.debug("send2trash refused %s; unlinking it", path, exc_info=True)
    path.unlink(missing_ok=True)


def remove_files_to_trash(paths: List[str]) -> None:
    """Move purged generated files to the OS trash (unlink as a fallback).

    Failures are logged and skipped -- the DB rows are already gone by the
    time this runs (the storyboard purges), so a stray file on disk is a
    cosmetic leftover, not corruption (a rescan of a watched directory
    would re-ingest it; storyboard output dirs live under
    comfy.output_root, which isn't scanned).
    """
    for raw in paths:
        path = Path(raw)
        try:
            trash_file(path)
        except OSError:
            logger.warning("Could not remove purged image %s", path, exc_info=True)
