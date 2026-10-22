"""Scan management endpoints."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import similarity as similarity_api
from backend.config import load_app_config, get_directories
from backend.dependencies import get_db, get_thumbnail_cache
from backend.ws.manager import ws_manager
from metascan.core.scanner import Scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])

# Track the running scan task so it can be cancelled
_scan_task: Optional[asyncio.Task] = None
_cancel_requested = False


TASTE_SNAPSHOT_FILENAME = "taste_snapshot_before_full_clean.json"


def _snapshot_file_path(db: Any) -> Optional[Path]:
    base = getattr(db, "db_path", None)
    return Path(base) / TASTE_SNAPSHOT_FILENAME if base is not None else None


def _write_snapshot_file(db: Any, snapshot: Dict[str, Any]) -> None:
    """Persist the pre-truncate taste snapshot next to the DB (atomic
    replace). Kept after the scan as a manual recovery copy; the next full
    clean overwrites it."""
    target = _snapshot_file_path(db)
    if target is None:
        return
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    os.replace(tmp, target)


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


async def _run_scan(full_cleanup: bool, full_clean: bool = False) -> None:  # noqa: C901
    """Run the scan in a background task with WebSocket progress updates."""
    db = get_db()
    thumbnail_cache = get_thumbnail_cache()
    config = load_app_config()
    directories = get_directories(config)

    scanner = Scanner(db, thumbnail_cache)

    await ws_manager.broadcast("scan", "started", {"full_clean": full_clean})

    # Snapshot the taste state (judgments, category links, favorites)
    # before any destructive operation: truncating media cascades away
    # media_judgments and media_categories. The snapshot is also written
    # to disk so a crash mid-rescan doesn't lose it; if either step fails
    # the full clean is aborted before anything is deleted.
    taste_snapshot: Optional[Dict[str, Any]] = None
    if full_clean:
        try:
            taste_snapshot = await asyncio.to_thread(db.export_taste_state)
            await asyncio.to_thread(_write_snapshot_file, db, taste_snapshot)
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

    async def _restore_taste_from_snapshot() -> None:
        """Put the snapshot back for paths that re-appeared and tombstone
        the rest. Runs at most once per scan (tombstones are not
        idempotent); safe to call when no snapshot was taken."""
        nonlocal restore_done
        if restore_done or taste_snapshot is None:
            return
        restore_done = True
        try:
            counts = await asyncio.to_thread(
                db.restore_taste_state, taste_snapshot, True
            )
            logger.info(
                "Full clean: taste state restored for %d paths, %d tombstoned",
                counts["restored"],
                counts["tombstoned"],
            )
            await ws_manager.broadcast("scan", "taste_restored", counts)
        except Exception as restore_err:
            logger.error(
                f"Failed to restore taste state after scan (snapshot kept at "
                f"{_snapshot_file_path(db)}): {restore_err}",
                exc_info=True,
            )

    try:
        total_processed = 0
        total_dirs = len(directories)

        for dir_idx, dir_config in enumerate(directories):
            if _cancel_requested:
                await _restore_taste_from_snapshot()
                await ws_manager.broadcast("scan", "cancelled", {})
                return

            dir_path = Path(dir_config.filepath)
            if not dir_path.exists():
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
            stale_paths = [Path(p) for p in existing_db_paths if not Path(p).exists()]
            if stale_paths:
                stale_count = await asyncio.to_thread(
                    db.delete_media_batch, stale_paths
                )

        # Restore ratings for files that re-appeared in the rescan
        await _restore_taste_from_snapshot()

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
        await _restore_taste_from_snapshot()
        logger.error(f"Scan error: {e}", exc_info=True)
        await ws_manager.broadcast("scan", "error", {"message": str(e)})
