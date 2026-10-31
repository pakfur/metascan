"""The Embedding process: index builds behind the registry (spec 6.2, 6.3, 7).

Pause and hold use the existing cancel path: the worker saves and exits, and
``has_embedding`` keeps the remainder durable. The queue keeps indexing until
the worker *process* has exited (CLAUDE.md, Tombstone vectors), so the panel
says ``running`` with "Saving, then pausing" until ``is_indexing`` is false,
and only then ``paused`` / ``held``. Resume starts an incremental build, which
costs a CLIP reload; a rebuild that was cut short resumes as an incremental
one (the rows it had not reached keep ``has_embedding = 0``).

Everything runs on the server loop except ``report`` and ``record_*``, which
the embedding poller's worker thread may call."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from fastapi import HTTPException

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry
from metascan.core.gpu_guard import gpu_reserved

logger = logging.getLogger(__name__)

RETRY_S = 30.0  # while waiting for the GPU
WIND_DOWN_POLL_S = 0.25  # while a cancelled worker is still exiting

# (rebuild, allow_vlm_tagging)
BuildRequest = Tuple[bool, bool]


class EmbeddingProcess:
    id = "embedding"
    label = "Embedding"
    order = 20
    holds: Tuple[str, ...] = ("tagging",)

    def __init__(
        self,
        registry: ProcessRegistry,
        get_queue: Callable[[], Any],
        get_db: Callable[[], Any],
        start_build: Callable[[bool, bool], Awaitable[Dict[str, Any]]],
        get_progress: Callable[[], Dict[str, Any]],
        *,
        retry_s: float = RETRY_S,
        wind_down_poll_s: float = WIND_DOWN_POLL_S,
    ) -> None:
        self._registry = registry
        self._get_queue = get_queue
        self._get_db = get_db
        self._start_build = start_build
        self._get_progress = get_progress
        self._retry_s = retry_s
        self._wind_down_poll_s = wind_down_poll_s
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._suspended = False  # told to pause (by the owner or a hold)
        self._stopping = False
        self._error: Optional[str] = None
        self._waiting = False  # the GPU is reserved
        self._current_vlm = False  # allow_vlm_tagging of the build in flight
        self._interrupted: Optional[bool] = None  # allow_vlm of a paused/held run
        self._queued: Optional[BuildRequest] = None  # an owner build met a hold
        self._owner_start = False  # the owner's build is clearing a stop
        self._watcher: Optional[asyncio.Task] = None
        self._retry_handle: Optional[asyncio.TimerHandle] = None
        self._closed = False

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=True, can_pause=True, can_stop=True)

    def report(self) -> ProcessReport:
        indexing = self._indexing()
        if indexing:
            # Always winding_down: a pause, hold or stop that has not reached
            # pause()/stop() yet must not drop the process's holds early.
            p = self._progress()
            if self._stopping:
                text = "Saving, then stopping"
            elif self._suspended:
                text = "Saving, then pausing"
            else:
                text = f"Embedding {p.done} of {p.total} files" if p else "Embedding"
            return ProcessReport(State.RUNNING, p, text, winding_down=True)
        if self._error:
            return ProcessReport(State.FAILED, None, "Embedding failed", self._error)
        if self._waiting:
            return ProcessReport(State.WAITING, None, "Waiting: GPU in use")
        return ProcessReport(State.IDLE, None, "Nothing to embed")

    async def start(self) -> None:
        # The owner's own build clears a stop and runs itself (request_build).
        owner_build, self._owner_start = self._owner_start, False
        self._suspended = False
        self._stopping = False
        if owner_build:
            return
        await self._run_build(False, True)

    async def pause(self) -> None:
        """Cancel the worker (it saves, then exits) and remember what to resume."""
        self._suspended = True
        self._cancel_retry()
        if self._indexing():
            self._interrupted = self._current_vlm
            await asyncio.to_thread(self._get_queue().cancel_indexing)
            self._watch()
        self._notify()

    async def resume(self) -> None:
        self._suspended = False
        if self._indexing():
            # Still exiting: the watcher starts the resumed build once it has.
            self._watch()
            self._notify()
            return
        await self._resume_work()
        self._notify()

    async def stop(self) -> None:
        self._stopping = True
        self._interrupted = None
        self._queued = None
        self._waiting = False
        self._cancel_retry()
        if self._indexing():
            await asyncio.to_thread(self._get_queue().cancel_indexing)
            self._watch()
        self._notify()

    # entry points ------------------------------------------------------
    async def kick(self, allow_vlm_tagging: bool) -> Dict[str, Any]:
        """A build the server itself wants (auto index, a scan's follow-up).
        Does nothing while Embedding is held, paused or stopped."""
        return await self.request_build(False, allow_vlm_tagging, owner=False)

    async def request_build(
        self, rebuild: bool, allow_vlm_tagging: bool, *, owner: bool
    ) -> Dict[str, Any]:
        state = self._registry.status(self.id).state
        if state == State.STOPPED:
            if not owner:
                return {"status": "blocked", "reason": "stopped"}
            # The owner's own action clears the stop.
            self._owner_start = True
            await self._registry.command(self.id, "start")
        elif state == State.PAUSED:
            if not owner:
                return {"status": "blocked", "reason": "paused"}
            # The owner's build replaces the run that was paused.
            self._interrupted = None
            await self._registry.command(self.id, "resume")
        elif state == State.HELD:
            # Waits for the release, then runs as asked.
            self._queue(rebuild, allow_vlm_tagging)
            self._notify()
            return {"status": "queued"}
        return await self._run_build(rebuild, allow_vlm_tagging, raise_busy=owner)

    def record_error(self, message: str) -> None:
        self._error = message
        self._notify()

    def poke(self) -> None:
        """Progress, completion: re-evaluate the registry. Any thread."""
        self._notify()

    async def aclose(self) -> None:
        self._closed = True
        self._cancel_retry()
        task = self._watcher
        self._watcher = None
        if task is not None and not task.done():
            task.cancel()

    # internals ---------------------------------------------------------
    def _indexing(self) -> bool:
        try:
            return bool(self._get_queue().is_indexing())
        except Exception:
            logger.debug("embedding is_indexing probe failed", exc_info=True)
            return False

    def _progress(self) -> Optional[Progress]:
        raw = self._get_progress() or {}
        total = int(raw.get("total") or 0)
        if not total:
            return None
        return Progress(done=int(raw.get("current") or 0), total=total, unit="files")

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("embedding notify failed")

    async def _run_build(
        self, rebuild: bool, allow_vlm: bool, *, raise_busy: bool = False
    ) -> Dict[str, Any]:
        if self._closed:
            return {"status": "refused"}
        self._cancel_retry()
        if gpu_reserved():
            return self._wait_for_gpu(rebuild, allow_vlm)
        try:
            result = await self._start_build(rebuild, allow_vlm)
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict) and str(detail.get("code", "")).startswith(
                "gpu_busy"
            ):
                return self._wait_for_gpu(rebuild, allow_vlm)
            if raise_busy:
                raise
            logger.info("embedding build not started: %s", detail)
            return {"status": "refused"}
        self._waiting = False
        self._error = None
        self._current_vlm = allow_vlm
        self._notify()
        return result

    def _queue(self, rebuild: bool, allow_vlm: bool) -> None:
        """Keep the strongest queued request: a weaker one (Import's kick)
        must not overwrite the owner's rebuild or VLM tagging."""
        old = self._queued
        if old is not None:
            rebuild = old[0] or rebuild
            allow_vlm = old[1] and allow_vlm
        self._queued = (rebuild, allow_vlm)

    def _wait_for_gpu(self, rebuild: bool, allow_vlm: bool) -> Dict[str, Any]:
        """The GPU is reserved: not an error. Keep the request and try again."""
        self._waiting = True
        self._queue(rebuild, allow_vlm)
        self._arm_retry()
        self._notify()
        return {"status": "waiting"}

    async def _resume_work(self) -> None:
        """After a release or a resume: run what was asked for while held, else
        continue an interrupted run when files remain, else stay idle."""
        if self._closed:
            return
        queued, self._queued = self._queued, None
        if queued is not None:
            await self._run_build(*queued)
            return
        interrupted, self._interrupted = self._interrupted, None
        if interrupted is None:
            return
        try:
            remaining = await asyncio.to_thread(
                self._get_db().get_unembedded_file_paths
            )
        except Exception:
            logger.exception("could not count the unembedded files")
            return
        if remaining:
            await self._run_build(False, interrupted)

    def _watch(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            return
        if self._watcher is None or self._watcher.done():
            self._watcher = loop.create_task(self._wind_down())

    async def _wind_down(self) -> None:
        """Tell the registry when the cancelled worker has really exited, and
        start the resumed build if the release came meanwhile."""
        while self._indexing():
            await asyncio.sleep(self._wind_down_poll_s)
        self._notify()
        if not self._suspended and not self._closed:
            state = self._registry.status(self.id).state
            if state not in (State.STOPPED, State.PAUSED, State.HELD):
                await self._resume_work()
                self._notify()

    def _arm_retry(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            return
        self._retry_handle = loop.call_later(self._retry_s, self._on_retry)

    def _on_retry(self) -> None:
        self._retry_handle = None
        if self._closed or self._suspended:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.create_task(self._retry())

    async def _retry(self) -> None:
        if self._registry.blocked(self.id) or not self._waiting:
            return
        await self._resume_work()

    def _cancel_retry(self) -> None:
        handle = self._retry_handle
        self._retry_handle = None
        if handle is not None:
            handle.cancel()
