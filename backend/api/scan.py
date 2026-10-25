"""Scan management endpoints."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import similarity as similarity_api
from backend.config import DirectoryConfig, load_app_config, get_directories
from backend.dependencies import get_db, get_thumbnail_cache
from backend.ws.manager import ws_manager
from backend.services.taste_snapshot import TASTE_SNAPSHOT_FILENAME  # noqa: F401
from backend.services.taste_snapshot import (
    PendingSnapshotUnreadable,
    apply_pending_taste_snapshot,
    merge_pending_into_snapshot,
    restore_and_persist,
    snapshot_file_path,
    write_snapshot_file,
)
from metascan.core.scanner import Scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])

# Track the running scan task so it can be cancelled
_scan_task: Optional[asyncio.Task] = None
_cancel_requested = False


def _data_dir(db: Any) -> Optional[Path]:
    base = getattr(db, "db_path", None)
    return Path(base) if base is not None else None


# A configured scan folder that is missing (an unmounted drive, a renamed
# folder) hides its files; it does not mean they were deleted. A full clean
# with one missing never completes, and the stale cleanup leaves its rows.
SCAN_FOLDERS_MISSING = "scan_folders_missing"
_REFUSED_LOG = "Full clean refused, nothing deleted: scan folder(s) missing: %s"


def _folder_missing(filepath: str) -> bool:
    """True when a configured scan folder can't be seen right now: it does
    not exist, or its stat fails (I/O error, permissions, a dead network
    share)."""
    try:
        return not Path(filepath).exists()
    except OSError:
        return True


def _missing_scan_folders(directories: Sequence[DirectoryConfig]) -> List[str]:
    """The configured scan folders missing now, each once, in config order."""
    return list(
        dict.fromkeys(d.filepath for d in directories if _folder_missing(d.filepath))
    )


def _missing_folders_message(folders: Sequence[str]) -> str:
    if len(folders) == 1:
        return (
            f"Full clean refused: the scan folder {folders[0]} is missing, so "
            "its images could not be re-imported. Reconnect the drive or "
            "remove the folder from the scan list (Configuration → "
            "Directories), then try again. Nothing was deleted."
        )
    return (
        f"Full clean refused: {len(folders)} scan folders are missing "
        f"({', '.join(folders)}), so their images could not be re-imported. "
        "Reconnect the drives or remove the folders from the scan list "
        "(Configuration → Directories), then try again. Nothing was deleted."
    )


def _path_key(path: str) -> str:
    """``path`` normalised the way the OS compares paths (separators, case
    on Windows) and ending in one separator, so a path lies under a folder
    exactly when its key starts with the folder's ("lib" never matches
    "library")."""
    return os.path.normcase(os.path.normpath(path)).rstrip(os.sep) + os.sep


def _stale_paths_to_clean(
    existing_db_paths: Iterable[str], directories: Sequence[DirectoryConfig]
) -> Tuple[List[Path], Dict[str, int]]:
    """The rows whose files are gone, split into the ones to delete and, for
    each configured scan folder that is missing, how many of its rows are
    left alone. A row under no configured folder (one removed from the
    config) is deleted as before. The folders are checked after the
    per-file pass, so a drive lost during it counts as missing."""
    stale = [p for p in existing_db_paths if not Path(p).exists()]
    if not stale:
        return [], {}
    missing = [(f, _path_key(f)) for f in _missing_scan_folders(directories)]
    to_delete: List[Path] = []
    left_alone: Dict[str, int] = {}
    for p in stale:
        key = _path_key(p)
        folder = next((f for f, prefix in missing if key.startswith(prefix)), None)
        if folder is None:
            to_delete.append(Path(p))
        else:
            left_alone[folder] = left_alone.get(folder, 0) + 1
    return to_delete, left_alone


class ScanRequest(BaseModel):
    full_cleanup: bool = False
    full_clean: bool = False  # destructive: truncates DB, preserves ratings


@router.post("/prepare")
async def prepare_scan():
    """Count files in configured directories and return stats for confirmation."""
    config = load_app_config()
    directories = get_directories(config)

    supported_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp4",
        ".webm",
        ".mov",
        ".bmp",
    }
    total_files = 0
    dir_stats = []

    for d in directories:
        dir_path = Path(d.filepath)
        if not dir_path.exists():
            continue
        count = 0
        if d.search_subfolders:
            for root, _, files in os.walk(dir_path):
                count += sum(
                    1 for f in files if Path(f).suffix.lower() in supported_extensions
                )
        else:
            count = sum(
                1
                for f in dir_path.iterdir()
                if f.is_file() and f.suffix.lower() in supported_extensions
            )
        total_files += count
        dir_stats.append(
            {
                "path": d.filepath,
                "file_count": count,
                "search_subfolders": d.search_subfolders,
            }
        )

    db = get_db()
    existing_count = len(await asyncio.to_thread(db.get_existing_file_paths))

    return {
        "directories": dir_stats,
        "total_files": total_files,
        "existing_in_db": existing_count,
    }


@router.post("/start")
async def start_scan(body: ScanRequest):
    """Begin a scan. Progress is reported via WebSocket.

    A full clean deletes every row first and gets a file back only by
    re-importing it, so it is refused (409 ``scan_folders_missing``) while a
    configured scan folder is missing; an ordinary scan skips that folder."""
    global _scan_task, _cancel_requested

    if _scan_task and not _scan_task.done():
        raise HTTPException(status_code=409, detail="Scan already in progress")

    if body.full_clean:
        missing = _missing_scan_folders(get_directories(load_app_config()))
        if missing:
            logger.warning(_REFUSED_LOG, ", ".join(missing))
            raise HTTPException(
                status_code=409,
                detail={
                    "code": SCAN_FOLDERS_MISSING,
                    "folders": missing,
                    "message": _missing_folders_message(missing),
                },
            )

    _cancel_requested = False
    _scan_task = asyncio.create_task(_run_scan(body.full_cleanup, body.full_clean))
    return {"status": "started"}


@router.post("/cancel")
async def cancel_scan():
    """Cancel a running scan."""
    global _cancel_requested

    if not _scan_task or _scan_task.done():
        raise HTTPException(status_code=409, detail="No scan in progress")

    _cancel_requested = True
    return {"status": "cancelling"}


async def _run_scan(full_cleanup: bool, full_clean: bool = False) -> None:  # noqa: C901
    """Run the scan in a background task with WebSocket progress updates."""
    db = get_db()
    thumbnail_cache = get_thumbnail_cache()
    config = load_app_config()
    directories = get_directories(config)

    scanner = Scanner(db, thumbnail_cache)

    await ws_manager.broadcast("scan", "started", {"full_clean": full_clean})

    # Configured folders found missing at any check of this run, in config
    # order. A full clean that saw one never completes (no tombstones).
    missing_folders: List[str] = []

    def _note_missing(folders: Iterable[str]) -> None:
        for folder in folders:
            if folder not in missing_folders:
                missing_folders.append(folder)

    # Snapshot the taste state (judgments, category links, favorites)
    # before any destructive operation: truncating media cascades away
    # media_judgments and media_categories. The snapshot is written to
    # disk as *pending state* (backend/services/taste_snapshot.py) before
    # the truncate; if either step fails the full clean is aborted before
    # anything is deleted. Leftovers of an earlier interrupted full clean
    # are first applied, and whatever is still pending is merged in so it
    # isn't overwritten.
    data_dir = _data_dir(db)
    taste_snapshot: Optional[Dict[str, Any]] = None
    if full_clean:
        # start_scan refuses this up front (409); checked again before
        # anything is written, for a folder lost since or a direct caller.
        missing = _missing_scan_folders(directories)
        if missing:
            logger.warning(_REFUSED_LOG, ", ".join(missing))
            await ws_manager.broadcast(
                "scan",
                "error",
                {
                    "code": SCAN_FOLDERS_MISSING,
                    "folders": missing,
                    "message": _missing_folders_message(missing),
                },
            )
            return
        try:
            if data_dir is None:
                raise RuntimeError("database has no data directory")
            # raise_unreadable: a pending file we can't read may hold valid
            # ratings -- abort instead of overwriting it below.
            await asyncio.to_thread(apply_pending_taste_snapshot, db, data_dir, True)
            taste_snapshot = await asyncio.to_thread(db.export_taste_state)
            taste_snapshot = await asyncio.to_thread(
                merge_pending_into_snapshot, data_dir, taste_snapshot
            )
            await asyncio.to_thread(write_snapshot_file, data_dir, taste_snapshot)
        except PendingSnapshotUnreadable as pend_err:
            logger.error(f"Full clean aborted: {pend_err}")
            await ws_manager.broadcast(
                "scan",
                "error",
                {
                    "message": (
                        "Full clean aborted: the pending ratings file from an "
                        "earlier interrupted full clean could not be read, so "
                        f"nothing was deleted ({pend_err})"
                    )
                },
            )
            return
        except Exception as snap_err:
            logger.error(
                f"Full clean aborted: could not snapshot taste state: {snap_err}",
                exc_info=True,
            )
            await ws_manager.broadcast(
                "scan",
                "error",
                {"message": f"Full clean aborted (taste snapshot failed): {snap_err}"},
            )
            return
        await asyncio.to_thread(db.truncate_all_data)
        await ws_manager.broadcast(
            "scan",
            "phase_changed",
            {
                "phase": "full_clean",
                "favorites_preserved": len(taste_snapshot["favorites"]),
                "judgments_preserved": len(taste_snapshot["judgments"]),
            },
        )

    restore_done = False

    async def _restore_taste_from_snapshot(completed: bool) -> None:
        """Put the snapshot back for paths that re-appeared. On a completed
        run the rest is tombstoned and the pending file deleted; on cancel,
        error or a missing scan folder the rest stays pending (file
        rewritten, no tombstones).
        Runs at most once per scan; no-op when no snapshot was taken.
        Never called on CancelledError (shutdown): the pending file is
        left as written and applied at the next startup."""
        nonlocal restore_done
        if restore_done or taste_snapshot is None or data_dir is None:
            return
        restore_done = True
        try:
            counts = await asyncio.to_thread(
                restore_and_persist, db, data_dir, taste_snapshot, completed
            )
            logger.info(
                "Full clean: taste state restored for %d paths, %d %s",
                counts["restored"],
                counts["missing"],
                "tombstoned" if completed else "still pending",
            )
            await ws_manager.broadcast("scan", "taste_restored", counts)
        except Exception as restore_err:
            logger.error(
                f"Failed to restore taste state after scan (pending snapshot "
                f"kept at {snapshot_file_path(data_dir)}): {restore_err}",
                exc_info=True,
            )

    async def _apply_pending() -> None:
        """Restore anything pending from an earlier interrupted full clean
        that this scan brought back. Never tombstones, never raises."""
        if data_dir is None:
            return
        try:
            await asyncio.to_thread(apply_pending_taste_snapshot, db, data_dir)
        except Exception as e:
            logger.error(f"Applying pending taste snapshot failed: {e}", exc_info=True)

    try:
        total_processed = 0
        total_dirs = len(directories)

        for dir_idx, dir_config in enumerate(directories):
            if _cancel_requested:
                await _restore_taste_from_snapshot(completed=False)
                await _apply_pending()
                await ws_manager.broadcast("scan", "cancelled", {})
                return

            dir_path = Path(dir_config.filepath)
            if not dir_path.exists():
                logger.warning(
                    "Scan folder %s is missing; skipped", dir_config.filepath
                )
                _note_missing([dir_config.filepath])
                continue

            await ws_manager.broadcast(
                "scan",
                "progress",
                {
                    "phase": "scanning",
                    "directory": dir_config.filepath,
                    "dir_current": dir_idx + 1,
                    "dir_total": total_dirs,
                },
            )

            def progress_callback(current, total, file_path):
                ws_manager.broadcast_sync(
                    "scan",
                    "file_progress",
                    {"current": current, "total": total, "file": str(file_path)},
                )
                return not _cancel_requested

            processed = await asyncio.to_thread(
                scanner.scan_directory,
                str(dir_path),
                dir_config.search_subfolders,
                progress_callback,
                False,  # not full_scan - skip existing
            )
            total_processed += processed

        # Stale cleanup
        stale_count = 0
        if full_cleanup:
            await ws_manager.broadcast(
                "scan", "phase_changed", {"phase": "stale_cleanup"}
            )
            existing_db_paths = await asyncio.to_thread(db.get_existing_file_paths)
            # A row under a configured folder that is missing now is left
            # alone: its file is out of sight, not deleted.
            stale_paths, left_alone = await asyncio.to_thread(
                _stale_paths_to_clean, existing_db_paths, directories
            )
            for folder, count in left_alone.items():
                logger.warning(
                    "Stale cleanup: scan folder %s is missing; left its %d rows "
                    "in the library",
                    folder,
                    count,
                )
            _note_missing(left_alone)
            if stale_paths:
                stale_count = await asyncio.to_thread(
                    db.delete_media_batch, stale_paths
                )

        if full_clean:
            # Checked once more: a drive lost during its own folder's scan
            # brought back only part of it.
            _note_missing(_missing_scan_folders(directories))
            if missing_folders:
                logger.warning(
                    "Full clean incomplete: scan folder(s) %s missing; the "
                    "ratings of images that did not come back stay pending, "
                    "none are tombstoned",
                    ", ".join(missing_folders),
                )

        # Restore ratings for files that re-appeared in the rescan. A cancel
        # that landed during the last directory still leaves the loop
        # normally -- it must not tombstone what wasn't re-imported yet, and
        # neither may a run that could not see one of its folders.
        await _restore_taste_from_snapshot(
            completed=not _cancel_requested and not missing_folders
        )
        await _apply_pending()

        # Auto-trigger embedding build when:
        #   - the scan was not cancelled,
        #   - config has a similarity block with auto_index_after_scan true (default true),
        #   - at least one file is missing an embedding.
        sim = config.get("similarity") if isinstance(config, dict) else None
        if (
            not _cancel_requested
            and sim is not None
            and sim.get("auto_index_after_scan", True)
        ):
            unembedded = await asyncio.to_thread(db.get_unembedded_file_paths)
            if unembedded:
                try:
                    await similarity_api.build_index(rebuild=False)
                except Exception as e:
                    logger.warning(f"Auto-trigger embedding build failed: {e}")

        await ws_manager.broadcast(
            "scan",
            "complete",
            {
                "processed": total_processed,
                "stale_removed": stale_count,
                "missing_folders": missing_folders,
            },
        )

    except Exception as e:
        # Best-effort restore so users don't silently lose ratings on a
        # failed full-clean rescan.
        await _restore_taste_from_snapshot(completed=False)
        await _apply_pending()
        logger.error(f"Scan error: {e}", exc_info=True)
        await ws_manager.broadcast("scan", "error", {"message": str(e)})
