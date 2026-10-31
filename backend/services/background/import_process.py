"""The Import process: the always-on watcher and today's library scan (spec 6.2, 9).

Two jobs behind one panel row.

*The watcher* polls the configured scan folders (``FolderPoller``), ingests
what settles with ``Scanner.ingest_file`` one file at a time on a thread,
recognises moved and renamed files (``move_detector``) and re-keys their rows
with everything attached (``move_media_batch_applied``), then kicks Embedding
(debounced) so new files are embedded and tagged. It only reports ``running``
while it ingests, moves or settles files; polling alone is ``idle``. It is off
when ``watch_directories`` is false, suspended while a scan or Full clean runs
(the scan owns the library then, and the pending snapshot file), and
re-seeded from the database when either ends, pause or stop is lifted, or the
folder list changes. Import never deletes a row for a vanished file:
hygiene does, after ``MOVE_GRACE_S`` (``vanished_since``).

*The scan* is the route's work; this class only shows it and cancels it. Stop
is the ``/cancel`` path (``scan.request_cancel``), so a Full clean stopped here
is a cancelled one: the marker and the pending file stay and nothing is
tombstoned. Until the task has ended the process reports ``winding_down``.

The activity line carries counts and the bare file name, never a path. A
failure shows the exception's type only."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry
from metascan.core.folder_poller import (
    WATCH_INTERVAL_S,
    FolderPoller,
    FolderSpec,
)
from metascan.core.move_detector import MOVE_GRACE_S, Candidate, match_moves
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

ANNOUNCE_INTERVAL_S = 2.0  # at most one `library` event this often
EMBED_DEBOUNCE_S = 5.0  # after the last ingest
CLOSE_WAIT_S = 10.0  # aclose waits this long for a file being written
SETTLE_REPORT_S = 60.0  # a file still settling counts as work this long


INGESTED, SKIPPED, FAILED = "ingested", "skipped", "failed"


def _bare(path: str) -> str:
    return Path(path).name or path


class ImportProcess:
    id = "import"
    label = "Import"
    order = 10
    holds: Tuple[str, ...] = ()

    def __init__(
        self,
        registry: ProcessRegistry,
        status: Callable[[], Dict[str, Any]],
        cancel: Callable[[], bool],
        *,
        get_db: Optional[Callable[[], Any]] = None,
        get_config: Optional[Callable[[], Dict[str, Any]]] = None,
        make_scanner: Optional[Callable[[], Any]] = None,
        probe: Optional[Callable[[str], str]] = None,
        output_roots: Optional[Callable[[Dict[str, Any]], List[str]]] = None,
        broadcast: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        kick_embedding: Optional[Callable[[], Awaitable[Any]]] = None,
        after_move: Optional[Callable[[], Awaitable[Any]]] = None,
        compute_phash: Optional[Callable[[Path], Optional[str]]] = None,
        move_gate: Optional[threading.Lock] = None,
        clock: Callable[[], float] = time.monotonic,
        announce_interval_s: float = ANNOUNCE_INTERVAL_S,
        embed_debounce_s: float = EMBED_DEBOUNCE_S,
    ) -> None:
        self._registry = registry
        self._status = status
        self._cancel = cancel
        self._get_db = get_db
        self._get_config = get_config
        self._make_scanner = make_scanner
        self._probe = probe
        self._output_roots = output_roots
        self._broadcast = broadcast
        self._kick_embedding = kick_embedding
        self._after_move = after_move
        self._compute_phash = compute_phash
        self._move_gate = move_gate
        self._clock = clock
        self._announce_interval_s = announce_interval_s
        self._embed_debounce_s = embed_debounce_s

        # watcher state (the event loop's; read by report() from any thread)
        self._watch_on: Optional[bool] = None  # None until the first pass
        self._poller: Optional[FolderPoller] = None
        self._signature: Optional[Tuple[Any, ...]] = None
        self._folder_count = 0
        self._unavailable: List[str] = []
        self._resync = True
        self._scan_seen_running = False
        self._scanner: Any = None
        self._held: Dict[str, None] = {}  # settled, waiting one pass for a move
        self._todo: List[str] = []
        self._retry_next: List[str] = []
        self._tries: Dict[str, int] = {}
        self._bad: Dict[str, Tuple[int, float]] = {}  # failed twice: path -> stamp
        self._vanished: Dict[str, float] = {}  # stored path -> when it vanished
        self._settle_seen: Dict[str, float] = {}
        self._batch_done = 0
        self._batch_total = 0
        self._current: Optional[str] = None
        self._moving = 0
        self._inflight = False
        self._error: Optional[str] = None
        self._respell_retry: List[Tuple[str, str]] = []
        self._kick_dirty = False
        self._kick_task: Optional[asyncio.Task] = None
        self._threads: Set[asyncio.Future] = set()
        self._last_prune = float("-inf")
        self._announce_pending = 0
        self._last_announce = float("-inf")
        self._announce_handle: Optional[asyncio.TimerHandle] = None
        self._kick_handle: Optional[asyncio.TimerHandle] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._closed = False

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        st = self._status()
        running = bool(st["running"])
        full = running and bool(st["full_clean"])
        watching = self._watch_on is True
        intent = self._registry.intent(self.id)
        failed = bool(st["error"] or self._error or self._registry.failed(self.id))
        return Capabilities(
            can_start=intent in ("paused", "stopped") or failed,
            can_pause=watching and not full,
            can_stop=running or watching,
            confirm_stop=full,
        )

    def report(self) -> ProcessReport:
        st = self._status()
        self._note_scan(bool(st["running"]))
        if st["running"]:
            return self._report_scan(st)
        if st["error"]:
            return ProcessReport(State.FAILED, None, "Scan failed", error=st["error"])
        if self._error:
            return ProcessReport(State.FAILED, None, "Import failed", error=self._error)
        if self._watch_on is None:
            return ProcessReport(State.IDLE, None, "Not running")
        if not self._watch_on:
            return ProcessReport(State.IDLE, None, "Watching is off")
        warnings = len(self._bad)
        if self._inflight:
            # Pause or stop lands at the next file; this one finishes first.
            winding = self._halted()
            return self._working(warnings, winding)
        if not self._halted() and self._has_work():
            return self._working(warnings, False)
        if self._unavailable:
            names = ", ".join(_bare(p) for p in self._unavailable)
            return ProcessReport(
                State.WAITING, None, f"Waiting: {names} unavailable", None, warnings
            )
        intent = self._registry.intent(self.id)
        if intent in ("paused", "stopped"):
            return ProcessReport(State.IDLE, None, intent.capitalize(), None, warnings)
        n = self._folder_count
        what = "no folders" if n == 0 else f"{n} folder{'s' if n != 1 else ''}"
        return ProcessReport(State.IDLE, None, f"Watching {what}", None, warnings)

    def _working(self, warnings: int, winding: bool) -> ProcessReport:
        total = self._batch_total
        progress = Progress(self._batch_done, total, "files") if total else None
        if self._moving:
            text = f"Moving {self._moving} files"
        elif self._current:
            text = f"Importing {self._current}"
        elif self._todo or self._held:
            text = "Importing"
        else:
            text = "Settling new files"
        if winding:
            text = f"Stopping: {text}"
        return ProcessReport(State.RUNNING, progress, text, None, warnings, winding)

    def _report_scan(self, st: Dict[str, Any]) -> ProcessReport:
        total = st["total"]
        progress = (
            Progress(int(st["current"] or 0), int(total), "files") if total else None
        )
        label = "Full clean" if st["full_clean"] else "Scan"
        parts = [label]
        phase = st["phase"]
        if phase == "scanning" and st["dir_total"]:
            parts.append(f"folder {st['dir_current']} of {st['dir_total']}")
            if total:
                parts.append(f"file {st['current']} of {total}")
            if st["file"]:
                parts.append(str(st["file"]))
        elif phase == "stale_cleanup":
            parts.append("removing stale entries")
        elif phase == "full_clean":
            parts.append("clearing the library")
        else:
            parts.append("starting")
        activity = ", ".join(parts)
        if st["cancelling"]:
            return ProcessReport(
                State.RUNNING, progress, f"Stopping: {activity}", winding_down=True
            )
        return ProcessReport(State.RUNNING, progress, activity)

    async def start(self) -> None:
        self._error = None
        self._catch_up()

    async def pause(self) -> None:
        self._wake_loop()

    async def resume(self) -> None:
        self._catch_up()

    async def stop(self) -> None:
        # Outside a scan there is nothing to cancel: the watcher halts at the
        # next file (the registry's stopped intent).
        self._cancel()
        self.poke()

    async def owner_scan_starting(self) -> None:
        """``POST /api/scan/start``: the owner starting a scan clears a Stop."""
        try:
            if self._registry.intent(self.id) == "stopped":
                await self._registry.command(self.id, "start")
        except CommandRefused as exc:
            logger.debug("import owner start refused: %s", exc.code)

    def poke(self) -> None:
        try:
            self._note_scan(bool(self._status()["running"]))
            self._registry.notify(self.id)
        except Exception:
            logger.exception("import notify failed")

    # hygiene's view -----------------------------------------------------
    def vanished_since(self, path: str) -> Optional[float]:
        """When (``clock()`` time) the watcher saw the file at ``path`` vanish,
        or None if it has not (it was never watched, or it came back).
        Hygiene waits ``MOVE_GRACE_S`` from this before it cleans the row."""
        return self._vanished.get(to_posix_path(path))

    def is_settling(self, path: str) -> bool:
        """Whether Import has ``path`` in hand: waiting to settle, held one
        pass for a move, or queued to ingest. Hygiene leaves such a file to
        Import."""
        native = to_native_path(path)
        return (
            native in self._settle_seen
            or native in self._held
            or native in self._todo
            or native in self._retry_next
        )

    def gave_up_on(self, path: str) -> bool:
        """Import tried this file twice and left it alone, and it has not
        changed since."""
        stamp = self._bad.get(path)
        if stamp is None:
            return False
        try:
            st = os.stat(path)
        except OSError:
            return False
        return stamp == (st.st_size, st.st_mtime)

    def accepts_unknown(self) -> bool:
        """Whether Import would take files from hygiene: watching is on and
        it is not paused or stopped (the owner's choice)."""
        return self._watch_on is True and not self._halted()

    async def enqueue_unknown(self, paths: Sequence[str]) -> int:
        """Files hygiene found on disk that the library does not have: they
        go through Import's own pipeline (``_intake``: move check, a pass in
        hand, ingest, ``library`` event, embedding kick), not a second ingest
        path. Nothing is taken while Import is paused, stopped or not
        watching. Event loop only. Returns how many were handed over."""
        if not self.accepts_unknown():
            return 0
        known = set(self._todo) | set(self._held) | set(self._retry_next)
        fresh = [p for p in paths if p not in known and not self.gave_up_on(p)]
        if not fresh:
            return 0
        await self._intake(fresh)
        self._wake_loop()
        self.poke()
        return len(fresh)

    async def apply_moves(
        self, pairs: Sequence[Tuple[str, str]]
    ) -> Optional[List[Tuple[str, str]]]:
        """Re-key rows for moves found elsewhere (hygiene): under the move
        gate, then the pending snapshot, the FAISS rows and the grid, exactly
        as the watcher's own moves. Whatever Import's state: a move keeps
        ratings, it is not an ingest. None when a scan runs."""
        return await self._apply_moves(list(pairs))

    # lifecycle -----------------------------------------------------------
    def start_loop(self) -> None:
        """Start the polling loop on the running event loop (lifespan)."""
        if self._task is not None or self._closed:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = self._loop.create_task(self._run())

    async def aclose(self) -> None:
        self._closed = True
        for handle in (self._announce_handle, self._kick_handle):
            if handle is not None:
                handle.cancel()
        self._announce_handle = self._kick_handle = None
        for t in (self._task, self._kick_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("import loop ended with an error")
        self._task = self._kick_task = None
        # A file being written must not finish after the database closes.
        if self._threads:
            _, late = await asyncio.wait(set(self._threads), timeout=CLOSE_WAIT_S)
            if late:
                logger.warning("import: a file was still being written at shutdown")

    async def _thread(self, fn: Callable[..., Any], *args: Any) -> Any:
        """``to_thread`` that ``aclose`` can wait for: cancelling the loop
        leaves the thread running, so the future is tracked."""
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        self._threads.add(fut)
        fut.add_done_callback(self._threads.discard)
        return await asyncio.shield(fut)

    # the loop ------------------------------------------------------------
    async def _run(self) -> None:
        assert self._wake is not None
        while not self._closed:
            try:
                await self.run_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A pass failing is shown (type only) and tried again.
                logger.exception("import pass failed")
                self._error = type(exc).__name__
                self.poke()
            poller = self._poller
            wait = poller.next_interval() if poller else WATCH_INTERVAL_S
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    def _wake_loop(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    def _catch_up(self) -> None:
        """Start and resume: the next pass compares disk with the database."""
        self._resync = True
        self._wake_loop()
        self.poke()

    def _note_scan(self, running: bool) -> None:
        """A scan or Full clean ending changes the library under the watcher:
        it re-seeds from the database."""
        if running:
            self._scan_seen_running = True
        elif self._scan_seen_running:
            self._scan_seen_running = False
            self._resync = True
            self._wake_loop()

    def _halted(self) -> bool:
        return self._registry.intent(self.id) != "none"

    def _scan_running(self) -> bool:
        return bool(self._status()["running"])

    def _has_work(self) -> bool:
        if self._todo or self._held or self._retry_next or self._moving:
            return True
        now = self._clock()
        return any(now - t < SETTLE_REPORT_S for t in self._settle_seen.values())

    # one pass --------------------------------------------------------------
    async def run_pass(self) -> None:
        """Poll once, apply moves, ingest what settled. Never blocks the event
        loop: polling and database work run in threads."""
        config = await asyncio.to_thread(self._config)
        on = bool(config.get("watch_directories", True))
        if on != self._watch_on:
            self._watch_on = on
            if not on:
                self._reset_watch()
            self.poke()
        scanning = self._scan_running()
        self._note_scan(scanning)
        if not on or scanning or self._halted():
            return
        await self._ensure_poller(config)
        poller = self._poller
        assert poller is not None
        result = await asyncio.to_thread(poller.poll)
        self._unavailable = list(result.unavailable)
        self._track_settling(poller)
        now = self._clock()
        self._forget(result.vanished)
        for path in result.vanished:
            self._vanished.setdefault(to_posix_path(path), now)
        for path in result.changed:
            if path in self._bad:
                del self._bad[path]
                self._tries.pop(path, None)
                self._todo.append(path)
        await self._respell(result.respelled)
        await self._prune_vanished()
        await self._intake(result.settled_new)
        self.poke()
        await self._drain()
        if self._kick_dirty and not self._todo and self._kick_handle is None:
            await self._kick()
        self._error = None  # a clean pass ends a transient failure
        self.poke()

    async def _respell(self, found: Sequence[Tuple[str, str]]) -> None:
        """Case-only renames: the poller records the new spelling once, so a
        pair that could not be applied is kept for the next pass."""
        pairs = [*self._respell_retry, *found]
        self._respell_retry = []
        if not pairs:
            return
        posix = [(to_posix_path(o), to_posix_path(n)) for o, n in pairs]
        try:
            done = await self._apply_moves(posix)
        except Exception:
            logger.exception("import: a respelled file could not be re-keyed")
            done = None
        if done is None:
            self._respell_retry = pairs

    async def _prune_vanished(self) -> None:
        """Drop vanished entries whose row is gone (hygiene or a delete took
        it): the dict holds rows only."""
        now = self._clock()
        if not self._vanished or now - self._last_prune < MOVE_GRACE_S:
            return
        self._last_prune = now
        keys = list(self._vanished)
        present = await self._thread(self._db().media_paths_present, keys)
        for k in keys:
            if k not in present:
                self._vanished.pop(k, None)

    def _config(self) -> Dict[str, Any]:
        if self._get_config is not None:
            return self._get_config()
        from backend.config import load_app_config

        return load_app_config()

    def _db(self) -> Any:
        if self._get_db is not None:
            return self._get_db()
        from backend.dependencies import get_db

        return get_db()

    def _reset_watch(self) -> None:
        self._poller = None
        self._signature = None
        self._unavailable = []
        self._held.clear()
        self._todo.clear()
        self._retry_next.clear()
        self._settle_seen.clear()
        self._folder_count = 0
        self._batch_done = self._batch_total = 0

    async def _ensure_poller(self, config: Dict[str, Any]) -> None:
        from backend.config import get_directories
        from metascan.core.scanner import Scanner

        folders = [
            FolderSpec(d.filepath, d.search_subfolders) for d in get_directories(config)
        ]
        roots = self._roots(config)
        signature = (tuple(folders), tuple(roots))
        if (
            self._poller is not None
            and signature == self._signature
            and not self._resync
        ):
            return
        if self._probe is not None:
            probe = self._probe
        else:
            from backend.api.scan import _probe_folder as probe
        supported = Scanner.SUPPORTED_EXTENSIONS
        poller = FolderPoller(
            folders,
            probe,
            roots,
            lambda p: Path(p).suffix.lower() in supported,
        )
        # The database holds POSIX spellings; the walk reports native ones.
        stored = await self._thread(self._db().all_media_paths)
        poller.seed([to_native_path(p) for p in stored])
        self._poller = poller
        self._signature = signature
        self._folder_count = len(folders)
        self._resync = False
        # What the old poller had half-settled is found again by the new one.
        self._held.clear()

    def _roots(self, config: Dict[str, Any]) -> List[str]:
        if self._output_roots is not None:
            return list(self._output_roots(config))
        from backend.api.scan import _generated_output_roots

        return _generated_output_roots(config)

    def _track_settling(self, poller: FolderPoller) -> None:
        now = self._clock()
        pending = set(poller.pending_paths())
        for p in pending:
            self._settle_seen.setdefault(p, now)
        for p in list(self._settle_seen):
            if p not in pending:
                del self._settle_seen[p]

    def _forget(self, paths: Sequence[str]) -> None:
        """Watched files vanished: whatever was queued or failed for them goes."""
        gone = set(paths)
        if not gone:
            return
        for p in gone:
            self._bad.pop(p, None)
            self._tries.pop(p, None)
            self._held.pop(p, None)
        self._todo = [p for p in self._todo if p not in gone]
        self._retry_next = [p for p in self._retry_next if p not in gone]

    # moves ---------------------------------------------------------------
    async def _intake(self, settled: Sequence[str]) -> None:
        """Settled new files: first see whether each is a moved file. Files
        that are not wait one pass beside the vanished list (so both sides of
        a move are seen), then ingest."""
        ready = list(self._held)
        fresh = [p for p in settled if p not in self._held]
        for p in settled:
            self._vanished.pop(to_posix_path(p), None)
        candidates = ready + fresh
        moved: List[str] = []
        if candidates and self._vanished:
            self._moving = len(self._vanished)
            self._inflight = True
            self.poke()
            try:
                pairs = await self._thread(
                    self._detect_moves, candidates, dict(self._vanished)
                )
                applied = await self._apply_moves(pairs)
            finally:
                self._moving = 0
                self._inflight = False
            if applied is None:
                # A scan took the library meanwhile: look again afterwards.
                self._held = {p: None for p in candidates}
                return
            moved = [new for _, new in applied]
        moved_set = set(moved)
        fresh_set = set(fresh)
        keep = [p for p in candidates if to_posix_path(p) not in moved_set]
        self._held = {p: None for p in keep if p in fresh_set}
        self._queue([p for p in keep if p not in fresh_set])

    def _queue(self, paths: Sequence[str]) -> None:
        carried, self._retry_next = self._retry_next, []
        queued = set(self._todo)
        fresh = []
        for p in [*carried, *paths]:
            if p not in queued:
                queued.add(p)
                fresh.append(p)
        self._todo.extend(fresh)
        self._batch_total += len(fresh)

    def _detect_moves(
        self, new_paths: Sequence[str], vanished: Dict[str, float]
    ) -> List[Tuple[str, str]]:
        """Thread. ``(old, new)`` stored-path pairs for the settled new files
        that are a vanished row's file."""
        db = self._db()
        now = self._clock()
        old: List[Candidate] = []
        for path, since in vanished.items():
            if now - since > MOVE_GRACE_S:
                continue
            cand = db.candidate_for(path)
            if cand is not None:
                old.append(cand)
        if not old:
            return []
        sizes = {c.size for c in old}
        if self._compute_phash is not None:
            compute = self._compute_phash
        else:
            from metascan.core.phash_utils import compute_phash_for_file as compute
        new: List[Candidate] = []
        for p in new_paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            phash = None
            if st.st_size in sizes:
                # Only a file some vanished one could be is worth hashing.
                try:
                    phash = compute(Path(p))
                except Exception:
                    phash = None
            new.append(Candidate(to_posix_path(p), st.st_size, st.st_mtime, phash))
        return match_moves(new, old)

    async def _apply_moves(
        self, pairs: Sequence[Tuple[str, str]]
    ) -> Optional[List[Tuple[str, str]]]:
        """Re-key the rows (one batch), then the pending snapshot, the FAISS
        rows and the grid. The applied pairs, or None when a scan or Full
        clean runs: it owns the library and the pending file."""
        if not pairs:
            return []
        applied: Optional[List[Tuple[str, str]]] = await self._thread(
            self._move_locked, list(pairs)
        )
        if not applied:
            return applied
        for old, _ in applied:
            self._vanished.pop(old, None)
        await self._after_media_move()
        self._announce(len(applied))
        return applied

    def _gate(self) -> threading.Lock:
        if self._move_gate is None:
            from backend.api.scan import move_gate

            self._move_gate = move_gate
        return self._move_gate

    def _move_locked(
        self, pairs: List[Tuple[str, str]]
    ) -> Optional[List[Tuple[str, str]]]:
        """Thread. The scan-running check and the move are one critical
        section with the moment the scan route marks itself running (it takes
        the same gate), so a move cannot land after a scan's snapshot."""
        from backend.services import taste_snapshot

        with self._gate():
            if self._scan_running():
                return None
            db = self._db()
            applied: List[Tuple[str, str]] = db.move_media_batch_applied(pairs)
            if applied:
                try:
                    taste_snapshot.rekey_pending(Path(db.db_path), applied)
                except Exception as exc:
                    # The rows moved; the pending file is read by the next scan.
                    logger.warning(
                        "pending snapshot not re-keyed: %s", type(exc).__name__
                    )
            return applied

    async def _after_media_move(self) -> None:
        try:
            if self._after_move is not None:
                await self._after_move()
            else:
                from backend.api import similarity

                await similarity.after_media_move()
        except Exception:
            logger.exception("after_media_move failed")

    # ingest ----------------------------------------------------------------
    async def _drain(self) -> None:
        while self._todo and not self._halted() and not self._scan_running():
            path = self._todo.pop(0)
            self._current = _bare(path)
            self._inflight = True
            self.poke()
            try:
                outcome = await self._thread(self._ingest_one, path)
            finally:
                self._inflight = False
                self._current = None
            self._batch_done += 1
            if outcome == INGESTED:
                self._tries.pop(path, None)
                self._announce(1)
                self._schedule_kick()
            elif outcome == FAILED:
                self._failed(path)
            else:
                self._tries.pop(path, None)
            self.poke()
        if not self._todo and not self._retry_next:
            self._batch_done = self._batch_total = 0

    def _ingest_one(self, path: str) -> str:
        """Thread. ``INGESTED``, ``SKIPPED`` (gone, or already in the library:
        ComfyClient's own ingest wrote its row with lineage) or ``FAILED``."""
        if self._scanner is None:
            if self._make_scanner is not None:
                self._scanner = self._make_scanner()
            else:
                from backend.dependencies import get_thumbnail_cache
                from metascan.core.scanner import Scanner

                self._scanner = Scanner(self._db(), get_thumbnail_cache())
        if not os.path.exists(path):
            return SKIPPED  # gone again: the poller reports it vanished
        try:
            if self._db().candidate_for(to_posix_path(path)) is not None:
                return SKIPPED
            ok = self._scanner.ingest_file(Path(path)) is not None
        except Exception as exc:
            logger.warning("import of a file failed: %s", type(exc).__name__)
            return FAILED
        return INGESTED if ok else FAILED

    def _failed(self, path: str) -> None:
        """Retried once on the next pass, then counted and left until the file
        changes."""
        tries = self._tries.get(path, 0) + 1
        if tries < 2:
            self._tries[path] = tries
            self._retry_next.append(path)
            return
        self._tries.pop(path, None)
        try:
            st = os.stat(path)
            self._bad[path] = (st.st_size, st.st_mtime)
        except OSError:
            return
        logger.warning("a file could not be imported twice; it is left alone")

    # the grid and the pipeline ------------------------------------------------
    def _announce(self, count: int) -> None:
        """A ``library`` / ``media_added`` event, at most one every
        ``announce_interval_s``; what arrives in between goes out together."""
        self._announce_pending += count
        loop = self._loop or _running_loop()
        now = self._clock()
        wait = self._last_announce + self._announce_interval_s - now
        if wait <= 0:
            self._send_announce()
        elif self._announce_handle is None and loop is not None:
            self._announce_handle = loop.call_later(wait, self._send_announce)

    def _send_announce(self) -> None:
        self._announce_handle = None
        n, self._announce_pending = self._announce_pending, 0
        if not n or self._closed:
            return
        self._last_announce = self._clock()
        try:
            if self._broadcast is not None:
                self._broadcast("library", "media_added", {"count": n})
            else:
                from backend.ws.manager import ws_manager

                ws_manager.broadcast_sync("library", "media_added", {"count": n})
        except Exception:
            logger.exception("library broadcast failed")

    def _schedule_kick(self) -> None:
        """Embedding runs once ingest has been quiet for the debounce."""
        loop = self._loop or _running_loop()
        if loop is None or self._closed:
            return
        self._kick_dirty = True
        if self._kick_handle is not None:
            self._kick_handle.cancel()
        self._kick_handle = loop.call_later(self._embed_debounce_s, self._start_kick)

    def _start_kick(self) -> None:
        self._kick_handle = None
        loop = self._loop or _running_loop()
        if loop is not None and not self._closed:
            self._kick_task = loop.create_task(self._kick())

    async def _kick(self) -> None:
        """Ask Embedding for an incremental build. Its answer matters: while a
        build runs (or the GPU is unavailable) it says busy or refused and the
        new files would wait for the next drop, so the request stays pending
        and the next pass asks again. Blocked (held, paused, stopped) needs no
        retry: resume and release build when files remain."""
        if self._closed:
            return
        if self._todo or self._inflight:
            self._schedule_kick()  # still ingesting
            return
        try:
            if self._kick_embedding is not None:
                answer = await self._kick_embedding()
            else:
                from backend.dependencies import get_embedding_process

                embedding = get_embedding_process()
                answer = await embedding.kick(True) if embedding else None
        except Exception:
            logger.exception("embedding kick failed")
            return
        status = answer.get("status") if isinstance(answer, dict) else None
        self._kick_dirty = status in ("busy", "refused")


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
