"""Move purged generated files to the OS trash (unlink as a fallback)."""

import logging
from pathlib import Path
from typing import List

from send2trash import send2trash

logger = logging.getLogger(__name__)


def remove_files_to_trash(paths: List[str]) -> None:
    """Move purged generated files to the OS trash (unlink as a fallback).

    Mirrors MediaService.delete_media's send2trash behavior. Failures are
    logged and skipped -- the DB rows are already gone by the time this
    runs, so a stray file on disk is a cosmetic leftover, not corruption
    (a rescan of a watched directory would re-ingest it; storyboard
    output dirs live under comfy.output_root, which isn't scanned).
    """
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        try:
            send2trash(str(path))
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove purged image %s", path, exc_info=True)
