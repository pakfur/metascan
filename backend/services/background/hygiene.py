"""The Hygiene process: library checks that run when the server is quiet
(spec 10).

A pass runs the registered checks in order. It starts by itself only when
nothing else works (no other process ``running``), the owner has not changed
anything for ``IDLE_AFTER_S``, and the last complete pass is
``HYGIENE_INTERVAL_S`` old; the first pass after a start has no interval.
Play ignores both timers but never runs alongside another process
(background work never delays the owner): it begins as soon as nothing else
is running, and until then the panel is ``waiting``. Hygiene is in nobody's
``holds``: a process starting mid-pass makes the pass, a manual one too, stop
at the next row or check through ``HygieneContext.should_stop``; an automatic
pass starts anew at the next quiet moment, a manual one stays pending. Pause and stop land the same way, between rows or checks; a hold
(Full clean) arrives as a pause.

A check that raises fails the pass with the exception's type only; later
checks do not run, the next pass tries again and Play retries at once.
Activity text carries counts and bare names, never a full path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from backend.services.background.idle import IDLE_AFTER_S, IdleTracker
from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry

logger = logging.getLogger(__name__)

HYGIENE_INTERVAL_S = 1800.0
POLL_S = 5.0
ACTIVITY_MAX = 120
OTHERS_CACHE_S = 1.0  # should_stop() is asked per row


@dataclass
class CheckResult:
    changed: int = 0  # rows fixed
    cleaned: int = 0  # rows removed


class MassRemovalBlocked(Exception):
    """A pass would clean too much of one folder: it cleaned nothing there
    and waits for the owner's Play. ``folder`` is the folder as configured,
    ``count`` the rows that would go; ``more`` other folders are blocked too."""

    def __init__(self, folder: str, count: int, more: int = 0) -> None:
        super().__init__(folder, count, more)
        self.folder = folder
        self.count = count
        self.more = more

    def message(self) -> str:
        text = f"{self.count:,} files missing from {self.folder}"
        if self.more:
            text += f" and {self.more} more"
        return text + ": press play to confirm"


@dataclass
class HygieneContext:
    db: Any
    config: Any
    data_dir: Optional[Path]
    import_process: Any  # vanished_since(path)
    should_stop: Callable[[], bool]
    report: Callable[[str, int, int], None]  # activity, done, total
    clock: Callable[[], float] = time.monotonic
    embedding: Any = None  # EmbeddingProcess (kick)
    tagging: Any = None  # TaggingProcess (kick)
    scanner: Any = None  # Scanner, for ingest_file
    thumbnails: Any = None  # ThumbnailCache
    confirmed: Any = field(default_factory=dict)  # folder -> rows shown at Play
    shared: Dict[str, Any] = field(default_factory=dict)  # one pass, all checks

    def stopped(self) -> bool:
        """Whether the pass must stop (``should_stop``), latched: a check cut
        short stays cut short for the checks after it, even when the reason
        goes away. Checks bail with this, between rows."""
        if self.shared.get("interrupted"):
            return True
        if self.should_stop():
            self.shared["interrupted"] = True
            return True
        return False


class HygieneCheck(Protocol):
    name: str
    # Optional ``always = True``: the check runs even when the pass was cut
    # short (Finish: an interrupted pass must still tell the snapshot code).

    async def run(self, ctx: HygieneContext) -> CheckResult: ...


_PATHISH = re.compile(r"[^\s]*[/\\]")


def _safe_activity(text: str) -> str:
    """No full path in a panel: keep what follows the last separator."""
    return _PATHISH.sub("", text)[:ACTIVITY_MAX]


class HygieneProcess:
    id = "hygiene"
    label = "Library hygiene"
    order = 80
    holds: Tuple[str, ...] = ()

    def __init__(
        self,
        registry: ProcessRegistry,
        idle: IdleTracker,
        *,
        get_db: Callable[[], Any],
        get_config: Callable[[], Any],
        get_data_dir: Callable[[Any], Optional[Path]],
        import_process: Callable[[], Any] = lambda: None,
        checks: Optional[Sequence[HygieneCheck]] = None,
        get_embedding: Callable[[], Any] = lambda: None,
        get_tagging: Callable[[], Any] = lambda: None,
        get_scanner: Callable[[], Any] = lambda: None,
        get_thumbnails: Callable[[], Any] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        idle_after_s: float = IDLE_AFTER_S,
        interval_s: float = HYGIENE_INTERVAL_S,
        poll_s: float = POLL_S,
    ) -> None:
        self._registry = registry
        self._idle = idle
        self._get_db = get_db
        self._get_config = get_config
        self._get_data_dir = get_data_dir
        self._import_process = import_process
        if checks is None:
            from backend.services.background.hygiene_checks import default_checks

            checks = default_checks()
        self.checks: List[HygieneCheck] = list(checks)
        self._get_embedding = get_embedding
        self._get_tagging = get_tagging
        self._get_scanner = get_scanner
        self._get_thumbnails = get_thumbnails
        self._blocked: Optional[MassRemovalBlocked] = None
        self._blocked_folders: List[str] = []
        self._confirmed: Dict[str, int] = {}  # folder -> rows Play confirmed
        self._blocked_counts: List[Tuple[str, int]] = []
        self._last_run = ""
        self._cleaned_last = 0
        self._clock = clock
        self._idle_after_s = idle_after_s
        self._interval_s = interval_s
        self._poll_s = poll_s

        self._pass: Optional[asyncio.Task] = None
        self._manual_pending = False  # Play pressed, not yet finished
        self._waiting_for = ""
        self._others: Tuple[float, List[str]] = (float("-inf"), [])
        self._halted = registry.persisted_intent(self.id) != "none"
        self._closed = False
        self._error: Optional[str] = None
        self._ever_complete = False
        self._next_ok_at = float("-inf")  # the interval, after a complete/failed pass
        self._activity = ""
        self._progress: Optional[Progress] = None
        self._loop_task: Optional[asyncio.Task] = None

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        running = self._running()
        return Capabilities(
            can_start=not (running or self._manual_pending),
            can_pause=True,
            can_stop=running or self._manual_pending,
        )

    def report(self) -> ProcessReport:
        if self._running():
            if self._halted:
                return ProcessReport(
                    State.RUNNING,
                    self._progress,
                    "Stopping after this step",
                    winding_down=True,
                )
            return ProcessReport(State.RUNNING, self._progress, self._activity)
        if self._manual_pending and not self._halted:
            return ProcessReport(
                State.WAITING, None, f"Waiting for {self._waiting_for or 'other work'}"
            )
        if self._error:
            text = self._error if self._blocked_folders else "Library check failed"
            return ProcessReport(State.FAILED, None, text, error=self._error)
        if self._ever_complete:
            return ProcessReport(State.IDLE, None, self._in_sync_text())
        return ProcessReport(State.IDLE, None, "Waiting for a quiet moment")

    def _in_sync_text(self) -> str:
        text = "In sync"
        if self._last_run:
            text += f", last run {self._last_run}"
        n = self._cleaned_last
        if n:
            text += f", cleaned {n:,} file{'s' if n != 1 else ''}"
        return text

    async def start(self) -> None:
        """The owner's play: a pass now, ignoring both timers. After a
        mass-removal block it also confirms the folder the panel names."""
        self._halted = False
        if self._running():
            self._error = None
            return
        if self._blocked_counts:
            folder, count = self._blocked_counts[0]
            self._confirmed = {folder: count}
        self._error = None
        self._manual_pending = True
        if not self._begin_if_clear():
            self._notify()

    async def pause(self) -> None:
        self._halted = True
        self._notify()

    async def resume(self) -> None:
        self._halted = False
        self._notify()

    async def stop(self) -> None:
        self._halted = True
        self._manual_pending = False
        self._notify()

    async def aclose(self) -> None:
        self._closed = True
        tasks = [t for t in (self._loop_task, self._pass) if t is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = self._pass = None

    # scheduling --------------------------------------------------------
    def start_loop(self) -> None:
        """Poll for a quiet moment on the running event loop (lifespan)."""
        if self._loop_task is None and not self._closed:
            self._loop_task = asyncio.get_running_loop().create_task(self._poll())

    async def _poll(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._poll_s)
            try:
                self.tick()
            except Exception:
                logger.exception("hygiene poll failed")

    def _running(self) -> bool:
        return self._pass is not None and not self._pass.done()

    def _running_others(self, fresh: bool = False) -> List[str]:
        """Labels of the other processes that are running. The registry
        snapshots every adapter, so the answer is kept ``OTHERS_CACHE_S``
        unless ``fresh``."""
        now = self._clock()
        if fresh or now - self._others[0] >= OTHERS_CACHE_S:
            labels = [
                s.label
                for s in self._registry.statuses()
                if s.state == State.RUNNING and s.id != self.id
            ]
            self._others = (now, labels)
        return self._others[1]

    def _begin_if_clear(self) -> bool:
        others = self._running_others(fresh=True)
        self._waiting_for = ", ".join(others)
        if others:
            return False
        self._begin()
        return True

    def tick(self) -> bool:
        """Start a pass when the rule allows it. True if it did."""
        if self._running() or self._closed or self._halted:
            return False
        if self._registry.blocked(self.id):
            return False
        if self._manual_pending:
            return self._begin_if_clear()
        now = self._clock()
        if now < self._next_ok_at or self._idle.idle_for() < self._idle_after_s:
            return False
        if self._running_others(fresh=True):
            return False
        self._begin()
        return True

    def _begin(self) -> None:
        if self._running() or self._closed:
            return
        self._error = None
        self._activity = "Starting"
        self._progress = None
        self._pass = asyncio.get_running_loop().create_task(self._run_pass())
        self._notify()

    def should_stop(self) -> bool:
        if self._closed or self._halted:
            return True
        return bool(self._running_others())

    # the pass ----------------------------------------------------------
    def _context(self) -> HygieneContext:
        db = self._get_db()
        return HygieneContext(
            db=db,
            config=self._get_config(),
            data_dir=self._get_data_dir(db),
            import_process=self._import_process(),
            should_stop=self.should_stop,
            report=self._set_activity,
            clock=self._clock,
            embedding=self._get_embedding(),
            tagging=self._get_tagging(),
            scanner=self._get_scanner(),
            thumbnails=self._get_thumbnails(),
            confirmed=dict(self._confirmed),
        )

    def _set_activity(self, activity: str, done: int = 0, total: int = 0) -> None:
        self._activity = _safe_activity(activity)
        self._progress = Progress(done, total, "items") if total else None
        self._notify()

    async def _run_pass(self) -> None:
        complete = False
        cleaned = 0
        blocked: List[Tuple[str, int]] = []
        ctx: Optional[HygieneContext] = None
        try:
            ctx = self._context()
            ran = set()
            for check in list(self.checks):
                if ctx.stopped():
                    break
                self._set_activity(f"Checking {check.name}")
                result = await check.run(ctx)
                ran.add(id(check))
                cleaned += getattr(result, "cleaned", 0)
            else:
                # cut short during the last check: not complete
                complete = not ctx.stopped()
            blocked = list(ctx.shared.get("blocked", ()))
            if not complete:
                # A check that must see every pass, an interrupted one too.
                for check in list(self.checks):
                    if getattr(check, "always", False) and id(check) not in ran:
                        await check.run(ctx)
            if blocked:
                first = blocked[0]
                self._error = MassRemovalBlocked(
                    first[0], first[1], len(blocked) - 1
                ).message()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = type(exc).__name__
            logger.warning("hygiene pass failed: %s", self._error)
        finally:
            if complete:
                self._ever_complete = True
            if complete or self._error:
                self._manual_pending = False
                self._next_ok_at = self._clock() + self._interval_s
                self._confirmed = {}  # one pass only
                self._blocked_folders = [f for f, _ in blocked]
                self._blocked_counts = list(blocked)
                if complete and not self._error:
                    self._cleaned_last = cleaned
                    self._last_run = time.strftime("%H:%M")
            elif self._confirmed:
                # Cut short: a confirmation that cleaned nothing is kept for
                # the next pass, one that was used up lapses.
                used = ctx.shared.get("deleted_in", set()) if ctx else set()
                self._confirmed = {
                    f: n for f, n in self._confirmed.items() if f not in used
                }
            self._pass = None
            self._progress = None
            self._notify()

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("hygiene notify failed")

    async def wait_pass(self) -> None:
        """Wait for the pass in flight (tests, shutdown)."""
        task = self._pass
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
