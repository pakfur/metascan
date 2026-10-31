"""The Taste process: the scorer's train and rescore, and the tombstone
re-embedder's pass (spec 6.2, 6.3, 7).

Pause and stop set ``TasteService.auto_paused``, which keeps the retrain loop
from starting anything; the registry persists the intent, so there is no
config for it. A run in flight finishes on pause ("Finishing this run, then
pausing"; ``winding_down``) and is cancelled on stop through the runner's
cancel path. The owner's ``POST /train`` works while paused and clears a
stop. The re-embedder keeps its own ``blocked`` checks and is not stopped by
a pause; its progress shows in the activity.

Activity carries counts and stage words only. The service has no event for
every change, so a one-second poll compares a signature and notifies."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry

logger = logging.getLogger(__name__)

POLL_S = 1.0
ERROR_MAX = 200


class TasteProcess:
    id = "taste"
    label = "Taste training"
    order = 90
    holds: Tuple[str, ...] = ()

    def __init__(
        self, registry: ProcessRegistry, service: Any, *, poll_s: float = POLL_S
    ) -> None:
        self._registry = registry
        self._service = service
        self._poll_s = poll_s
        # A persisted pause or stop applies before the loop's first tick.
        service.auto_paused = registry.persisted_intent(self.id) != "none"
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._task = loop.create_task(self._poll()) if loop is not None else None
        self._sig: Any = None
        self._clearing = False  # owner_train_starting is clearing a stop

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=True, can_pause=True, can_stop=True)

    def report(self) -> ProcessReport:
        svc = self._service
        paused = bool(svc.auto_paused)
        reembed = svc.reembed_progress()
        if svc.is_training or svc.is_rescoring:
            word = "Training the scorer" if svc.is_training else "Rescoring"
            if paused:
                word = "Finishing this run, then pausing"
            return ProcessReport(State.RUNNING, None, word, winding_down=paused)
        if reembed and reembed.get("running"):
            done, total = int(reembed.get("done") or 0), int(reembed.get("total") or 0)
            progress = Progress(done, total, "images") if total else None
            return ProcessReport(
                State.RUNNING,
                progress,
                f"Re-embedding {done} of {total} deleted images",
            )
        if svc.last_error:
            return ProcessReport(
                State.FAILED, None, "Training failed", svc.last_error[:ERROR_MAX]
            )
        return ProcessReport(State.IDLE, None, "Scorer up to date")

    async def start(self) -> None:
        """The owner's play: clears a stop and trains now. A refusal (too few
        grades, already training) is not an error of the process."""
        self._service.auto_paused = False
        if self._clearing:
            return  # the owner's own POST /train runs it
        from backend.services.taste_service import TrainConflict

        try:
            await self._service.start_training()
        except TrainConflict as exc:
            logger.info("taste train not started: %s", exc.code)
        self._notify()

    async def pause(self) -> None:
        self._service.auto_paused = True
        self._notify()

    async def resume(self) -> None:
        self._service.auto_paused = False
        self._notify()

    async def stop(self) -> None:
        self._service.auto_paused = True
        await self._service.cancel_training()
        self._notify()

    # entry points ------------------------------------------------------
    async def owner_train_starting(self) -> None:
        """The owner's POST /train clears a stop (not a pause: it just runs)."""
        try:
            if self._registry.status(self.id).state == State.STOPPED:
                self._clearing = True
                try:
                    await self._registry.command(self.id, "start")
                finally:
                    self._clearing = False
        except CommandRefused as exc:
            logger.debug("taste owner start refused: %s", exc.code)

    def poke(self) -> None:
        self._notify()

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # internals ---------------------------------------------------------
    def _signature(self) -> Any:
        svc = self._service
        re = svc.reembed_progress() or {}
        return (
            svc.is_training,
            svc.is_rescoring,
            svc.last_error,
            re.get("running"),
            re.get("done"),
            re.get("total"),
        )

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self._poll_s)
            try:
                sig = self._signature()
                if sig != self._sig:
                    self._sig = sig
                    self._notify()
            except Exception:
                logger.debug("taste poll failed", exc_info=True)

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("taste notify failed")
