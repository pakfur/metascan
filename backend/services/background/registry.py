"""The process registry: holds, owner intent and coalesced status events.

State evaluation is synchronous and lock-protected (``notify`` may be called
from worker threads); the process calls it implies (pause/resume/start/stop)
are queued and run in order on the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import replace
from collections import Counter
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Optional,
    Tuple,
)

from .protocol import (
    BackgroundProcess,
    Capabilities,
    ProcessReport,
    ProcessStatus,
    State,
)

logger = logging.getLogger(__name__)

Action = Literal["start", "pause", "resume", "stop"]
NOTIFY_MIN_INTERVAL_S = 0.5  # two events per second per process
TICK_S = 0.1
# Chat, playground and writer prompting hold Tagging this long after a request:
# a chat has no clear end (spec 7).
INTERACTIVE_GRACE_S = 60.0


class CommandRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProcessRegistry:
    def __init__(
        self,
        state_path: Path,
        notify: Callable[[ProcessStatus], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._state_path = Path(state_path)
        self._emit = notify
        self._clock = clock
        self._lock = threading.RLock()
        self._procs: Dict[str, BackgroundProcess] = {}
        self._intent: Dict[str, str] = {}  # none | paused | stopped
        self._applied: Dict[str, str] = {}  # what the process was told
        self._holds: Dict[str, Counter] = {}  # target -> label -> count
        self._sticky_error: Dict[str, str] = {}
        self._cmd_error: Dict[str, str] = {}
        self._pending: List[Tuple[str, Action]] = []
        self._graces: List[Tuple[float, Tuple[str, ...], str]] = []
        self._last_emit: Dict[str, float] = {}
        self._dirty: set = set()
        self._updated: Dict[str, Tuple[Any, float]] = {}
        self._persisted = self._load()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ticker: Optional[asyncio.Task] = None
        self._drain_lock: Optional[asyncio.Lock] = None
        self._closed = False
        self._shutting_down = False
        self._settle_tasks: set = set()
        # Processes whose resume is queued or running: until it has returned,
        # a process still reporting PAUSED is on its way back, not paused.
        self._resuming: set = set()

    # persistence -------------------------------------------------------
    def _load(self) -> Dict[str, str]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {
                k: v
                for k, v in raw.get("intents", {}).items()
                if v in ("paused", "stopped")
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError):
            logger.warning("background state file unreadable; starting clean")
            return {}

    def _save(self) -> None:
        intents = {k: v for k, v in self._intent.items() if v != "none"}
        # keep intents of processes not registered (yet) in this run
        for k, v in self._persisted.items():
            if k not in self._procs:
                intents.setdefault(k, v)
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"intents": intents}), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            logger.exception("could not save background state")

    def persisted_intent(self, pid: str) -> str:
        """The owner intent saved by an earlier run ('paused', 'stopped' or
        'none'), readable before the process registers."""
        return self._persisted.get(pid, "none")

    def intent(self, pid: str) -> str:
        """The owner's current intent ('none', 'paused' or 'stopped'). No
        report or capability is evaluated, so adapters may call it from
        ``capabilities()``."""
        with self._lock:
            return self._intent.get(pid, "none")

    # registration ------------------------------------------------------
    def register(self, p: BackgroundProcess) -> None:
        with self._lock:
            if p.id in self._procs:
                raise ValueError(f"duplicate process id: {p.id}")
            self._check_cycle(p)
            self._procs[p.id] = p
            persisted = self._persisted.get(p.id, "none")
            self._intent[p.id] = persisted
            self._applied[p.id] = {"paused": "running", "stopped": "stopped"}.get(
                persisted, "running"
            )
        self._capture_loop()
        self.notify(p.id)

    def _check_cycle(self, p: BackgroundProcess) -> None:
        edges = {i: tuple(q.holds) for i, q in self._procs.items()}
        edges[p.id] = tuple(p.holds)
        seen: set = set()

        def walk(node: str, path: Tuple[str, ...]) -> None:
            if node in path:
                raise ValueError(f"holds cycle: {' -> '.join(path + (node,))}")
            if node in seen:
                return
            for nxt in edges.get(node, ()):
                walk(nxt, path + (node,))
            seen.add(node)

        walk(p.id, ())

    # evaluation ----------------------------------------------------------
    def _reports(self) -> Dict[str, ProcessReport]:
        # outside the lock: a report() may take the adapter's own locks
        out = {}
        for pid, p in list(self._procs.items()):
            try:
                out[pid] = p.report()
            except Exception as exc:  # a broken adapter must not break the rest
                logger.warning("report() of %s raised %s", pid, type(exc).__name__)
                out[pid] = ProcessReport(State.FAILED, None, "", error="report failed")
        return out

    def _capabilities(self) -> Dict[str, Capabilities]:
        # outside the lock, like _reports(): an adapter may take its own locks
        out = {}
        for pid, p in list(self._procs.items()):
            try:
                out[pid] = p.capabilities()
            except Exception as exc:
                logger.warning(
                    "capabilities() of %s raised %s", pid, type(exc).__name__
                )
                out[pid] = Capabilities(False, False, False)
        return out

    def _compute(
        self, reports: Dict[str, ProcessReport]
    ) -> Dict[str, Tuple[State, List[str]]]:
        """Effective (state, held_by) for every process. Needs the lock."""
        for pid, rep in reports.items():
            if rep.state == State.FAILED:
                self._sticky_error[pid] = rep.error or "failed"
            elif rep.state == State.RUNNING:
                self._sticky_error.pop(pid, None)
        for rid in self._resuming:
            held_rep = reports.get(rid)
            if (
                held_rep is not None
                and held_rep.state == State.PAUSED
                and self._intent.get(rid, "none") == "none"
            ):
                # The hold ended or the owner pressed play, but the adapter has
                # not run resume() yet: never show paused with no intent and no
                # holder (the owner's Play would be refused as not_paused).
                reports[rid] = replace(
                    held_rep, state=State.WAITING, activity="Resuming"
                )
        memo: Dict[str, Tuple[State, List[str]]] = {}

        def holders_of(pid: str) -> List[str]:
            labels: List[str] = []
            for hid, h in self._procs.items():
                if pid in h.holds and hid != pid and eff(hid)[0] == State.RUNNING:
                    labels.append(h.label)
            labels.extend(sorted(self._holds.get(pid, Counter()).elements()))
            return labels

        def eff(pid: str) -> Tuple[State, List[str]]:
            if pid in memo:
                return memo[pid]
            rep = reports[pid]
            intent = self._intent.get(pid, "none")
            held = holders_of(pid)
            res: Tuple[State, List[str]]
            if rep.winding_down and rep.state == State.RUNNING:
                # Pausing, held or stopped but the work is still going: it
                # counts as running, and keeps its holds, until it has ended.
                # The held list stays, so _reconcile still counts it suspended.
                res = (State.RUNNING, sorted(set(held)))
            elif intent == "stopped":
                res = (State.STOPPED, [])
            elif intent == "paused":
                res = (State.PAUSED, [])
            elif held:
                res = (State.HELD, sorted(set(held)))
            elif pid in self._sticky_error and rep.state in (
                State.IDLE,
                State.WAITING,
                State.FAILED,
            ):
                res = (State.FAILED, [])
            else:
                res = (rep.state, [])
            memo[pid] = res
            return res

        return {pid: eff(pid) for pid in reports}

    def _build(
        self,
        pid: str,
        rep: ProcessReport,
        caps: Capabilities,
        state: State,
        held_by: List[str],
    ) -> ProcessStatus:
        p = self._procs[pid]
        error = rep.error or self._cmd_error.get(pid)
        if state == State.FAILED:
            error = error or self._sticky_error.get(pid)
        st = ProcessStatus(
            id=pid,
            label=p.label,
            order=p.order,
            state=state,
            progress=rep.progress,
            activity=rep.activity,
            held_by=held_by if state == State.HELD else [],
            error=error,
            warnings_count=rep.warnings_count,
            capabilities=caps,
        )
        key = (
            st.state,
            st.progress,
            st.activity,
            tuple(st.held_by),
            st.error,
            st.warnings_count,
            st.capabilities,
        )
        prev = self._updated.get(pid)
        if prev is None or prev[0] != key:
            self._updated[pid] = (key, time.time())
        st.updated_at = self._updated[pid][1]
        return st

    def _reconcile(self, computed: Dict[str, Tuple[State, List[str]]]) -> None:
        """Queue the pause/resume calls the new state implies. Needs the lock."""
        if self._shutting_down:
            return  # no pause or resume is queued once the server is closing
        for pid in self._procs:
            intent = self._intent.get(pid, "none")
            if intent == "stopped":
                continue
            suspended = intent == "paused" or bool(computed[pid][1])
            applied = self._applied.get(pid, "running")
            if suspended and applied == "running":
                self._pending.append((pid, "pause"))
                self._applied[pid] = "paused"
            elif not suspended and applied == "paused":
                self._pending.append((pid, "resume"))
                self._resuming.add(pid)
                self._applied[pid] = "running"

    def _snapshot(self) -> Dict[str, ProcessStatus]:
        reports = self._reports()
        caps = self._capabilities()
        with self._lock:
            computed = self._compute(reports)
            return {
                pid: self._build(pid, reports[pid], caps[pid], *computed[pid])
                for pid in reports
                if pid in self._procs and pid in caps
            }

    def statuses(self) -> List[ProcessStatus]:
        snap = self._snapshot()
        return sorted(snap.values(), key=lambda s: (s.order, s.id))

    def status(self, pid: str) -> ProcessStatus:
        snap = self._snapshot()
        if pid not in snap:
            raise CommandRefused("unknown_process")
        return snap[pid]

    def failed(self, pid: str) -> bool:
        """True while ``pid`` shows ``failed`` until it runs or is started
        again (the sticky error). No report is evaluated."""
        with self._lock:
            return pid in self._sticky_error

    def blocked(self, pid: str) -> bool:
        """True when the process is held, paused or stopped (don't auto-start)."""
        return self.status(pid).state in (State.HELD, State.PAUSED, State.STOPPED)

    # notifications -----------------------------------------------------
    def notify(self, pid: str) -> None:
        """Re-evaluate holds and publish. Safe to call from any thread."""
        if pid not in self._procs:
            return
        reports = self._reports()
        caps = self._capabilities()
        with self._lock:
            computed = self._compute(reports)
            self._reconcile(computed)
            statuses = {
                p: self._build(p, reports[p], caps[p], *computed[p])
                for p in reports
                if p in self._procs and p in caps
            }
            self._dirty.add(pid)
            # a hold change can move other processes too
            self._dirty.update(statuses)
        self._flush(statuses, only_due=True)
        self._kick()

    def _flush(self, statuses: Dict[str, ProcessStatus], only_due: bool) -> None:
        to_send: List[ProcessStatus] = []
        with self._lock:
            now = self._clock()
            for pid in sorted(self._dirty):
                last = self._last_emit.get(pid)
                if only_due and last is not None and now - last < NOTIFY_MIN_INTERVAL_S:
                    continue
                if pid in statuses:
                    self._dirty.discard(pid)
                    self._last_emit[pid] = now
                    to_send.append(statuses[pid])
        for st in to_send:
            try:
                self._emit(st)
            except Exception:
                logger.exception("status notify callback failed")

    def tick(self) -> None:
        """Release due grace holds and flush coalesced status events."""
        released = False
        with self._lock:
            now = self._clock()
            due = [g for g in self._graces if g[0] <= now]
            self._graces = [g for g in self._graces if g[0] > now]
            for _, targets, by in due:
                self._drop(targets, by)
                released = True
            dirty = bool(self._dirty)
        if released:
            self.notify(next(iter(self._procs), ""))
        elif dirty:
            self._flush(self._snapshot(), only_due=True)

    # holds -------------------------------------------------------------
    def _add(self, targets: Iterable[str], by: str) -> None:
        with self._lock:
            for t in targets:
                self._holds.setdefault(t, Counter())[by] += 1

    def _drop(self, targets: Iterable[str], by: str) -> None:
        for t in targets:
            c = self._holds.get(t)
            if c is None:
                continue
            c[by] -= 1
            if c[by] <= 0:
                del c[by]
            if not c:
                del self._holds[t]

    def _poke(self, targets: Iterable[str]) -> None:
        for t in targets:
            if t in self._procs:
                self.notify(t)
                return
        if self._procs:
            self.notify(next(iter(self._procs)))

    @contextlib.contextmanager
    def hold(self, targets: Iterable[str], by: str) -> Iterator[None]:
        targets = tuple(targets)
        self._add(targets, by)
        self._poke(targets)
        try:
            yield
        finally:
            with self._lock:
                self._drop(targets, by)
            self._poke(targets)

    @contextlib.contextmanager
    def hold_for(
        self, targets: Iterable[str], by: str, grace_s: float
    ) -> Iterator[None]:
        """Hold for the body plus ``grace_s`` after it, however it ended.

        The grace timer belongs to the registry, so a cancelled request still
        releases.
        """
        targets = tuple(targets)
        self._add(targets, by)
        self._poke(targets)
        try:
            yield
        finally:
            with self._lock:
                self._graces.append((self._clock() + grace_s, targets, by))
            self._capture_loop()
            self.tick()

    # commands ----------------------------------------------------------
    async def command(self, pid: str, action: Action) -> ProcessStatus:
        self._capture_loop()
        if pid not in self._procs:
            raise CommandRefused("unknown_process")
        caps = self._procs[pid].capabilities()
        with self._lock:
            intent = self._intent[pid]
            if action == "pause":
                if intent == "stopped":
                    raise CommandRefused("stopped")
                if not caps.can_pause:
                    raise CommandRefused("not_pausable")
                self._intent[pid] = "paused"
            elif action == "stop":
                if not caps.can_stop:
                    raise CommandRefused("not_stoppable")
                self._intent[pid] = "stopped"
                self._applied[pid] = "stopped"
                self._pending.append((pid, "stop"))
            elif action == "resume":
                if intent != "paused":
                    raise CommandRefused("not_paused")
                self._intent[pid] = "none"
            elif action == "start":
                if intent == "paused":
                    self._intent[pid] = "none"
                else:
                    if intent == "none" and not caps.can_start:
                        raise CommandRefused("not_startable")
                    self._intent[pid] = "none"
                    self._sticky_error.pop(pid, None)
                    self._applied[pid] = "running"
                    self._pending.append((pid, "start"))
            else:
                raise CommandRefused("unknown_action")
            self._cmd_error.pop(pid, None)
            self._save()
        self.notify(pid)
        await self.settle()
        return self.status(pid)

    async def settle(self) -> None:
        """Run the queued process calls, in order, to completion."""
        self._capture_loop()
        if self._drain_lock is None:
            self._drain_lock = asyncio.Lock()
        async with self._drain_lock:
            while True:
                with self._lock:
                    if not self._pending:
                        return
                    pid, action = self._pending.pop(0)
                p = self._procs.get(pid)
                if p is None:
                    continue
                try:
                    await getattr(p, action)()
                    if action == "resume":
                        self._resumed(pid)
                    elif action == "start":
                        # An error shown before this start (the notify ran
                        # first) is cleared by it; a new failure sets it again.
                        with self._lock:
                            self._sticky_error.pop(pid, None)
                except Exception as exc:
                    if action == "resume":
                        self._resumed(pid)
                    logger.warning(
                        "%s of %s failed: %s", action, pid, type(exc).__name__
                    )
                    with self._lock:
                        self._cmd_error[pid] = f"{action} failed: {exc}"
                    self.notify(pid)

    def _resumed(self, pid: str) -> None:
        with self._lock:
            # a later pause queued behind this resume still counts as resuming
            # only while its own resume is pending
            if not any(a == "resume" and q == pid for q, a in self._pending):
                self._resuming.discard(pid)

    # plumbing ----------------------------------------------------------
    def _capture_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._loop = loop
        if self._ticker is None and not self._closed:
            self._ticker = loop.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            self.tick()

    def _kick(self) -> None:
        loop = self._loop
        if loop is None or self._closed or loop.is_closed():
            return
        with self._lock:
            if not self._pending:
                return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._spawn_settle(loop)
        else:
            loop.call_soon_threadsafe(self._spawn_settle, loop)

    def _spawn_settle(self, loop: asyncio.AbstractEventLoop) -> None:
        task = loop.create_task(self.settle())
        self._settle_tasks.add(task)
        task.add_done_callback(self._settle_tasks.discard)

    def begin_shutdown(self) -> None:
        """The server is closing: reconcile no more holds and queue no more
        pause/resume calls (pausing training must not restart Tagging or
        Embedding). Status reads and the owner's routes keep working."""
        with self._lock:
            self._shutting_down = True
            self._pending = []

    async def aclose(self) -> None:
        self._closed = True
        self._shutting_down = True
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
            self._ticker = None
        with self._lock:
            for _, targets, by in self._graces:
                self._drop(targets, by)
            self._graces = []
            self._pending = []  # a queued resume must not restart work now
