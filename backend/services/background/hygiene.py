"""The Hygiene process: library checks that run when the server is quiet
(spec 10).

A pass runs the registered checks in order. It starts by itself only when
nothing else works (no other process ``running``), the owner has not changed
anything for ``IDLE_AFTER_S``, and the last complete pass is
``HYGIENE_INTERVAL_S`` old; the first pass after a start has no interval.
Play runs one now and ignores both timers (and a process that is running: a
manual pass is the owner's call). Hygiene is in nobody's ``holds``: a process
starting mid-pass makes an automatic pass stop at the next row or check
through ``HygieneContext.should_stop``, and the next quiet moment starts a
new pass. Pause and stop land the same way, between rows or checks; a hold
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Protocol, Sequence, Tuple

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


@dataclass
class CheckResult:
    changed: int = 0  # rows fixed or removed


@dataclass
class HygieneContext:
    db: Any
    config: Any
    data_dir: Optional[Path]
    import_process: Any  # vanished_since(path)
    should_stop: Callable[[], bool]
    report: Callable[[str, int, int], None]  # activity, done, total


class HygieneCheck(Protocol):
    name: str

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
        checks: Sequence[HygieneCheck] = (),
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
        self.checks: List[HygieneCheck] = list(checks)
        self._clock = clock
        self._idle_after_s = idle_after_s
        self._interval_s = interval_s
        self._poll_s = poll_s

        self._pass: Optional[asyncio.Task] = None
        self._manual = False
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
        return Capabilities(can_start=not running, can_pause=True, can_stop=running)

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
        if self._error:
            return ProcessReport(
                State.FAILED, None, "Library check failed", error=self._error
            )
        if self._ever_complete:
            return ProcessReport(State.IDLE, None, "In sync")
        return ProcessReport(State.IDLE, None, "Waiting for a quiet moment")

    async def start(self) -> None:
        """The owner's play: a pass now, ignoring both timers."""
        self._halted = False
        self._error = None
        self._begin(manual=True)

    async def pause(self) -> None:
        self._halted = True
        self._notify()

    async def resume(self) -> None:
        self._halted = False
        self._notify()

    async def stop(self) -> None:
        self._halted = True
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

    def _others_running(self) -> bool:
        return any(
            s.state == State.RUNNING and s.id != self.id
            for s in self._registry.statuses()
        )

    def tick(self) -> bool:
        """Start an automatic pass when the rule allows it. True if it did."""
        if self._running() or self._closed or self._halted:
            return False
        now = self._clock()
        if now < self._next_ok_at or self._idle.idle_for() < self._idle_after_s:
            return False
        if self._registry.blocked(self.id) or self._others_running():
            return False
        self._begin(manual=False)
        return True

    def _begin(self, manual: bool) -> None:
        if self._running() or self._closed:
            return
        self._manual = manual
        self._error = None
        self._activity = "Starting"
        self._progress = None
        self._pass = asyncio.get_running_loop().create_task(self._run_pass())
        self._notify()

    def should_stop(self) -> bool:
        if self._closed or self._halted:
            return True
        return not self._manual and self._others_running()

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
        )

    def _set_activity(self, activity: str, done: int = 0, total: int = 0) -> None:
        self._activity = _safe_activity(activity)
        self._progress = Progress(done, total, "items") if total else None
        self._notify()

    async def _run_pass(self) -> None:
        complete = False
        try:
            ctx = self._context()
            for check in list(self.checks):
                if self.should_stop():
                    break
                self._set_activity(f"Checking {check.name}")
                await check.run(ctx)
            else:
                complete = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = type(exc).__name__
            logger.warning("hygiene pass failed: %s", self._error)
        finally:
            if complete:
                self._ever_complete = True
            if complete or self._error:
                self._next_ok_at = self._clock() + self._interval_s
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
