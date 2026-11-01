"""The server side of the Configuration window's Import / Export tab (the
bundle itself: ``metascan/core/library_transfer.py``).

An upload is staged: one bundle, kept on disk beside its parsed data until it
is applied or replaced, so the owner can correct the folder mapping and see the
match counts without sending the file again. Applying is a background task (it
can ingest thousands of files); the tab polls ``status()``.

Neither direction runs while a scan runs or a Full clean is unfinished: its
ratings are in the pending snapshot, not the tables a bundle reads, and an
import would race the rescan. ``scan.move_gate`` serialises the check and the
write with a scan starting, as for a move.
"""

import asyncio
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from backend.api import scan
from backend.api.scan import _generated_output_roots
from backend.config import get_directories, load_app_config
from backend.dependencies import get_thumbnail_cache
from backend.services import taste_snapshot
from backend.ws.manager import ws_manager
from metascan.core.library_transfer import (
    MediaResolver,
    PathMap,
    bundle_members,
    export_library_data,
    extract_thumbnails,
    generated_paths,
    import_library_data,
    media_paths,
    read_bundle,
    suggest_targets,
    write_bundle,
)
from metascan.core.scanner import Scanner
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

TRANSFER_DIRNAME = "library_transfer"

# How long an import waits for a move or hygiene batch in flight.
GATE_WAIT_S = 30.0
GATE_POLL_S = 0.05


class TransferRefused(Exception):
    """Not now: ``code`` for the client, ``message`` for the owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Staged:
    token: str
    file: Path
    manifest: Dict[str, Any]
    data: Dict[str, Any]


_staged: Optional[Staged] = None
_lock = threading.Lock()
_run: Dict[str, Any] = {"running": False}


def transfer_dir(data_dir: Path) -> Path:
    path = Path(data_dir) / TRANSFER_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_leftovers(data_dir: Path) -> None:
    """Startup: nothing staged survives a restart, and a killed export or
    upload leaves its file. Never raises."""
    path = Path(data_dir) / TRANSFER_DIRNAME
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        logger.warning("Could not clear the library transfer folder")


def _require_data_dir(db: Any) -> Path:
    data_dir = scan._data_dir(db)
    if data_dir is None:
        raise TransferRefused("no_data_dir", "The database has no data folder.")
    return data_dir


def _refusal(data_dir: Path) -> Optional[TransferRefused]:
    """Why the library cannot be read or written now. Anything that cannot
    be checked counts as busy."""
    if scan.scan_running() or taste_snapshot.is_full_clean_running():
        return TransferRefused(
            "scan_running", "A scan is running. Try again when it has finished."
        )
    try:
        unfinished = scan._full_clean_unfinished(
            data_dir, False
        ) or taste_snapshot.pending_snapshot_exists(data_dir)
    except OSError:
        unfinished = True
    if unfinished:
        return TransferRefused(
            "full_clean_unfinished",
            "A Full clean & rescan has not finished, so some ratings are not "
            "in the library yet. Finish it first (Maintenance tab).",
        )
    return None


def library_roots(config: Mapping[str, Any]) -> List[str]:
    """The folders a bundle's paths are relative to, POSIX, as stored: the
    scan folders and the generated-output roots. A relative output root (the
    default I2V one) is listed both ways, since its rows may be stored either
    way."""
    roots: List[str] = []
    configured = [d.filepath for d in get_directories(dict(config))]
    configured.extend(_generated_output_roots(dict(config)))
    for root in configured:
        for spelling in (root, os.path.abspath(root)):
            posix = to_posix_path(spelling).rstrip("/")
            if posix and posix not in roots:
                roots.append(posix)
    return roots


def build_export(db: Any) -> Path:
    """Thread. The bundle as a new file in the transfer folder; the caller
    deletes it once it has been sent."""
    data_dir = _require_data_dir(db)
    refusal = _refusal(data_dir)
    if refusal is not None:
        raise refusal
    roots = library_roots(load_app_config())
    target = transfer_dir(data_dir) / f"export-{uuid.uuid4().hex}.zip"
    data = export_library_data(db)
    try:
        manifest = write_bundle(target, data, roots, data_dir)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    logger.info("Library data exported: %s", manifest["counts"])
    return target


def new_upload_path(db: Any) -> Path:
    return transfer_dir(_require_data_dir(db)) / f"upload-{uuid.uuid4().hex}.zip"


def stage_upload(db: Any, upload: Path) -> Dict[str, Any]:
    """Thread. Parse an uploaded bundle (BundleError when it is not one, and
    the file is removed) and make it THE staged bundle, replacing any other."""
    global _staged
    try:
        manifest, data = read_bundle(upload)
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    staged = Staged(uuid.uuid4().hex, upload, manifest, data)
    with _lock:
        if _run["running"]:
            upload.unlink(missing_ok=True)
            raise TransferRefused("import_running", "An import is running.")
        previous, _staged = _staged, staged
    if previous is not None:
        previous.file.unlink(missing_ok=True)
    mapping = suggested_mapping(db, staged)
    return preview(db, staged.token, mapping)


def _take_staged(token: str) -> Staged:
    staged = _staged
    if staged is None or staged.token != token:
        raise TransferRefused(
            "not_staged", "That file is no longer uploaded. Choose it again."
        )
    return staged


def discard(token: str) -> None:
    global _staged
    with _lock:
        if _run["running"]:
            raise TransferRefused("import_running", "An import is running.")
        staged = _take_staged(token)
        _staged = None
    staged.file.unlink(missing_ok=True)


def _library(db: Any) -> MediaResolver:
    return MediaResolver(db.all_media_paths())


def suggested_mapping(db: Any, staged: Staged) -> Dict[str, Optional[str]]:
    config = load_app_config()
    return suggest_targets(
        staged.manifest["roots"],
        media_paths(staged.data),
        library_roots(config),
        _library(db),
    )


def _clean_mapping(
    staged: Staged, mapping: Mapping[str, Optional[str]]
) -> Dict[str, Optional[str]]:
    """Every root of the bundle, each with its target as stored here, or None
    when it was left blank. A root the bundle does not list is ignored."""
    out: Dict[str, Optional[str]] = {}
    for root in staged.manifest["roots"]:
        target = (mapping.get(root) or "").strip()
        out[root] = to_posix_path(target).rstrip("/") if target else None
    return out


def _file_on_disk(path: str) -> bool:
    return scan._generated_file_state(to_native_path(path)) == "present"


def _generated_to_ingest(
    staged: Staged, paths: PathMap, library: MediaResolver
) -> List[str]:
    """Translated T2I / I2V paths with no media row whose file is here."""
    missing = (paths.translate(path) for path in sorted(generated_paths(staged.data)))
    return [p for p in missing if library.resolve(p) is None and _file_on_disk(p)]


def preview(
    db: Any, token: str, mapping: Mapping[str, Optional[str]]
) -> Dict[str, Any]:
    """Thread. What applying with ``mapping`` would find, root by root."""
    staged = _take_staged(token)
    clean = _clean_mapping(staged, mapping)
    paths = PathMap(clean)
    library = _library(db)
    per_root: Dict[str, List[int]] = {root: [0, 0] for root in clean}
    outside = [0, 0]
    for path in media_paths(staged.data):
        root = paths.root_of(path)
        cell = per_root[root] if root is not None else outside
        cell[0] += 1
        cell[1] += int(library.resolve(paths.translate(path)) is not None)
    config = load_app_config()
    return {
        "token": token,
        "created_at": staged.manifest.get("created_at"),
        "counts": staged.manifest.get("counts", {}),
        "roots": [
            {
                "source": root,
                "target": target,
                "paths": per_root[root][0],
                "found": per_root[root][1],
            }
            for root, target in clean.items()
            if per_root[root][0]
        ],
        "outside": {"paths": outside[0], "found": outside[1]},
        "generated_to_ingest": len(_generated_to_ingest(staged, paths, library)),
        "targets": library_roots(config),
    }


def status() -> Dict[str, Any]:
    return dict(_run)


def _set_run(**fields: Any) -> None:
    global _run
    _run = {**_run, **fields}


def start_import(
    db: Any, token: str, mapping: Mapping[str, Optional[str]]
) -> "asyncio.Task[None]":
    """Loop. Refuse now if it cannot run, else start the import task."""
    data_dir = _require_data_dir(db)
    staged = _take_staged(token)
    refusal = _refusal(data_dir)
    if refusal is not None:
        raise refusal
    clean = _clean_mapping(staged, mapping)
    with _lock:
        if _run["running"]:
            raise TransferRefused("import_running", "An import is running.")
        _set_run(
            running=True, phase="preparing", done=0, total=0, report=None, error=None
        )
    return asyncio.create_task(_import(db, data_dir, staged, clean))


def _ingest_generated(db: Any, paths: Sequence[str]) -> Dict[str, int]:
    """Thread. Bring in generated output no import would find (usually
    outside every scan folder), so its ratings and lineage have a row."""
    scanner = Scanner(db, get_thumbnail_cache())
    counts = {"ingested": 0, "failed": 0}
    for index, path in enumerate(paths):
        outcome = scan._ingest_generated_file(scanner, to_native_path(path))
        counts["ingested" if outcome == "reingested" else "failed"] += 1
        _set_run(done=index + 1)
    return counts


def _apply_under_gate(
    db: Any, data_dir: Path, staged: Staged, paths: PathMap, members: Set[str]
) -> Dict[str, Dict[str, int]]:
    """Thread. The check and the transaction, as one critical section with
    a scan marking itself running. The wait is bounded, so it never strands
    the lock."""
    deadline = time.monotonic() + GATE_WAIT_S
    while not scan.move_gate.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise TransferRefused("library_busy", "The library is busy, try again.")
        time.sleep(GATE_POLL_S)
    try:
        refusal = _refusal(data_dir)
        if refusal is not None:
            raise refusal
        return import_library_data(db, staged.data, paths, members)
    finally:
        scan.move_gate.release()


def _announce() -> None:
    """Every view of what changed reloads; the taste model is due at once."""
    for channel, event, payload in (
        ("library", "media_added", {"count": 0}),
        ("library", "data_imported", {}),
        ("categories", "categories_changed", {}),
        ("t2i", "t2i_images_changed", {}),
        ("i2v", "i2v_videos_changed", {"source_path": "", "files": []}),
        ("training", "adapters_changed", {}),
    ):
        try:
            ws_manager.broadcast_sync(channel, event, payload)
        except Exception:
            logger.exception("library transfer broadcast failed")
    from backend.api.taste import get_taste_service

    service = get_taste_service()
    if service is not None:
        service.mark_due("library import")


async def _import(
    db: Any, data_dir: Path, staged: Staged, mapping: Dict[str, Optional[str]]
) -> None:
    global _staged
    paths = PathMap(mapping)
    try:
        library = await asyncio.to_thread(_library, db)
        to_ingest = await asyncio.to_thread(
            _generated_to_ingest, staged, paths, library
        )
        _set_run(phase="ingesting", done=0, total=len(to_ingest))
        ingest = await asyncio.to_thread(_ingest_generated, db, to_ingest)
        _set_run(phase="applying", done=0, total=0)
        await asyncio.to_thread(extract_thumbnails, staged.file, staged.data, data_dir)
        members = await asyncio.to_thread(bundle_members, staged.file)
        counts = await asyncio.to_thread(
            _apply_under_gate, db, data_dir, staged, paths, members
        )
    except TransferRefused as refused:
        _set_run(running=False, phase=None, error=refused.message)
        return
    except Exception as e:
        # The type only: its text can quote a prompt or a path.
        logger.error("Library import failed (%s)", type(e).__name__, exc_info=False)
        _set_run(
            running=False,
            phase=None,
            error=f"The import failed and changed nothing ({type(e).__name__}).",
        )
        return
    logger.info(
        "Library data imported: %s",
        {kind: count["added"] for kind, count in counts.items()},
    )
    with _lock:
        if _staged is staged:
            _staged = None
    staged.file.unlink(missing_ok=True)
    _set_run(running=False, phase=None, report={"counts": counts, **ingest})
    _announce()
