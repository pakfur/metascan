"""The Upscaling process: the upscale queue behind the registry (spec 6.2, 6.3, 7).

The queue is durable (``queue.json``) and its worker is a subprocess, so the
adapter keeps no queue of its own. Pause is the queue's existing pause: it
kills the running worker and resets that file to 0, so the activity says "the
current file restarts". Pause, resume and stop call the same functions the
``/pause-all`` and ``/resume-all`` routes call. While paused (or after a
restart with a persisted owner pause) ``queue.gated`` keeps the poller from
claiming anything; a task the owner adds meanwhile is paused on arrival.

Counts only in the activity line, never a path or file name.

Everything runs on the server loop except ``report`` and ``poke``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry
from metascan.core.gpu_guard import gpu_reserved

logger = logging.getLogger(__name__)

REPORT_TTL_S = 0.5  # the queue is a file: do not read it per notify
RESTART_NOTE = "Paused; the current file restarts"

_ACTIVE = ("pending", "processing", "downloading_models")
_OPEN = _ACTIVE + ("paused",)


class UpscaleProcess:
    id = "upscale"
    label = "Upscaling"
    order = 60
    holds: Tuple[str, ...] = ("tagging", "embedding")

    def __init__(
        self,
        registry: ProcessRegistry,
        get_queue: Callable[[], Any],
        pause_queue: Callable[[], Awaitable[int]],
        resume_queue: Callable[[], Awaitable[int]],
        *,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        import time

        self._registry = registry
        self._get_queue = get_queue
        self._pause_queue = pause_queue
        self._resume_queue = resume_queue
        self._clock = clock or time.monotonic
        self._paused = False
        self._cache: Optional[Tuple[float, List[Any]]] = None
        self.last_count = 0  # tasks the last pause / resume changed
        # The queue's own callbacks move the panel (progress, ends).
        queue = get_queue()
        for name in ("on_task_updated", "on_task_added", "on_task_removed"):
            self._chain(queue, name)
        queue.on_queue_changed = self._chain_fn(queue.on_queue_changed)
        # A persisted owner pause: nothing is claimed before pause() is re-applied.
        if registry.persisted_intent(self.id) == "paused":
            self._paused = True
            queue.gated = True

    # callbacks ---------------------------------------------------------
    def _chain_fn(self, previous: Optional[Callable[..., None]]) -> Callable[..., None]:
        def chained(*args: Any) -> None:
            self._cache = None
            if previous is not None:
                try:
                    previous(*args)
                except Exception:
                    logger.exception("upscale callback failed")
            self._notify()

        return chained

    def _chain(self, queue: Any, name: str) -> None:
        setattr(queue, name, self._chain_fn(getattr(queue, name)))

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=True, can_pause=True, can_stop=True)

    def _tasks(self) -> List[Any]:
        now = self._clock()
        cached = self._cache
        if cached is not None and now - cached[0] < REPORT_TTL_S:
            return cached[1]
        try:
            tasks = list(self._get_queue().get_all_tasks())
        except Exception:
            logger.debug("upscale queue read failed", exc_info=True)
            return cached[1] if cached is not None else []
        self._cache = (now, tasks)
        return tasks

    @staticmethod
    def _status(task: Any) -> str:
        return str(getattr(task.status, "value", task.status))

    def report(self) -> ProcessReport:
        tasks = self._tasks()
        by: Dict[str, List[Any]] = {}
        for t in tasks:
            by.setdefault(self._status(t), []).append(t)
        n = lambda *names: sum(len(by.get(x, [])) for x in names)  # noqa: E731
        done = n("completed")
        total = done + n("failed", *_OPEN)
        progress = Progress(done, total, "files") if total else None
        warnings = n("failed")
        open_count = n(*_OPEN)
        if n("paused") or (self._paused and open_count):
            return ProcessReport(
                State.PAUSED, progress, RESTART_NOTE, warnings_count=warnings
            )
        if self._paused:
            return ProcessReport(
                State.PAUSED, progress, "Paused", warnings_count=warnings
            )
        running = n("processing", "downloading_models")
        if running:
            current = by.get("processing") or by.get("downloading_models") or []
            pct = int(float(getattr(current[0], "progress", 0) or 0))
            text = f"File {min(done + 1, total)} of {total}, {pct}%"
            if not by.get("processing"):
                text = f"File {min(done + 1, total)} of {total}, downloading models"
            return ProcessReport(State.RUNNING, progress, text, warnings_count=warnings)
        if n("pending"):
            if gpu_reserved():
                return ProcessReport(
                    State.WAITING,
                    progress,
                    "Waiting: GPU in use",
                    warnings_count=warnings,
                )
            return ProcessReport(
                State.RUNNING,
                progress,
                f"File {min(done + 1, total)} of {total}, starting",
                warnings_count=warnings,
            )
        return ProcessReport(
            State.IDLE, None, "No upscale tasks", warnings_count=warnings
        )

    async def start(self) -> None:
        """The owner's play from idle / stopped / failed (or a legacy pause)."""
        self._paused = False
        self._get_queue().gated = False
        self.last_count = await self._resume_queue()
        await asyncio.to_thread(self._get_queue().start_processing)
        self._cache = None
        self._notify()

    async def pause(self) -> None:
        self._paused = True
        self._get_queue().gated = True
        self.last_count = await self._pause_queue()
        self._cache = None
        self._notify()

    async def resume(self) -> None:
        self._paused = False
        self._get_queue().gated = False
        self.last_count = await self._resume_queue()
        # Tasks added while paused were paused on arrival; pending ones from a
        # persisted pause were never claimed. Either way start the workers.
        await asyncio.to_thread(self._get_queue().start_processing)
        self._cache = None
        self._notify()

    async def stop(self) -> None:
        """Today's cancel: every open task is cancelled (the running worker is
        signalled). Nothing restarts until the owner adds a task."""
        queue = self._get_queue()
        self._paused = False
        queue.gated = False
        for task in list(await asyncio.to_thread(queue.get_all_tasks)):
            if self._status(task) in _OPEN:
                await asyncio.to_thread(queue.cancel_task, task.id)
        self._cache = None
        self._notify()

    # entry points ------------------------------------------------------
    @property
    def gated(self) -> bool:
        return self._paused

    async def owner_task_adding(self) -> None:
        """The owner is adding a task: that clears a stop. A paused process
        accepts it and queues it, still paused."""
        try:
            if self._registry.status(self.id).state == State.STOPPED:
                await self._registry.command(self.id, "start")
        except CommandRefused as exc:
            logger.debug("upscale owner start refused: %s", exc.code)

    async def task_added(self) -> None:
        """After the owner's tasks are queued: while paused they are paused
        on arrival (not active work for the GPU checks)."""
        if self._paused:
            await self._pause_queue()
        self._cache = None
        self._notify()

    async def owner_pause(self) -> int:
        try:
            await self._registry.command(self.id, "pause")
            return self.last_count
        except CommandRefused:
            return await self._pause_queue()

    async def owner_resume(self) -> int:
        try:
            try:
                await self._registry.command(self.id, "resume")
            except CommandRefused:
                # A pause made some other way (or already resumed): play.
                await self._registry.command(self.id, "start")
            return self.last_count
        except CommandRefused:
            return await self._resume_queue()

    async def aclose(self) -> None:
        return None  # the queue's own shutdown ends the workers

    def poke(self) -> None:
        self._cache = None
        self._notify()

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("upscale notify failed")
