"""Scan management endpoints."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    pending_snapshot_paths,
    restore_and_persist,
    snapshot_file_path,
    snapshot_paths,
    write_snapshot_file,
)
from metascan.core.scanner import Scanner
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])

# Track the running scan task so it can be cancelled
_scan_task: Optional[asyncio.Task] = None
_cancel_requested = False


def _data_dir(db: Any) -> Optional[Path]:
    base = getattr(db, "db_path", None)
    return Path(base) if base is not None else None


# A configured scan folder whose files can't be seen -- an unplugged drive, a
# renamed folder, a dead share, a folder the server may not list, or the
# empty mount point of a drive that is not mounted -- hides them; it does not
# mean they were deleted. A full clean with one never completes, and the
# stale cleanup leaves its rows.
SCAN_FOLDERS_MISSING = "scan_folders_missing"
_REFUSED_LOG = "Full clean refused, nothing deleted: scan folder(s) unavailable: %s"

# _probe_folder states. EMPTY alone is not unavailable (_unavailable_folders).
PRESENT, EMPTY, MISSING, UNREADABLE = "present", "empty", "missing", "unreadable"


def _probe_folder(filepath: str) -> str:
    """What a configured scan folder looks like right now: MISSING (not
    there), UNREADABLE (its stat or its listing fails: Python's rglob finds
    nothing, silently, in a folder it may not list), EMPTY (no entries at
    all) or PRESENT."""
    try:
        if not Path(filepath).exists():
            return MISSING
        with os.scandir(filepath) as entries:
            return PRESENT if next(entries, None) is not None else EMPTY
    except FileNotFoundError:
        return MISSING
    except OSError:
        return UNREADABLE


def _path_key(path: str) -> str:
    """``path`` normalised the way the OS compares paths (separators, case
    on Windows) and ending in one separator, so a path lies under a folder
    exactly when its key starts with the folder's ("lib" never matches
    "library")."""
    return os.path.normcase(os.path.normpath(path)).rstrip(os.sep) + os.sep


def _unavailable_folders(
    directories: Sequence[DirectoryConfig],
    expected_paths: Callable[[], Iterable[str]],
) -> Dict[str, str]:
    """The configured scan folders whose files can't be seen now, each once
    in config order, with why: MISSING, UNREADABLE or EMPTY. An empty folder
    counts only when one of ``expected_paths()`` (paths the library has, or
    had, a row or a rating for; called only when a folder is empty) lies
    under it: then it is the mount point of a drive that is not mounted. A
    folder nothing ever came from (one just added) does not count. A mount
    point *below* a configured folder is not seen."""
    folders = list(dict.fromkeys(d.filepath for d in directories))
    states = {f: _probe_folder(f) for f in folders}
    empty = {f: _path_key(f) for f in folders if states[f] == EMPTY}
    backed: Set[str] = set()
    if empty:
        for p in expected_paths():
            key = _path_key(to_native_path(p))
            backed.update(f for f, prefix in empty.items() if key.startswith(prefix))
            if len(backed) == len(empty):
                break
    return {
        f: states[f]
        for f in folders
        if states[f] in (MISSING, UNREADABLE) or f in backed
    }


def _full_clean_blockers(
    db: Any, directories: Sequence[DirectoryConfig]
) -> Dict[str, str]:
    """The unavailable folders a full clean must not start with: an empty
    one counts when the library has rows under it, or the pending file of an
    earlier, unfinished full clean holds ratings under it."""
    data_dir = _data_dir(db)

    def expected() -> Iterable[str]:
        paths: Set[str] = set(db.get_existing_file_paths())
        if data_dir is not None:
            paths |= pending_snapshot_paths(data_dir)
        return paths

    return _unavailable_folders(directories, expected)


_WHY_SHORT = {
    MISSING: "missing",
    UNREADABLE: "can't be read",
    EMPTY: "empty: is its drive mounted?",
}


def _blockers_text(blockers: Dict[str, str]) -> str:
    return ", ".join(f"{f} ({why})" for f, why in blockers.items())


def _refusal_message(blockers: Dict[str, str]) -> str:
    """Reconnect first; removing a folder from the scan list is the last
    resort, and the message says what it costs."""
    tail = (
        " Remove {what} from the scan list (Configuration → Directories) only "
        "if it is gone for good: its images' ratings are then dropped from the "
        "library. Nothing was deleted."
    )
    if len(blockers) > 1:
        return (
            f"Full clean refused: {len(blockers)} scan folders can't be scanned: "
            f"{_blockers_text(blockers)}. Reconnect or mount their drives and try "
            "again." + tail.format(what="a folder")
        )
    [(folder, why)] = blockers.items()
    if why == EMPTY:
        head = (
            f"Full clean refused: the scan folder {folder} is empty, but the "
            "library has images from it. Is its drive mounted? Mount it and try "
            "again."
        )
    elif why == UNREADABLE:
        head = (
            f"Full clean refused: the scan folder {folder} can't be read, so its "
            "images could not be re-imported. Check its drive and the folder's "
            "permissions, then try again."
        )
    else:
        head = (
            f"Full clean refused: the scan folder {folder} is missing, so its "
            "images could not be re-imported. Reconnect its drive and try again."
        )
    return head + tail.format(what="the folder")


def _stale_paths_to_clean(
    existing_db_paths: Iterable[str], directories: Sequence[DirectoryConfig]
) -> Tuple[List[Path], Dict[str, int], Dict[str, str]]:
    """The rows whose files are gone, split into the ones to delete and, for
    each configured scan folder whose files can't be seen
    (:func:`_unavailable_folders`; an empty one counts, its rows lie under
    it), how many of its rows are left alone; plus why each unavailable
    folder is. A row under no configured folder (one removed from the
    config) is deleted as before. The folders are checked after the per-file
    pass, so a drive lost during it counts, and what would be deleted is
    stat-ed again last: a drive away during the pass but back by the folder
    check has its files back."""
    stale = [p for p in existing_db_paths if not Path(p).exists()]
    if not stale:
        return [], {}, {}
    unavailable = _unavailable_folders(directories, lambda: stale)
    prefixes = [(f, _path_key(f)) for f in unavailable]
    candidates: List[str] = []
    left_alone: Dict[str, int] = {}
    for p in stale:
        key = _path_key(p)
        folder = next((f for f, prefix in prefixes if key.startswith(prefix)), None)
        if folder is None:
            candidates.append(p)
        else:
            left_alone[folder] = left_alone.get(folder, 0) + 1
    to_delete = [Path(p) for p in candidates if not Path(p).exists()]
    return to_delete, left_alone, unavailable


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
    configured scan folder's files can't be seen (:func:`_full_clean_blockers`);
    an ordinary scan skips that folder."""
    global _scan_task, _cancel_requested

    if _scan_task and not _scan_task.done():
        raise HTTPException(status_code=409, detail="Scan already in progress")

    if body.full_clean:
        # Off the event loop: it stats and lists folders (a dead share can
        # hang) and reads the library's paths.
        blockers = await asyncio.to_thread(
            _full_clean_blockers, get_db(), get_directories(load_app_config())
        )
        if blockers:
            logger.warning(_REFUSED_LOG, _blockers_text(blockers))
            raise HTTPException(
                status_code=409,
                detail={
                    "code": SCAN_FOLDERS_MISSING,
                    "folders": list(blockers),
                    "message": _refusal_message(blockers),
                },
            )
        # Another request may have started a scan while this one waited.
        if _scan_task and not _scan_task.done():
            raise HTTPException(status_code=409, detail="Scan already in progress")

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

    # Configured folders whose files could not be seen at some check of this
    # run (missing, unreadable, or empty with rows under them), in config
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
        blockers = await asyncio.to_thread(_full_clean_blockers, db, directories)
        if blockers:
            logger.warning(_REFUSED_LOG, _blockers_text(blockers))
            await ws_manager.broadcast(
                "scan",
                "error",
                {
                    "code": SCAN_FOLDERS_MISSING,
                    "folders": list(blockers),
                    "message": _refusal_message(blockers),
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

    def _expected_paths() -> Iterable[str]:
        """What should be under the configured folders, for telling an empty
        mount point from a folder nothing ever came from: the paths a full
        clean's snapshot holds ratings for (the rows are gone), else the
        library's rows."""
        if taste_snapshot is not None:
            return snapshot_paths(taste_snapshot)
        return db.get_existing_file_paths()

    async def _unavailable(dirs: Sequence[DirectoryConfig]) -> Dict[str, str]:
        return await asyncio.to_thread(_unavailable_folders, dirs, _expected_paths)

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
            unavailable = await _unavailable([dir_config])
            if unavailable:
                logger.warning(
                    "Scan folder %s is %s; skipped",
                    dir_config.filepath,
                    unavailable[dir_config.filepath],
                )
                _note_missing(unavailable)
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

            try:
                processed = await asyncio.to_thread(
                    scanner.scan_directory,
                    str(dir_path),
                    dir_config.search_subfolders,
                    progress_callback,
                    False,  # not full_scan - skip existing
                )
            except (ValueError, OSError):
                # The scanner raises ValueError("Directory does not exist")
                # for a folder gone since the check above, and an OSError for
                # one lost mid-walk: skip it like any unavailable folder and
                # scan the rest. A failure in a folder that is there still
                # fails the run.
                unavailable = await _unavailable([dir_config])
                if not unavailable:
                    raise
                logger.warning(
                    "Scan folder %s is %s (lost during its scan); skipped",
                    dir_config.filepath,
                    unavailable[dir_config.filepath],
                )
                _note_missing(unavailable)
                continue
            total_processed += processed

        # Stale cleanup
        stale_count = 0
        if full_cleanup:
            await ws_manager.broadcast(
                "scan", "phase_changed", {"phase": "stale_cleanup"}
            )
            existing_db_paths = await asyncio.to_thread(db.get_existing_file_paths)
            # A row under a configured folder whose files can't be seen now
            # is left alone: its file is out of sight, not deleted.
            stale_paths, left_alone, why = await asyncio.to_thread(
                _stale_paths_to_clean, existing_db_paths, directories
            )
            for folder, count in left_alone.items():
                logger.warning(
                    "Stale cleanup: scan folder %s is %s; left %d row(s) under it "
                    "in the library",
                    folder,
                    why[folder],
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
            _note_missing(await _unavailable(directories))
            if missing_folders:
                logger.warning(
                    "Full clean incomplete: scan folder(s) %s unavailable; the "
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
