"""Scan management endpoints."""

import asyncio
import logging
import os
import stat
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api import similarity as similarity_api
from backend.config import (
    DirectoryConfig,
    get_comfy_config,
    get_directories,
    get_i2v_config,
    get_t2i_config,
    load_app_config,
)
from backend.dependencies import get_db, get_thumbnail_cache
from backend.ws.manager import ws_manager
from backend.services.taste_snapshot import TASTE_SNAPSHOT_FILENAME  # noqa: F401
from backend.services.taste_snapshot import (
    PendingSnapshotUnreadable,
    apply_pending_taste_snapshot,
    clear_full_clean_marker,
    full_clean_marker_exists,
    full_clean_running,
    generated_output_place,
    merge_pending_into_snapshot,
    note_generated_output,
    pending_snapshot_paths,
    restore_and_persist,
    snapshot_file_path,
    snapshot_paths,
    write_full_clean_marker,
    write_snapshot_file,
)
from metascan.core.i2v_runner import i2v_output_root
from metascan.core.scanner import Scanner
from metascan.core.t2i_runner import t2i_output_root
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


async def _run_scan(full_cleanup: bool, full_clean: bool = False) -> None:
    """Run the scan in a background task with WebSocket progress updates.

    The T2I / I2V listings must not prune lineage rows while the rescan of
    a full clean re-adds the media rows one by one
    (``taste_snapshot.lineage_prune_guard``). A full clean is marked as
    running (``full_clean_running``) from before its snapshot to after its
    restore, however it ends. Refused with nothing written while a
    configured folder is unavailable (``_full_clean_blockers``, checked
    again here), it then writes the lasting marker first, and only a scan
    that walks every configured folder to the end with none unavailable
    (and re-ingests the generated output still on disk) -- this one, or any
    later one -- removes it."""
    with full_clean_running() if full_clean else nullcontext():
        await _scan(full_cleanup, full_clean)


async def _mark_full_clean_unfinished(data_dir: Optional[Path]) -> bool:
    """Write the full-clean marker (``write_full_clean_marker``, fsynced),
    the first thing a full clean writes (its folder re-check writes
    nothing). False -- the full clean must stop before it writes or deletes
    anything -- when it cannot be written."""
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


def _generated_output_roots(config: Dict[str, Any]) -> List[str]:
    """The folders generated output is written under, resolved as the
    runners resolve them, each once: T2I's (``t2i_output_root``: the
    configured ``t2i.output_root``, else ``<comfy.output_root>/t2i``) and
    I2V's (``i2v_output_root``: ``i2v.output_root``, else
    ``<comfy.output_root>/i2v``). The dated folders and I2V's folder per
    source image sit below them."""
    comfy_root = get_comfy_config(config)["output_root"]
    roots = (
        t2i_output_root(get_t2i_config(config)["output_root"], comfy_root),
        i2v_output_root(get_i2v_config(config)["output_root"], comfy_root),
    )
    return list(dict.fromkeys(str(root) for root in roots))


def _absolute_key(path: str) -> str:
    """``_path_key`` of the absolute path: a relative output root (the
    default ``comfy.output_root`` is ``data/storyboards``) and the files
    below it resolve against the working directory, as the runners' do."""
    return _path_key(os.path.abspath(path))


def _shown_path(path: str) -> str:
    """``path`` as the scan shows it -- in its log, the marker and the
    complete event: ``os.path.abspath``, the step ``_absolute_key`` starts
    with. A relative path (the default I2V output root,
    ``data/storyboards/i2v``, and the clips below it) is joined to the
    working directory, and any path is normalised the way ``abspath`` does
    it (``..``, ``.`` and doubled separators collapsed), but neither
    case-folded nor given the trailing separator ``_path_key`` adds. For
    showing only: paths are matched by their keys."""
    return os.path.abspath(path)


def _output_root_of(path: str, roots: Sequence[str]) -> Optional[str]:
    """The deepest of ``roots`` that ``path`` lies under, or None."""
    key = _absolute_key(path)
    under = [root for root in roots if key.startswith(_absolute_key(root))]
    return max(under, key=lambda root: len(_absolute_key(root)), default=None)


def _generated_file_state(path: str) -> str:
    """What sits at a generated file's path: ``"present"`` (a regular file),
    ``"gone"`` (nothing: the path, or a folder on it, does not exist) or
    ``"unreadable"`` (its stat failed otherwise -- a permission or I/O error
    -- or something other than a file sits there). Whether a file that is
    not there was deleted or is on a drive that is not mounted is for its
    output root to say (``_reingest_generated_files``)."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "gone"
    except OSError:
        return "unreadable"
    return "present" if stat.S_ISREG(st.st_mode) else "unreadable"


def _ingest_generated_file(scanner: Any, path: str) -> str:
    """Re-ingest one generated file if it is there: ``"reingested"`` or
    ``"failed"`` (the ingest raised or could not read it as media); else
    its ``_generated_file_state``, ``"gone"`` or ``"unreadable"``."""
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

# The most failed generated files a scan's complete event lists
# (unreadable_output, sorted); unreadable_output_count counts every one.
UNREADABLE_OUTPUT_LIMIT = 50


@dataclass
class ReingestReport:
    """What the re-ingest of generated output found
    (``_reingest_generated_files``): the count of each ``REINGEST_OUTCOMES``
    outcome; ``unreachable``, the output roots that held files back, each
    with why (``MISSING`` / ``UNREADABLE``), in config order; ``failed``,
    the paths (native, as listed) of the files on disk that did not come
    back -- unreadable, or their ingest raised or returned None -- in the
    order met; ``failed_roots``, the roots that hold those, in config order,
    then None for one under no configured root; ``check_failed``, set when
    the step itself raised with no cancel: nothing about the output is
    known; and ``stopped``, set when it did not run to its end (a cancel,
    or an error): none of its findings is reported, only its counts so far
    for the log."""

    counts: Dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(REINGEST_OUTCOMES, 0)
    )
    unreachable: Dict[str, str] = field(default_factory=dict)
    failed: List[str] = field(default_factory=list)
    failed_roots: List[Optional[str]] = field(default_factory=list)
    check_failed: bool = False
    stopped: bool = False

    @property
    def back(self) -> bool:
        """Every generated file still on disk came back."""
        return not (
            self.stopped
            or self.check_failed
            or self.counts["unreachable"]
            or self.counts["failed"]
        )

    def add(self, path: str, outcome: str) -> None:
        self.counts[outcome] += 1
        if outcome == "failed":
            self.failed.append(path)


def _reingest_generated_files(
    db: Any,
    scanner: Any,
    data_dir: Path,
    full_clean: bool,
    cancelled: Callable[[], bool],
    config: Dict[str, Any],
) -> ReingestReport:
    """Re-ingest (``Scanner.ingest_file``) the T2I images and I2V clips whose
    media row is missing but whose file is still on disk -- only while a full
    clean is unfinished (``_full_clean_unfinished``). Outside one a dangling
    row means a deleted file, and nothing is done.

    A file that is not there is ``"gone"`` (deleted: a completed full clean
    tombstones it) unless it lies under a configured output root
    (``_generated_output_roots``, the deepest that holds it) that is missing
    or can't be read (``_probe_folder``: a drive not mounted, a permission
    error). Then it is ``"unreachable"`` -- it may well still exist -- and
    the root is reported. A folder missing *below* a root that is there
    (I2V's folder per source image, a dated folder) hides nothing, and a
    file under no configured root (one moved in config) has no unmounted
    root to hide on. The roots are checked after the pass over the files,
    so a drive lost during it counts, and a file found gone is stat-ed again
    last: one whose drive came back by the root check is re-ingested. A
    file that can't be read (``"unreadable"``) is ``"failed"`` unless its
    root is the one that can't be reached: it may well exist. A failed file
    stays failed however many scans fail on it: it is on disk, so only the
    owner can decide it is gone (by deleting or moving it).

    Returns the ``ReingestReport``: the failed files are reported with the
    roots that hold them. A cancel stops it, between files, with nothing
    reported (``stopped``, only the counts so far); it is checked again
    after the pass and before the report is returned, so one that lands
    during the last file's ingest or re-check reports nothing either.
    Raises what the candidate query or the config raises. Runs in a worker
    thread."""
    report = ReingestReport()
    if not _full_clean_unfinished(data_dir, full_clean):
        return report
    not_there: List[Tuple[str, str]] = []
    for path in db.generated_paths_without_media():
        if cancelled():
            return _stopped(report)
        outcome = _ingest_generated_file(scanner, path)
        if outcome in ("gone", "unreadable"):
            not_there.append((path, outcome))
        else:
            report.add(path, outcome)
    if cancelled():
        return _stopped(report)
    if not (not_there or report.failed):
        return report
    roots = _generated_output_roots(config)
    if not _settle_by_output_root(scanner, not_there, roots, report, cancelled):
        return _stopped(report)
    if cancelled():
        return _stopped(report)
    report.failed_roots = _roots_holding(report.failed, roots)
    return report


def _stopped(report: ReingestReport) -> ReingestReport:
    """What a re-ingest that a cancel stopped reports: its counts so far
    (for the log) and nothing else -- its findings are partial."""
    return ReingestReport(counts=report.counts, stopped=True)


def _settle_by_output_root(
    scanner: Any,
    not_there: Sequence[Tuple[str, str]],
    roots: Sequence[str],
    report: ReingestReport,
    cancelled: Callable[[], bool],
) -> bool:
    """Count into ``report`` each ``(path, file state)`` the pass did not
    find by its output root (``_reingest_generated_files``):
    ``"unreachable"`` under a root that is missing or can't be read (each
    root probed once, now: after the pass), else a gone file is stat-ed
    again -- re-ingested if it is back -- and an unreadable one is
    ``"failed"``. Records the roots that held files back, with why, in
    ``roots`` order. False when a cancel stopped it."""
    states: Dict[str, str] = {}
    for path, state in not_there:
        if cancelled():
            return False
        root = _output_root_of(path, roots)
        if root is not None and root not in states:
            states[root] = _probe_folder(root)
        if root is not None and states[root] in (MISSING, UNREADABLE):
            report.add(path, "unreachable")
            continue
        if state == "gone":
            state = _ingest_generated_file(scanner, path)
        report.add(path, "failed" if state == "unreadable" else state)
    report.unreachable = {
        r: states[r] for r in roots if states.get(r) in (MISSING, UNREADABLE)
    }
    return True


def _roots_holding(paths: Iterable[str], roots: Sequence[str]) -> List[Optional[str]]:
    """The roots that hold one of ``paths`` -- for each path the deepest
    that holds it (``_output_root_of``) -- in ``roots`` order, then None
    when one lies under none of them."""
    held: Set[Optional[str]] = {_output_root_of(path, roots) for path in paths}
    return [root for root in roots if root in held] + ([None] if None in held else [])


def _generated_output_fields(report: ReingestReport) -> Dict[str, Any]:
    """The ``complete`` event's account of the generated output, every path
    absolute (``_shown_path``) and native: ``unreachable_output``, the output
    roots that held files back; ``unreadable_output``, the failed files,
    sorted, at most ``UNREADABLE_OUTPUT_LIMIT``, and
    ``unreadable_output_count``, all of them; ``output_check_failed``, the
    step itself raised with no cancel. ``[]``, ``0`` and ``False`` with
    nothing to report. Two spellings of one root (one relative) show once."""
    failed = sorted(_shown_path(path) for path in report.failed)
    return {
        "unreachable_output": list(
            dict.fromkeys(_shown_path(root) for root in report.unreachable)
        ),
        "unreadable_output": failed[:UNREADABLE_OUTPUT_LIMIT],
        "unreadable_output_count": len(failed),
        "output_check_failed": report.check_failed,
    }


async def _scan(full_cleanup: bool, full_clean: bool) -> None:  # noqa: C901
    """The scan itself; see ``_run_scan``."""
    db = get_db()
    thumbnail_cache = get_thumbnail_cache()
    config = load_app_config()
    directories = get_directories(config)

    scanner = Scanner(db, thumbnail_cache)

    await ws_manager.broadcast("scan", "started", {"full_clean": full_clean})

    # Configured folders whose files could not be seen at some check of this
    # run (missing, unreadable, or empty with rows under them), in config
    # order, and how many it walked. A full clean that saw one never
    # completes (no tombstones); a scan that saw one, or walked none, never
    # removes the full-clean marker.
    missing_folders: List[str] = []
    walked_folders = 0

    def _note_missing(folders: Iterable[str]) -> None:
        for folder in folders:
            if folder not in missing_folders:
                missing_folders.append(folder)

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
        # The lasting marker is the first thing written, before the pending
        # file is applied or written: until a scan of every configured folder
        # completes, the T2I / I2V lists must not prune a lineage row the
        # rescan may still bring back -- even once nothing is pending any
        # more. A marker that can't be written stops the clean with nothing
        # written at all (a snapshot left behind could later put back what
        # the user changed).
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
        run the rest is tombstoned and the pending file deleted; on cancel,
        error, an unavailable scan folder or generated output that did not
        all come back the rest stays pending (file rewritten, no
        tombstones).
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

    async def _reingest_generated_output() -> ReingestReport:
        """T2I / I2V output enters the library through ComfyClient's ingest,
        under the ComfyUI output root, which is usually outside every scan
        folder: a full clean's folder walk never brings it back. While a
        full clean is unfinished (this one, or the marker of an earlier
        one), re-ingest the generated files whose media row is missing but
        that are still on disk, so the restore puts their ratings back and
        their lineage stays attached.

        Returns what it found (``_reingest_generated_files``; on a failure
        of the step itself, ``check_failed`` and nothing else -- nothing at
        all when a cancel was requested: a cancel reports nothing). Its
        ``back`` is False when the step itself failed or stopped, a file
        could not be read or ingested, or an output root could not be
        reached (a drive not mounted) -- the run is then unfinished: no
        tombstones, and the marker stays, saying why
        (``note_generated_output``: the roots, the count of failed files
        and the roots that hold them, or the type of the error). Logs
        counts, exception types and output roots, never a file's path.
        Never raises."""
        if data_dir is None:
            return ReingestReport()
        try:
            report = await asyncio.to_thread(
                _reingest_generated_files,
                db,
                scanner,
                data_dir,
                full_clean,
                lambda: _cancel_requested,
                config,
            )
        except Exception as e:
            logger.error(
                "Re-ingesting generated output failed (%s); the full clean "
                "stays unfinished",
                type(e).__name__,
            )
            if _cancel_requested:
                # A cancel reports nothing, the step's own failure included.
                return ReingestReport(stopped=True)
            await asyncio.to_thread(
                note_generated_output,
                data_dir,
                {},
                check_failed=type(e).__name__,
            )
            return ReingestReport(check_failed=True, stopped=True)
        counts = report.counts
        if any(counts.values()):
            logger.info(
                "Generated output outside the scan folders: re-ingested %d "
                "images / clips; %d gone from disk, %d under an output folder "
                "that can't be reached, %d could not be read or ingested",
                counts["reingested"],
                counts["gone"],
                counts["unreachable"],
                counts["failed"],
            )
        unreachable = {
            _shown_path(folder): why for folder, why in report.unreachable.items()
        }
        if unreachable:
            logger.warning(
                "Generated output folder(s) %s can't be reached, so %d "
                "generated images / clips in them could not be checked: the "
                "full clean stays unfinished (their ratings stay pending and "
                "the T2I / I2V lists keep their rows) until a scan finds the "
                "folder again. If a folder is gone for good, recreate it "
                "(empty is fine) and run Full clean & rescan: the images "
                "missing from it are then recorded as deleted, with their "
                "history",
                _blockers_text(unreachable),
                counts["unreachable"],
            )
        # The output roots that hold the failed files (None: under none),
        # each once however it is spelled in the config.
        failed_roots = list(
            dict.fromkeys(
                None if root is None else _shown_path(root)
                for root in report.failed_roots
            )
        )
        if report.failed:
            logger.warning(
                "%d generated images / clips on disk (%s) could not be read or "
                "re-imported; the full clean stays unfinished (their ratings "
                "stay pending and the T2I / I2V lists keep their rows) until a "
                "later scan brings them back. The scan's Done view lists the "
                "files: delete each one or move it somewhere else (or repair "
                "it), then run Full clean & rescan",
                len(report.failed),
                ", ".join(generated_output_place(root) for root in failed_roots),
            )
        if not _cancel_requested:
            # The marker says what this scan could not bring back (or no
            # longer says what an earlier one could not).
            await asyncio.to_thread(
                note_generated_output,
                data_dir,
                {folder: _WHY_SHORT[why] for folder, why in unreachable.items()},
                len(report.failed),
                failed_roots,
            )
        return report

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
            walked_folders += 1

        # Every generated file still on disk must come back for the run to
        # finish; never after a cancel (one in the last folder included).
        # What did not come back is named in the complete event: an output
        # root that can't be reached, a file that can't be read or ingested.
        generated = ReingestReport()
        generated_back = False
        if not _cancel_requested:
            generated = await _reingest_generated_output()
            generated_back = generated.back

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
        # neither may a run whose generated output did not all come back, or
        # one that could not see one of its folders.
        completed = not _cancel_requested and generated_back and not missing_folders
        await _restore_taste_from_snapshot(completed=completed)
        # A completed scan that walked its folders (none unavailable) has
        # every file still on disk back: the T2I / I2V lists may prune again.
        # Not when unfinished, not with a folder unavailable (its files were
        # not walked), and not with no folder to walk at all.
        if completed and walked_folders:
            await _every_folder_scanned()
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
                **_generated_output_fields(generated),
            },
        )

    except Exception as e:
        # Best-effort restore so users don't silently lose ratings on a
        # failed full-clean rescan.
        await _restore_taste_from_snapshot(completed=False)
        await _apply_pending()
        logger.error(f"Scan error: {e}", exc_info=True)
        await ws_manager.broadcast("scan", "error", {"message": str(e)})
