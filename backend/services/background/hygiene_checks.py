"""The library hygiene checks (spec 10) and the mass-removal guard.

Six checks, run in this order by ``HygieneProcess``: ``MissingFiles``,
``NewFiles``, ``ChangedFiles``, ``MissingDerived``, ``UpscaleOrphans``,
``Finish``. The first
deletes rows automatically, so it is the careful one:

* it reuses the scan route's ``_stale_paths_to_clean`` (a folder whose files
  can't be seen hides them; the folders are checked after the per-file pass
  and what would go is stat-ed again) and deletes through
  ``delete_media_batch``, so tombstones keep the ratings;
* a row under a generated-output root that is missing or unreadable is left
  alone (the file may be on an unmounted drive), as is a row whose file
  vanished less than ``MOVE_GRACE_S`` ago (it may be a move Import is about to
  see);
* it cleans nothing while a Full clean is unfinished (the marker or the
  pending snapshot exists), and each delete batch of at most
  ``DELETE_BATCH`` rows is serialised with a scan start through
  ``scan.move_gate``;
* it never removes more than a quarter of a folder's rows when that is more
  than ``MASS_REMOVAL_MIN_ROWS``: that folder waits for the owner's Play (the
  pass ends ``failed`` naming it), the others clean as normal.

``Finish`` runs on an interrupted pass too, so the snapshot code always
hears whether the pass was cut short (nothing is tombstoned then, and the
marker stays). Activity text carries counts and bare names, except the
blocked folder, which the owner must see."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.services.background.hygiene import (
    CheckResult,
    HygieneCheck,
    HygieneContext,
)
from metascan.core.folder_poller import WATCH_INTERVAL_S, FolderPoller, FolderSpec
from metascan.core.move_detector import (
    MOVE_GRACE_S,
    Candidate,
    match_moves,
    mtimes_equal,
)
from metascan.utils.fs_compat import path_key
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

MASS_REMOVAL_FRACTION = 0.25  # more than this share of a folder's rows ...
MASS_REMOVAL_MIN_ROWS = 100  # ... and more than this many rows
CONFIRM_MARGIN = 0.05
CONFIRM_MARGIN_ROWS = 10
NO_FOLDER_LABEL = "folders no longer in the scan list"
DELETE_BATCH = 200  # the longest the move gate is held
STAT_CHUNK = 500
PAGE = 500
NEW_FILE_SETTLE_S = 30.0  # a file written this recently is Import's to settle


def _scan() -> Any:
    from backend.api import scan

    return scan


def _bare(path: str) -> str:
    return Path(path).name or path


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _unfinished(scan: Any, data_dir: Optional[Path]) -> bool:
    """A Full clean is unfinished or running: hygiene may add rows but cleans
    nothing. Anything that cannot be checked counts as unfinished."""
    from backend.services import taste_snapshot

    if data_dir is None or scan.scan_running():
        return True
    try:
        return bool(
            scan._full_clean_unfinished(data_dir, False)
            or taste_snapshot.is_full_clean_running()
            or taste_snapshot.pending_snapshot_exists(data_dir)
        )
    except OSError:
        return True


def _group_of(path: str, folders: Sequence[Tuple[str, str]]) -> str:
    """The configured folder that holds ``path`` (the deepest), or the label
    of the rows under none of them."""
    key = _scan()._path_key(path)
    best = ""
    label = NO_FOLDER_LABEL
    for folder, prefix in folders:
        if key.startswith(prefix) and len(prefix) > len(best):
            best, label = prefix, folder
    return label


def _confirmed_covers(confirmed: Any, label: str, count: int) -> bool:
    """Play confirmed ``label`` when it showed ``confirmed[label]`` rows: a
    count that has grown by more than 5% (at least 10 rows) is a new
    question."""
    shown = confirmed.get(label) if hasattr(confirmed, "get") else None
    if shown is None:
        return False
    return bool(count <= shown + max(CONFIRM_MARGIN_ROWS, CONFIRM_MARGIN * shown))


def _is_blocked(count: int, total: int) -> bool:
    return count > MASS_REMOVAL_MIN_ROWS and count > MASS_REMOVAL_FRACTION * total


def _key(path: str) -> str:
    return path_key(os.path.normpath(os.path.abspath(path)))


async def _survey(ctx: HygieneContext, stored: Sequence[str]) -> Dict[str, Any]:
    """Walk the configured folders once per pass (the poller's own walk):
    the files on disk the library has no row for, how many folders were
    walked and which could not be seen. Shared by the move matcher and
    NewFiles."""
    cached = ctx.shared.get("survey")
    if cached is not None:
        return dict(cached)
    scan = _scan()
    from backend.config import get_directories
    from metascan.core.scanner import Scanner

    directories = get_directories(ctx.config)
    specs = [FolderSpec(d.filepath, d.search_subfolders) for d in directories]
    supported = Scanner.SUPPORTED_EXTENSIONS
    poller = FolderPoller(
        specs,
        scan._probe_folder,
        lambda p: Path(p).suffix.lower() in supported,
    )
    known = {_key(to_native_path(p)) for p in stored}
    unavailable = await asyncio.to_thread(
        scan._unavailable_folders,
        directories,
        lambda: [to_native_path(p) for p in stored],
    )
    missing: List[str] = []
    walked = 0
    unknown: List[str] = []
    for done, spec in enumerate(specs):
        if ctx.stopped():
            break
        ctx.report("Looking for new files", done, len(specs))
        if spec.filepath in unavailable:
            missing.append(spec.filepath)
            continue
        found = await asyncio.to_thread(poller.walk_folder, spec)
        if found is None:
            missing.append(spec.filepath)
            continue
        walked += 1
        unknown.extend(p for p in found if _key(p) not in known)
    result = {"unknown": unknown, "walked": walked, "missing": missing}
    if not ctx.shared.get("interrupted"):
        ctx.shared["survey"] = result
        ctx.shared["walked_folders"] = ctx.shared.get("walked_folders", 0) + walked
        folders = ctx.shared.setdefault("missing_folders", [])
        folders.extend(f for f in missing if f not in folders)
    return result


class MissingFiles:
    name = "missing files"

    def __init__(self) -> None:
        # path -> pass start when it was first found missing without Import
        # having seen it vanish (it is cleaned a poll interval later)
        self._unseen: Dict[str, float] = {}

    async def run(self, ctx: HygieneContext) -> CheckResult:
        scan = _scan()
        from backend.config import get_directories

        db, data_dir = ctx.db, ctx.data_dir
        started = ctx.clock()
        unfinished = await asyncio.to_thread(_unfinished, scan, data_dir)
        if unfinished:
            ctx.shared["cleaning_skipped"] = True
            return CheckResult()
        directories = get_directories(ctx.config)
        stored = await asyncio.to_thread(db.all_media_paths)
        native = [to_native_path(p) for p in stored]

        gone: List[str] = []
        for done, chunk in enumerate(_chunks(native, STAT_CHUNK)):
            if ctx.stopped():
                return CheckResult()
            ctx.report("Checking for missing files", done * STAT_CHUNK, len(native))
            gone.extend(await asyncio.to_thread(self._gone, chunk))
        busy = await asyncio.to_thread(db.upscale_busy_paths)
        gone = [p for p in gone if to_posix_path(p) not in busy]
        if not gone or ctx.stopped():
            return CheckResult()

        # A moved or renamed folder is not a deletion: re-key what moved
        # (whatever Import is doing; it only keeps the ratings) before any
        # row is deleted.
        gone, moved = await self._rescue_moves(ctx, stored, gone)
        if ctx.shared.get("interrupted"):
            return CheckResult()
        if not gone:
            return CheckResult()
        # The owner paused or stopped Import (maybe to reorganise files): no row
        # is deleted meanwhile. Moves were rescued above. With the watch off,
        # cleaning still runs.
        imp = ctx.import_process
        halted = getattr(imp, "owner_halted", None)
        if halted is not None and halted():
            return CheckResult()
        moved_posix = {to_posix_path(o) for o in moved}
        native = [p for p in native if to_posix_path(p) not in moved_posix]
        eligible = set(str(Path(p)) for p in self._past_grace(ctx, gone, started))

        # The folders are checked after the pass over the files, what would be
        # deleted is stat-ed again, and nothing under an unavailable folder goes.
        to_delete, left_alone, unavailable = await asyncio.to_thread(
            scan._stale_paths_to_clean, gone, directories
        )
        missing = ctx.shared.setdefault("missing_folders", [])
        for folder in left_alone:
            if folder not in missing:
                missing.append(folder)
            logger.warning(
                "Hygiene: scan folder %s is %s; left %d row(s) under it in the library",
                folder,
                unavailable.get(folder),
                left_alone[folder],
            )
        paths = await asyncio.to_thread(self._outside_lost_roots, ctx, to_delete)

        # The guard counts every missing row, in grace or not: a mass vanish
        # must not be cleaned in slices. Only the rows past grace are deleted.
        allowed = [
            p
            for p in self._apply_guard(ctx, directories, native, paths)
            if str(Path(p)) in eligible
        ]
        cleaned = 0
        folders = [
            (f, scan._path_key(f))
            for f in dict.fromkeys(d.filepath for d in directories)
        ]
        for batch in _chunks(allowed, DELETE_BATCH):
            if ctx.stopped():
                break
            ctx.report("Removing missing files", cleaned, len(allowed))
            n = await asyncio.to_thread(self._delete_batch, ctx, list(batch))
            if n is None:
                ctx.shared["interrupted"] = True  # a scan started: not ours now
                break
            if n:
                ctx.shared.setdefault("deleted_in", set()).update(
                    _group_of(p, folders) for p in batch
                )
            cleaned += n
        if cleaned:
            self._announce(cleaned)
        return CheckResult(cleaned=cleaned)

    async def _rescue_moves(
        self, ctx: HygieneContext, stored: Sequence[str], gone: List[str]
    ) -> Tuple[List[str], List[str]]:
        """Match the missing rows against the files on disk the library does
        not have (spec 11.1; ambiguity is "not a move") and re-key the matches
        through Import's own function. Returns the rows still missing and the
        old (stored) paths that moved."""
        imp = ctx.import_process
        if imp is None or not hasattr(imp, "apply_moves"):
            return gone, []
        from backend.config import get_directories

        directories = get_directories(ctx.config)
        # A row under a folder or output root that cannot be seen is not a
        # missing file (its drive may be away): never a move source either.
        seen = await self._visible(ctx, directories, gone)
        candidates = [g for g in gone if str(Path(g)) in seen]
        if not candidates:
            return gone, []
        survey = await _survey(ctx, stored)
        if not survey["unknown"] or ctx.stopped():
            return gone, []
        pairs = await asyncio.to_thread(
            self._match, ctx.db, candidates, survey["unknown"]
        )
        if not pairs:
            return gone, []
        # Look again just before applying: a drive lost during the match counts.
        seen = await self._visible(
            ctx, directories, [to_native_path(o) for o, _ in pairs]
        )
        pairs = [(o, n) for o, n in pairs if str(Path(to_native_path(o))) in seen]
        if not pairs:
            return gone, []
        applied = await imp.apply_moves(pairs)
        if applied is None:  # a scan runs: it owns the library now
            ctx.shared["interrupted"] = True
            return [], []
        moved_old = {o for o, _ in applied}
        ctx.shared.setdefault("moved_new", set()).update(
            _key(to_native_path(n)) for _, n in applied
        )
        return [g for g in gone if to_posix_path(g) not in moved_old], list(moved_old)

    async def _visible(
        self, ctx: HygieneContext, directories: Sequence[Any], rows: Sequence[str]
    ) -> set:
        """``rows`` (as ``str(Path)``) that are really gone: not under an
        unavailable scan folder (checked after, files stat-ed again) and not
        under a missing or unreadable generated-output root."""
        scan = _scan()
        to_delete, _left, _unav = await asyncio.to_thread(
            scan._stale_paths_to_clean, list(rows), directories
        )
        kept = await asyncio.to_thread(self._outside_lost_roots, ctx, to_delete)
        return {str(Path(p)) for p in kept}

    @staticmethod
    def _match(
        db: Any, gone: Sequence[str], unknown: Sequence[str]
    ) -> List[Tuple[str, str]]:
        """Thread. ``(old, new)`` stored-path pairs: a missing row and the
        unknown file that is the same file."""
        from metascan.core.phash_utils import compute_phash_for_file

        old: List[Candidate] = []
        for g in gone:
            cand = db.candidate_for(to_posix_path(g))
            if cand is not None:
                old.append(cand)
        if not old:
            return []
        sizes = {c.size for c in old}
        new: List[Candidate] = []
        for p in unknown:
            try:
                st = os.stat(p)
            except OSError:
                continue
            phash = None
            if st.st_size in sizes:  # only a file some missing row could be
                try:
                    phash = compute_phash_for_file(Path(p))
                except Exception:
                    phash = None
            new.append(Candidate(to_posix_path(p), st.st_size, st.st_mtime, phash))
        return match_moves(new, old)

    @staticmethod
    def _gone(chunk: Sequence[str]) -> List[str]:
        """Thread. Those of ``chunk`` whose file is not there. A file whose
        stat fails otherwise (permissions, I/O) may well exist: not gone."""
        scan = _scan()
        return [p for p in chunk if scan._generated_file_state(p) == "gone"]

    def _past_grace(
        self, ctx: HygieneContext, gone: List[str], started: float
    ) -> List[str]:
        """Rows to consider: Import saw the file vanish ``MOVE_GRACE_S`` ago, or
        saw nothing and a watch interval has passed since a pass first found
        it missing (the next pass; the watcher has had its polls)."""
        imp = ctx.import_process
        now = ctx.clock()
        ok: List[str] = []
        current = set(gone)
        for path in list(self._unseen):
            if path not in current:
                del self._unseen[path]
        for path in gone:
            since = imp.vanished_since(path) if imp is not None else None
            if since is not None:
                if now - since >= MOVE_GRACE_S:
                    ok.append(path)
                continue
            first = self._unseen.setdefault(path, started)
            if started - first >= WATCH_INTERVAL_S:
                ok.append(path)
        return ok

    @staticmethod
    def _outside_lost_roots(ctx: HygieneContext, paths: Sequence[Path]) -> List[str]:
        """Thread. ``paths`` minus those under a generated-output root that is
        missing or unreadable (its drive may be unmounted), probed now, after
        the pass over the files."""
        scan = _scan()
        roots = scan._generated_output_roots(ctx.config)
        states: Dict[str, str] = {}
        keep: List[str] = []
        for path in paths:
            p = str(path)
            root = scan._output_root_of(p, roots)
            if root is not None:
                if root not in states:
                    states[root] = scan._probe_folder(root)
                if states[root] in (scan.MISSING, scan.UNREADABLE):
                    ctx.shared["output_unreachable"] = True
                    continue
            keep.append(p)
        return keep

    @staticmethod
    def _apply_guard(
        ctx: HygieneContext,
        directories: Sequence[Any],
        all_native: Sequence[str],
        paths: Sequence[str],
    ) -> List[str]:
        """The rows to delete after the mass-removal guard. A group (a
        configured folder, or the rows under none) that would lose more than a
        quarter of its rows, and more than 100, loses nothing and is recorded
        in ``shared["blocked"]`` -- unless the owner confirmed it with Play."""
        scan = _scan()
        folders = [
            (f, scan._path_key(f))
            for f in dict.fromkeys(d.filepath for d in directories)
        ]
        order = [f for f, _ in folders] + [NO_FOLDER_LABEL]
        totals: Dict[str, int] = {}
        for p in all_native:
            g = _group_of(p, folders)
            totals[g] = totals.get(g, 0) + 1
        grouped: Dict[str, List[str]] = {}
        for p in paths:
            grouped.setdefault(_group_of(p, folders), []).append(p)
        blocked: List[Tuple[str, int]] = []
        allowed: List[str] = []
        for label in order:
            rows = grouped.get(label)
            if not rows:
                continue
            if not _confirmed_covers(ctx.confirmed, label, len(rows)) and _is_blocked(
                len(rows), totals.get(label, len(rows))
            ):
                blocked.append((label, len(rows)))
                logger.warning(
                    "Hygiene: %d of %d rows under one folder are missing; "
                    "nothing removed there until the owner confirms",
                    len(rows),
                    totals.get(label, len(rows)),
                )
                continue
            allowed.extend(rows)
        if blocked:
            ctx.shared.setdefault("blocked", []).extend(blocked)
        return allowed

    @staticmethod
    def _delete_batch(ctx: HygieneContext, batch: List[str]) -> Optional[int]:
        """Thread. One batch, at most ``DELETE_BATCH`` rows, under the move
        gate: the check that no scan is running and the delete are one
        critical section with a scan marking itself running. None when the
        gate is taken or a scan runs (the rest waits for the next pass)."""
        scan = _scan()
        gate: threading.Lock = scan.move_gate
        if not gate.acquire(blocking=False):
            return None
        try:
            if _unfinished(scan, ctx.data_dir):
                return None
            return int(ctx.db.delete_media_batch([Path(p) for p in batch]))
        finally:
            gate.release()

    @staticmethod
    def _announce(count: int) -> None:
        try:
            from backend.ws.manager import ws_manager

            ws_manager.broadcast_sync("library", "media_removed", {"count": count})
        except Exception:
            logger.exception("library broadcast failed")


class NewFiles:
    name = "new files"

    async def run(self, ctx: HygieneContext) -> CheckResult:
        stored = await asyncio.to_thread(ctx.db.all_media_paths)
        survey = await _survey(ctx, stored)
        if ctx.shared.get("interrupted"):
            return CheckResult()
        moved = ctx.shared.get("moved_new", set())
        unknown = [p for p in survey["unknown"] if _key(p) not in moved]

        added = 0
        imp = ctx.import_process
        if unknown:
            # A file Import gave up on is a failed ingest, as in a scan; any
            # other unknown file keeps a Full clean's marker until it is in.
            waiting = [
                p for p in unknown if not (imp is not None and imp.gave_up_on(p))
            ]
            if waiting:
                ctx.shared["new_files_pending"] = True
            watching = bool(ctx.config.get("watch_directories", True))
            if (
                imp is not None
                and watching
                and imp.accepts_unknown()
                and not ctx.stopped()
            ):
                ready = await asyncio.to_thread(self._settled, imp, unknown)
                added = await imp.enqueue_unknown(ready)
                if added:
                    ctx.report(f"Handed {added} new files to Import", added, added)
        await self._generated(ctx)
        return CheckResult(changed=added)

    @staticmethod
    def _settled(imp: Any, paths: Sequence[str]) -> List[str]:
        """Thread. Files not being written now and not already Import's."""
        now = time.time()
        out: List[str] = []
        for p in paths:
            if imp.is_settling(p):
                continue
            try:
                if now - os.stat(p).st_mtime < NEW_FILE_SETTLE_S:
                    continue
            except OSError:
                continue
            out.append(p)
        return out

    async def _generated(self, ctx: HygieneContext) -> None:
        """The generated-output pass, the scan route's own. Outside an
        unfinished Full clean it finds nothing to do."""
        scan = _scan()
        if ctx.data_dir is None or ctx.stopped():
            return
        scanner = _scanner(ctx)
        try:
            report = await asyncio.to_thread(
                scan._reingest_generated_files,
                ctx.db,
                scanner,
                ctx.data_dir,
                False,
                ctx.should_stop,
                ctx.config,
            )
        except Exception as exc:
            logger.error(
                "Hygiene: re-ingesting generated output failed (%s)", type(exc).__name__
            )
            ctx.shared["generated_back"] = False
            return
        ctx.shared["generated_back"] = report.back
        if report.stopped:
            ctx.shared["interrupted"] = True


def _scanner(ctx: HygieneContext) -> Any:
    if ctx.scanner is None:
        from backend.dependencies import get_thumbnail_cache
        from metascan.core.scanner import Scanner

        ctx.scanner = Scanner(ctx.db, ctx.thumbnails or get_thumbnail_cache())
    return ctx.scanner


class ChangedFiles:
    name = "changed files"

    async def run(self, ctx: HygieneContext) -> CheckResult:
        db = ctx.db
        imp = ctx.import_process
        after = ""
        changed = 0
        while True:
            if ctx.stopped():
                return CheckResult(changed=changed)
            # A short read per page: the DB lock is free between pages.
            page = await asyncio.to_thread(db.media_stat_page, after, PAGE)
            if not page:
                break
            after = page[-1][0]
            ctx.report("Checking for changed files", changed, 0)
            differing = await asyncio.to_thread(self._differing, page)
            for path in differing:
                if ctx.stopped():
                    return CheckResult(changed=changed)
                if imp is not None and imp.is_settling(to_native_path(path)):
                    continue
                if await asyncio.to_thread(self._reingest, ctx, path):
                    changed += 1
        return CheckResult(changed=changed)

    @staticmethod
    def _differing(
        page: Sequence[Tuple[str, Optional[int], Optional[float]]],
    ) -> List[str]:
        """Thread. Rows whose file size or modification time (within 2 s)
        differs from the file's. A row with no stored size or time, or a file
        that cannot be stat-ed, is left alone."""
        out: List[str] = []
        for path, size, mtime in page:
            if size is None or mtime is None:
                continue
            try:
                st = os.stat(to_native_path(path))
            except OSError:
                continue
            if st.st_size != size or not mtimes_equal(st.st_mtime, mtime):
                out.append(path)
        return out

    @staticmethod
    def _reingest(ctx: HygieneContext, path: str) -> bool:
        """Thread. Re-read the file (the path is the same, so ratings stay)
        and make Embedding do it again."""
        try:
            if path in ctx.db.upscale_busy_paths():
                return False  # a swap is mid-way: file and row disagree for now
            media = _scanner(ctx).ingest_file(Path(to_native_path(path)))
        except Exception as exc:
            logger.warning(
                "Hygiene: a changed file failed to re-ingest: %s", type(exc).__name__
            )
            return False
        if media is None:
            return False
        ctx.db.mark_unembedded([path])
        return True


def _stamp(path: str) -> Optional[Tuple[int, float]]:
    try:
        st = os.stat(to_native_path(path))
    except OSError:
        return None
    return (st.st_size, st.st_mtime)


class MissingDerived:
    name = "missing derived data"

    def __init__(self) -> None:
        # (kind, path) -> file stamp when the derived data could not be made:
        # not tried again until the file changes (for the server's lifetime)
        self._failed: Dict[Tuple[str, str], Optional[Tuple[int, float]]] = {}

    def _skip(self, kind: str, path: str) -> bool:
        key = (kind, path)
        return key in self._failed and self._failed[key] == _stamp(path)

    async def run(self, ctx: HygieneContext) -> CheckResult:
        fixed = await self._phashes(ctx)
        if not ctx.stopped():
            fixed += await self._thumbnails(ctx)
        if not ctx.stopped():
            await self._embedding(ctx)
        if not ctx.stopped():
            await self._tagging(ctx)
        return CheckResult(changed=fixed)

    async def _phashes(self, ctx: HygieneContext) -> int:
        from metascan.core.phash_utils import compute_phash_for_file

        db = ctx.db
        after = ""
        fixed = 0
        while not ctx.stopped():
            page = await asyncio.to_thread(db.media_missing_derived, after, PAGE)
            if not page:
                break
            after = page[-1]
            for path in page:
                if ctx.stopped():
                    return fixed
                if self._skip("phash", path):
                    continue
                ctx.report(f"Hashing {_bare(path)}", fixed, 0)
                n = await asyncio.to_thread(
                    self._phash_one, db, compute_phash_for_file, path
                )
                if n:
                    self._failed.pop(("phash", path), None)
                else:
                    self._failed[("phash", path)] = _stamp(path)
                fixed += n
        return fixed

    @staticmethod
    def _phash_one(db: Any, compute: Any, path: str) -> int:
        native = Path(to_native_path(path))
        if not native.is_file():
            return 0
        try:
            phash = compute(native)
        except Exception:
            return 0
        if not phash:
            return 0
        db.set_phash(path, phash)
        return 1

    async def _thumbnails(self, ctx: HygieneContext) -> int:
        cache = ctx.thumbnails
        if cache is None:
            from backend.dependencies import get_thumbnail_cache

            cache = get_thumbnail_cache()
        stored = await asyncio.to_thread(ctx.db.all_media_paths)
        fixed = 0
        for chunk in _chunks(stored, STAT_CHUNK):
            if ctx.stopped():
                return fixed
            lacking = await asyncio.to_thread(self._without_thumbnail, cache, chunk)
            for path in lacking:
                if ctx.stopped():
                    return fixed
                if self._skip("thumb", path):
                    continue
                ctx.report(f"Thumbnail for {_bare(path)}", fixed, 0)
                made = await asyncio.to_thread(
                    cache.get_or_create_thumbnail, Path(to_native_path(path))
                )
                if made:
                    self._failed.pop(("thumb", path), None)
                    fixed += 1
                else:
                    self._failed[("thumb", path)] = _stamp(path)
        return fixed

    @staticmethod
    def _without_thumbnail(cache: Any, chunk: Sequence[str]) -> List[str]:
        out: List[str] = []
        for path in chunk:
            try:
                thumb = cache.get_thumbnail_path(Path(to_native_path(path)))
            except OSError:
                continue
            if thumb is not None and not thumb.exists():  # None: the file is gone
                out.append(path)
        return out

    async def _embedding(self, ctx: HygieneContext) -> None:
        unembedded = await asyncio.to_thread(ctx.db.get_unembedded_file_paths)
        if not unembedded:
            return
        process = ctx.embedding
        if process is None:
            from backend.dependencies import get_embedding_process

            process = get_embedding_process()
        if process is None:
            return
        ctx.report(f"Asking Embedding for {len(unembedded):,} files", 0, 0)
        try:
            await process.kick(True)
        except Exception as exc:
            logger.warning("Hygiene: embedding kick failed: %s", type(exc).__name__)

    async def _tagging(self, ctx: HygieneContext) -> None:
        if not await asyncio.to_thread(_vlm_tier):
            return
        paths = await asyncio.to_thread(
            lambda: ctx.db.images_without_vlm_tags(for_hygiene=True)
        )
        if not paths:
            return
        added = await asyncio.to_thread(ctx.db.enqueue_tags, paths)
        process = ctx.tagging
        if process is None:
            from backend.dependencies import get_tagging_process

            process = get_tagging_process()
        if added and process is not None:
            ctx.report(f"Asked Tagging for {added:,} images", added, added)
            process.kick()


def _vlm_tier() -> bool:
    """Whether this host can run a VLM tagger (weights and server installed)."""
    from backend.services.scan_dispatch import should_tag_with_vlm
    from metascan.core.hardware import detect_hardware

    return bool(should_tag_with_vlm(detect_hardware()))


class Finish:
    name = "finish"
    always = True  # an interrupted pass still tells the snapshot code

    async def run(self, ctx: HygieneContext) -> CheckResult:
        scan = _scan()
        cancelled = ctx.stopped()
        missing = list(ctx.shared.get("missing_folders", []))
        back = (
            bool(ctx.shared.get("generated_back", True))
            and not ctx.shared.get("output_unreachable")
            and not ctx.shared.get("new_files_pending")
        )
        report = scan.PassReport(
            cancelled=cancelled,
            generated_back=back,
            missing_folders=missing,
            walked_folders=int(ctx.shared.get("walked_folders", 0)),
        )
        await asyncio.to_thread(self._finish, scan, report)
        return CheckResult()

    @staticmethod
    def _finish(scan: Any, report: Any) -> None:
        """Thread. The scan route's own ending, under the move gate: a scan
        that is starting owns the marker and the pending file."""
        gate: threading.Lock = scan.move_gate
        if not gate.acquire(blocking=False):
            return
        try:
            if scan.scan_running():
                return
            scan.finish_ordinary_pass(report)
        finally:
            gate.release()


class UpscaleOrphans:
    """The upscale leftovers no delete cleaned up: a swap still open (its
    commit or tidy-up failed), a record whose item left the library by a path
    that does not know about upscaling (a storyboard purge, a scorecard, a
    file removed by hand), and candidate folders with no rows."""

    name = "upscale leftovers"

    async def run(self, ctx: HygieneContext) -> CheckResult:
        from backend.dependencies import get_upscale_store

        store = get_upscale_store()
        if store is None or ctx.stopped():
            return CheckResult()
        changed = await asyncio.to_thread(self._sweep, ctx, store)
        return CheckResult(changed=changed)

    def _sweep(self, ctx: HygieneContext, store: Any) -> int:
        """Thread. Under the move gate: a swap's own critical section, and a
        scan that is starting, wait for this (and this for them)."""
        scan = _scan()
        if not scan.move_gate.acquire(blocking=False):
            return 0
        try:
            if _unfinished(scan, ctx.data_dir):
                return 0
            changed: int = store.recover()
            for record in ctx.db.upscale_records_without_media():
                if ctx.stopped():
                    break
                # Nothing is decided about a file that cannot be seen.
                path = to_native_path(record["file_path"])
                if scan._generated_file_state(path) != "gone":
                    continue
                if not _under_lost_folder(ctx, scan, path):
                    store.release([record["file_path"]])
                    changed += 1
            return changed
        finally:
            scan.move_gate.release()


def _under_lost_folder(ctx: HygieneContext, scan: Any, path: str) -> bool:
    """``path`` lies under a scan folder or output root that cannot be seen."""
    from backend.config import get_directories

    folders = [d.filepath for d in get_directories(ctx.config)]
    folders += scan._generated_output_roots(ctx.config)
    key = scan._absolute_key(path)
    return any(
        key.startswith(scan._absolute_key(folder))
        and scan._probe_folder(folder) in (scan.MISSING, scan.UNREADABLE)
        for folder in folders
    )


def default_checks() -> List[HygieneCheck]:
    return [
        MissingFiles(),
        NewFiles(),
        ChangedFiles(),
        MissingDerived(),
        UpscaleOrphans(),
        Finish(),
    ]
