"""Scan management endpoints."""

import asyncio
import logging
import os
import stat
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import similarity as similarity_api
from backend.config import load_app_config, get_directories
from backend.dependencies import get_db, get_thumbnail_cache
from backend.ws.manager import ws_manager
from backend.services.taste_snapshot import TASTE_SNAPSHOT_FILENAME  # noqa: F401
from backend.services.taste_snapshot import (
    PendingSnapshotUnreadable,
    apply_pending_taste_snapshot,
    clear_full_clean_marker,
    full_clean_marker_exists,
    full_clean_running,
    merge_pending_into_snapshot,
    restore_and_persist,
    snapshot_file_path,
    write_full_clean_marker,
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
    """Begin a scan. Progress is reported via WebSocket."""
    global _scan_task, _cancel_requested

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


async def _run_scan(full_cleanup: bool, full_clean: bool = False) -> None:
    """Run the scan in a background task with WebSocket progress updates.

    The T2I / I2V listings must not prune lineage rows while the rescan of
    a full clean re-adds the media rows one by one
    (``taste_snapshot.lineage_prune_guard``). A full clean is marked as
    running (``full_clean_running``) from before its snapshot to after its
    restore, however it ends. It writes the lasting marker first, and only
    a scan that walks every configured folder to the end (and re-ingests
    the generated output still on disk) -- this one, or any later one --
    removes it."""
    with full_clean_running() if full_clean else nullcontext():
        await _scan(full_cleanup, full_clean)


async def _mark_full_clean_unfinished(data_dir: Optional[Path]) -> bool:
    """Write the full-clean marker (``write_full_clean_marker``, fsynced),
    the first thing a full clean does. False -- the full clean must stop
    before it writes or deletes anything -- when it cannot be written."""
    try:
        if data_dir is None:
            raise RuntimeError("database has no data directory")
        await asyncio.to_thread(write_full_clean_marker, data_dir)
    except Exception as err:
        logger.error(
            f"Full clean aborted: could not write the full-clean marker: {err}",
            exc_info=True,
        )
        await ws_manager.broadcast(
            "scan",
            "error",
            {
                "message": (
                    "Full clean aborted: could not record that it started, so "
                    f"nothing was deleted ({err})"
                )
            },
        )
        return False
    return True


def _full_clean_unfinished(data_dir: Path, full_clean: bool) -> bool:
    """This run is a full clean, or the marker of an earlier one exists (a
    marker that can't be checked counts as existing: fail safe)."""
    if full_clean:
        return True
    try:
        return full_clean_marker_exists(data_dir)
    except OSError:
        return True


def _generated_file_state(path: str) -> str:
    """``"present"`` (a regular file); ``"gone"`` (its folder is there, the
    file is not: deleted); or ``"unreachable"`` (its folder is missing or
    can't be read -- an unmounted ``t2i.output_root`` / ``i2v.output_root``,
    a permission error -- so it may well still exist; or something other
    than a file sits at the path)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        try:
            folder = os.stat(os.path.dirname(path))
        except OSError:
            return "unreachable"
        return "gone" if stat.S_ISDIR(folder.st_mode) else "unreachable"
    except OSError:
        return "unreachable"
    return "present" if stat.S_ISREG(st.st_mode) else "unreachable"


def _ingest_generated_file(scanner: Any, path: str) -> str:
    """Re-ingest one generated file: ``"reingested"``, ``"gone"``,
    ``"unreachable"`` (``_generated_file_state``) or ``"failed"`` (on disk,
    but the ingest raised or could not read it as media)."""
    state = _generated_file_state(path)
    if state != "present":
        return state
    try:
        media = scanner.ingest_file(Path(path))
    except Exception as e:
        # The type only: counts, never paths, leave the re-ingest.
        logger.debug("Re-ingesting a generated file failed (%s)", type(e).__name__)
        return "failed"
    return "failed" if media is None else "reingested"


REINGEST_OUTCOMES = ("reingested", "gone", "unreachable", "failed")


def _reingest_generated_files(
    db: Any,
    scanner: Any,
    data_dir: Path,
    full_clean: bool,
    cancelled: Callable[[], bool],
) -> Dict[str, int]:
    """Re-ingest (``Scanner.ingest_file``) the T2I images and I2V clips whose
    media row is missing but whose file is still on disk -- only while a full
    clean is unfinished (``_full_clean_unfinished``). Outside one a dangling
    row means a deleted file, and nothing is done. Returns the count of each
    ``REINGEST_OUTCOMES`` outcome; stops at a cancel. Raises what the
    candidate query raises. Runs in a worker thread."""
    counts = dict.fromkeys(REINGEST_OUTCOMES, 0)
    if not _full_clean_unfinished(data_dir, full_clean):
        return counts
    for path in db.generated_paths_without_media():
        if cancelled():
            break
        counts[_ingest_generated_file(scanner, path)] += 1
    return counts


async def _scan(full_cleanup: bool, full_clean: bool) -> None:  # noqa: C901
    """The scan itself; see ``_run_scan``."""
    db = get_db()
    thumbnail_cache = get_thumbnail_cache()
    config = load_app_config()
    directories = get_directories(config)

    scanner = Scanner(db, thumbnail_cache)

    await ws_manager.broadcast("scan", "started", {"full_clean": full_clean})

    # Snapshot the taste state (judgments, category links, favorites,
    # hindsight briefs, ✓ library prompts) before any destructive
    # operation: truncating media cascades away media_judgments,
    # media_categories and hindsight_briefs. The snapshot is written to
    # disk as *pending state* (backend/services/taste_snapshot.py) before
    # the truncate; if either step fails the full clean is aborted before
    # anything is deleted. Leftovers of an earlier interrupted full clean
    # are first applied, and whatever is still pending is merged in so it
    # isn't overwritten.
    data_dir = _data_dir(db)
    taste_snapshot: Optional[Dict[str, Any]] = None
    if full_clean:
        # The lasting marker comes first, before the pending file is applied
        # or written: until a scan of every configured folder completes, the
        # T2I / I2V lists must not prune a lineage row the rescan may still
        # bring back -- even once nothing is pending any more. A marker that
        # can't be written stops the clean with nothing written at all (a
        # snapshot left behind could later put back what the user changed).
        if not await _mark_full_clean_unfinished(data_dir):
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
        run the rest is tombstoned and the pending file deleted; on cancel
        or error the rest stays pending (file rewritten, no tombstones).
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

    async def _reingest_generated_output() -> bool:
        """T2I / I2V output enters the library through ComfyClient's ingest,
        under the ComfyUI output root, which is usually outside every scan
        folder: a full clean's folder walk never brings it back. While a
        full clean is unfinished (this one, or the marker of an earlier
        one), re-ingest the generated files whose media row is missing but
        that are still on disk, so the restore puts their ratings back and
        their lineage stays attached.

        Returns whether every generated file still on disk came back: False
        when the step itself failed, a file failed to ingest, or a file's
        folder could not be reached (an unmounted output root) -- the run is
        then unfinished: no tombstones, and the marker stays. Logs counts
        and exception types only. Never raises."""
        if data_dir is None:
            return True
        try:
            counts = await asyncio.to_thread(
                _reingest_generated_files,
                db,
                scanner,
                data_dir,
                full_clean,
                lambda: _cancel_requested,
            )
        except Exception as e:
            logger.error(
                "Re-ingesting generated output failed (%s); the full clean "
                "stays unfinished",
                type(e).__name__,
            )
            return False
        if any(counts.values()):
            logger.info(
                "Generated output outside the scan folders: re-ingested %d "
                "images / clips; %d gone from disk, %d in a folder that can't "
                "be reached, %d failed to ingest",
                counts["reingested"],
                counts["gone"],
                counts["unreachable"],
                counts["failed"],
            )
        held_back = counts["unreachable"] + counts["failed"]
        if held_back:
            logger.warning(
                "%d generated images / clips could not be brought back; the "
                "full clean stays unfinished (their ratings stay pending and "
                "the T2I / I2V lists keep their rows) until a later scan brings "
                "them back",
                held_back,
            )
        return not held_back

    async def _every_folder_scanned() -> None:
        """A scan walked every configured folder to the end (and re-ingested
        the generated output still on disk): every file still on disk in a
        configured folder, and every T2I / I2V output still on disk, has its
        media row again, so the T2I / I2V lists may prune again. The only
        place the full-clean marker is removed; a failure keeps it (and the
        pause) and is logged. Never raises."""
        if data_dir is None:
            return
        try:
            await asyncio.to_thread(clear_full_clean_marker, data_dir)
        except Exception as e:
            logger.error(f"Removing the full-clean marker failed: {e}", exc_info=True)

    # Configured folders this run could not see (and so did not walk), and
    # how many it walked.
    missing_folders: List[str] = []
    walked_folders = 0

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
                missing_folders.append(dir_config.filepath)
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
            walked_folders += 1

        # Every generated file still on disk must come back for the run to
        # finish; never after a cancel (one in the last folder included).
        generated_back = False
        if not _cancel_requested:
            generated_back = await _reingest_generated_output()

        # Stale cleanup
        stale_count = 0
        if full_cleanup:
            await ws_manager.broadcast(
                "scan", "phase_changed", {"phase": "stale_cleanup"}
            )
            existing_db_paths = await asyncio.to_thread(db.get_existing_file_paths)
            stale_paths = [Path(p) for p in existing_db_paths if not Path(p).exists()]
            if stale_paths:
                stale_count = await asyncio.to_thread(
                    db.delete_media_batch, stale_paths
                )

        # Restore ratings for files that re-appeared in the rescan. A cancel
        # that landed during the last directory still leaves the loop
        # normally -- it must not tombstone what wasn't re-imported yet, and
        # neither may a run whose generated output did not all come back.
        finished = not _cancel_requested and generated_back
        await _restore_taste_from_snapshot(completed=finished)
        await _apply_pending()
        # Not when unfinished, not when a configured folder was missing (its
        # files were not walked), and not with no folder to walk at all.
        if finished and not missing_folders and walked_folders:
            await _every_folder_scanned()

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
            {"processed": total_processed, "stale_removed": stale_count},
        )

    except Exception as e:
        # Best-effort restore so users don't silently lose ratings on a
        # failed full-clean rescan.
        await _restore_taste_from_snapshot(completed=False)
        await _apply_pending()
        logger.error(f"Scan error: {e}", exc_info=True)
        await ws_manager.broadcast("scan", "error", {"message": str(e)})
