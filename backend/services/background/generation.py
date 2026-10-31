"""The T2I and I2V processes: batches behind the registry (spec 6.2, 6.3, 7).

Pause is a gate, not a cancel. ``ComfyClient.set_source_paused`` keeps the
source's queued jobs in the client's queue (order kept, other sources still
dispatch), the runner's prompting loop blocks in ``wait_unpaused`` between
steps, and nothing already in ComfyUI is interrupted. The process says
``running`` with "Pausing after the current job" (``winding_down``, so it
keeps its holds on Tagging and Embedding) until no job of its source is in
ComfyUI and no prompt step is mid-call to the writer; only then does the
registry show ``paused``. A paused Random batch keeps its writer hold
(``VlmClient.hold``) and so keeps the writer resident; that is deliberate.

Stop cancels every active batch through the runner's own ``cancel_batch``
(the route's logic). Nothing here is persisted: the registry re-applies a
persisted owner pause through ``pause()`` on register.

Everything runs on the server loop except ``report`` and ``poke``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry

logger = logging.getLogger(__name__)

WIND_DOWN_POLL_S = 0.25  # while a job of a paused source finishes


class _GenerationProcess:
    id = ""
    label = ""
    order = 0
    holds: Tuple[str, ...] = ("tagging", "embedding")
    noun = ""  # "Image" / "Video"
    unit = ""  # "images" / "videos"

    def __init__(
        self,
        registry: ProcessRegistry,
        runner: Any,
        comfy: Any,
        *,
        wind_down_poll_s: float = WIND_DOWN_POLL_S,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._comfy = comfy
        self._wind_down_poll_s = wind_down_poll_s
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._paused = False
        self._resuming = False  # unloading the VLMs before the gate opens
        self._gate = asyncio.Event()
        self._gate.set()
        self._watcher: Optional["asyncio.Task[None]"] = None
        self._closed = False
        runner.wait_unpaused = self.wait_unpaused

    # to be provided by T2iProcess / I2vProcess ----------------------------
    def _batch_ids(self) -> List[str]:
        raise NotImplementedError

    def _counts(self) -> Optional[Tuple[int, int, str]]:
        """(ended, total, phase word) over the active batches; None when none."""
        raise NotImplementedError

    # protocol ---------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=True, can_pause=True, can_stop=True)

    def report(self) -> ProcessReport:
        counts = self._counts()
        progress = None
        if counts is not None and counts[1]:
            progress = Progress(done=counts[0], total=counts[1], unit=self.unit)
        if self._resuming:
            # Holds are back on while the VLMs unload, before any job dispatches.
            return ProcessReport(State.RUNNING, progress, "Resuming", winding_down=True)
        if self._paused:
            if self._in_flight():
                return ProcessReport(
                    State.RUNNING,
                    progress,
                    "Pausing after the current job",
                    winding_down=True,
                )
            return ProcessReport(State.PAUSED, progress, "Paused")
        if counts is None:
            return ProcessReport(State.IDLE, None, "No batches")
        ended, total, phase = counts
        text = f"{self.noun} {min(ended + 1, total)} of {total}, {phase}"
        # Winding down is harmless while nothing suspends the process, and it
        # keeps the holds from dropping in the gap before pause() is reached.
        return ProcessReport(
            State.RUNNING, progress, text, winding_down=self._in_flight()
        )

    async def start(self) -> None:
        # A stop cancelled the batches; there is nothing to restart. The owner
        # starting a batch clears the stop first (owner_batch_starting).
        return None

    async def pause(self) -> None:
        self._paused = True
        self._gate.clear()
        self._comfy.set_source_paused(self.id, True)
        self._watch()
        self._notify()

    async def resume(self) -> None:
        """Hold Tagging and Embedding again, unload the VLMs a pause let
        Tagging reload (only when jobs are waiting behind the gate), then open
        the gate. A new submission never waits on this: it only queues."""
        self._resuming = True
        self._notify()
        try:
            if self._comfy.has_gated_jobs(self.id):
                try:
                    await self._runner.unload_vlms()
                except Exception:
                    logger.warning("%s: VLM unload before resume failed", self.id)
            self._open_gate()
        finally:
            self._resuming = False
            self._notify()

    async def stop(self) -> None:
        self._open_gate()  # whatever is left must be free to be cancelled
        for batch_id in self._batch_ids():
            try:
                await self._runner.cancel_batch(batch_id)
            except Exception as exc:
                # Over already, or unknown: nothing left to stop.
                logger.debug("%s stop: %s on %s", self.id, type(exc).__name__, batch_id)
        self._watch()
        self._notify()

    # entry points -------------------------------------------------------------
    async def wait_unpaused(self) -> None:
        """Awaited by the runner between prompt steps."""
        await self._gate.wait()

    async def owner_batch_starting(self) -> None:
        """The owner is starting a batch: that clears a stop. A paused process
        accepts it and queues it behind the gate, still paused."""
        try:
            if self._registry.status(self.id).state == State.STOPPED:
                await self._registry.command(self.id, "start")
        except CommandRefused as exc:
            logger.debug("%s owner start refused: %s", self.id, exc.code)

    def poke(self) -> None:
        """A batch or job event: re-evaluate the registry. Any thread."""
        self._notify()

    async def aclose(self) -> None:
        self._closed = True
        task = self._watcher
        self._watcher = None
        if task is not None and not task.done():
            task.cancel()
        self._gate.set()

    # internals ---------------------------------------------------------------
    def _open_gate(self) -> None:
        self._paused = False
        self._gate.set()
        self._comfy.set_source_paused(self.id, False)

    def _in_flight(self) -> bool:
        try:
            return bool(
                self._comfy.source_in_flight(self.id) > 0 or self._runner.is_writing()
            )
        except Exception:
            logger.debug("%s in-flight probe failed", self.id, exc_info=True)
            return False

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("%s notify failed", self.id)

    def _watch(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            return
        if self._watcher is None or self._watcher.done():
            self._watcher = loop.create_task(self._wind_down())

    async def _wind_down(self) -> None:
        """Tell the registry when the last job of the source has left ComfyUI."""
        while self._in_flight() and not self._closed:
            await asyncio.sleep(self._wind_down_poll_s)
        self._notify()


class T2iProcess(_GenerationProcess):
    id = "t2i"
    label = "Text to image"
    order = 40
    noun = "Image"
    unit = "images"

    def _rows(self) -> List[Dict[str, Any]]:
        return list(self._runner.active_batches())

    def _batch_ids(self) -> List[str]:
        return [str(r["batch_id"]) for r in self._rows()]

    def _counts(self) -> Optional[Tuple[int, int, str]]:
        rows = self._rows()
        if not rows:
            return None
        ended = sum(int(r["images_done"]) + int(r["images_failed"]) for r in rows)
        total = sum(int(r["images_total"]) for r in rows)
        phase = (
            "writing prompts"
            if any(r["state"] == "prompting" for r in rows)
            else "rendering"
        )
        return ended, total, phase


class I2vProcess(_GenerationProcess):
    id = "i2v"
    label = "Image to video"
    order = 50
    noun = "Video"
    unit = "videos"

    def _batch_ids(self) -> List[str]:
        return [str(r["batch_id"]) for r in self._runner.active_batches()]

    def _counts(self) -> Optional[Tuple[int, int, str]]:
        if not self._runner.active_batches():
            return None
        ended, total, phase = self._runner.progress_counts()
        return ended, total, "writing prompts" if phase == "writing" else phase
