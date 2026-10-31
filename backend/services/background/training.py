"""The Writer training process: training runs, hindsight briefs and the
scorecard (spec 6.2, 6.3, 7).

Pause, resume and cancel go through the manager's own methods, which keep
their lock and checkpoint rules. Pause lands at a step boundary, so while the
manager has a run the process says ``running`` with ``winding_down`` (it keeps
its holds on Tagging and Embedding until the worker has exited). A run paused
by a pause, a shutdown or a crash shows ``paused`` with no change to the
owner's intent. Briefs and scorecard show here when no run does. Stop cancels
the run (active or paused) if one exists, else the briefs job, else the
scorecard; it deletes checkpoints, so ``confirm_stop``.

``can_start`` is true only while a paused run can be resumed: play resumes it.
Starting a new run needs a form (scope, effort) and stays with the window. Play on a stopped panel clears the stop and stays idle.

Activity carries counts and stage words only, never examples or briefs. The
manager, the briefs job and the scorecard have no common event, so a
one-second poll compares a signature and notifies."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Tuple

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
_STAGE_WORDS = {"sft": "SFT", "dpo": "DPO", "convert": "Converting", "eval": "Eval"}


class WriterTrainingProcess:
    id = "training"
    label = "Writer training"
    order = 100
    holds: Tuple[str, ...] = ("tagging", "embedding")

    def __init__(
        self,
        registry: ProcessRegistry,
        get_manager: Callable[[], Any],
        get_db: Callable[[], Any],
        get_briefs: Callable[[], Any],
        get_scorecard: Callable[[], Any],
        *,
        poll_s: float = POLL_S,
    ) -> None:
        self._registry = registry
        self._get_manager = get_manager
        self._get_db = get_db
        self._get_briefs = get_briefs
        self._get_scorecard = get_scorecard
        self._poll_s = poll_s
        self._paused_run: Optional[Dict[str, Any]] = None  # newest paused run row
        self._error: Optional[str] = None
        self._sig: Any = None
        self._clearing = False  # an owner action is clearing a stop
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._task = loop.create_task(self._poll()) if loop is not None else None

    # reads -------------------------------------------------------------
    def _manager_status(self) -> Dict[str, Any]:
        try:
            return dict(self._get_manager().status())
        except Exception:
            return {"state": "idle", "run": None}

    def _briefs(self) -> Optional[Dict[str, Any]]:
        try:
            st = self._get_briefs().status()
        except Exception:
            return None
        return dict(st) if st.get("running") else None

    def _scorecard(self) -> Optional[Dict[str, Any]]:
        try:
            st = self._get_scorecard()
            return dict(st) if st else None
        except Exception:
            return None

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        # Play resumes a paused run, or clears a stop (and stays idle).
        can_start = (
            self._paused_run is not None or self._registry.intent(self.id) == "stopped"
        )
        # Briefs and the scorecard have no pause: refuse it rather than store
        # an intent that pauses nothing.
        only_side_jobs = self._manager_status().get("run") is None and (
            self._briefs() is not None or self._scorecard() is not None
        )
        return Capabilities(
            can_start=can_start,
            can_pause=not only_side_jobs,
            can_stop=True,
            confirm_stop=True,
        )

    def report(self) -> ProcessReport:
        st = self._manager_status()
        run = st.get("run")
        if run is not None:
            prog = run.get("progress") or {}
            step, total = int(prog.get("step") or 0), int(prog.get("total_steps") or 0)
            progress = Progress(step, total, "steps") if total else None
            state = str(st.get("state"))
            if state == "pausing":
                text = "Pausing at the next step"
            elif state == "cancelling":
                text = "Cancelling"
            elif state == "starting" or not total:
                text = "Starting"
            else:
                stage = _STAGE_WORDS.get(str(prog.get("stage") or ""), "Training")
                text = f"{stage} step {min(step + 1, total)} of {total}"
            # Always winding down: the worker is a process that exits at a
            # step boundary, and its holds must not drop before it has.
            return ProcessReport(State.RUNNING, progress, text, winding_down=True)
        briefs = self._briefs()
        if briefs is not None:
            done, total = int(briefs.get("done") or 0), int(briefs.get("total") or 0)
            progress = Progress(done, total, "briefs") if total else None
            return ProcessReport(
                State.RUNNING,
                progress,
                f"Writing briefs, {done} of {total}",
                winding_down=True,
            )
        card = self._scorecard()
        if card is not None:
            done, total = int(card.get("done") or 0), int(card.get("total") or 0)
            phase = str(card.get("phase") or "running")
            progress = Progress(done, total, "images") if total else None
            return ProcessReport(
                State.RUNNING,
                progress,
                f"Scorecard {phase}, {done} of {total}",
                winding_down=True,
            )
        if self._error:
            return ProcessReport(State.FAILED, None, "Run failed", self._error)
        paused = self._paused_run
        if paused is not None:
            step, total = int(paused.get("step") or 0), int(
                paused.get("total_steps") or 0
            )
            progress = Progress(step, total, "steps") if total else None
            text = f"Paused at step {step} of {total}" if total else "Paused"
            return ProcessReport(State.PAUSED, progress, text)
        return ProcessReport(State.IDLE, None, "No training run")

    async def start(self) -> None:
        """Play resumes the paused run (a new run needs the window's form)."""
        if self._clearing:
            return  # the owner's own start / resume runs it
        await self._resume_paused()

    async def pause(self) -> None:
        from backend.services.training_manager import TrainingError

        try:
            await self._get_manager().pause()
        except TrainingError as exc:
            # Idle (a race with the run ending): nothing to pause. Briefs and
            # the scorecard are refused earlier, by can_pause.
            if self._briefs() is not None or self._scorecard() is not None:
                raise
            logger.debug("training pause: %s", exc.code)
        self._notify()

    async def resume(self) -> None:
        await self._resume_paused()

    async def stop(self) -> None:
        from backend.services.training_manager import TrainingError

        manager = self._get_manager()
        try:
            if self._manager_status().get("run") is not None:
                await manager.cancel()
            else:
                await self.refresh()
                run = self._paused_run
                if run is not None:
                    await manager.cancel(int(run["id"]))
                elif self._briefs() is not None:
                    self._get_briefs().cancel()
                elif self._scorecard() is not None:
                    await self._get_scorecard_service().cancel()
        except TrainingError as exc:
            logger.info("training stop: %s", exc.code)
        await self.refresh()
        self._notify()

    def _get_scorecard_service(self) -> Any:
        from backend.services.scorecard import get_scorecard_service

        return get_scorecard_service()

    # entry points ------------------------------------------------------
    async def owner_run_starting(self) -> None:
        """The owner starts or resumes a run, or renders a scorecard: that
        clears a stop."""
        try:
            if self._registry.status(self.id).state == State.STOPPED:
                self._clearing = True
                try:
                    await self._registry.command(self.id, "start")
                finally:
                    self._clearing = False
        except CommandRefused as exc:
            logger.debug("training owner start refused: %s", exc.code)

    async def refresh(self) -> None:
        """Re-read the newest paused run (the database, off the loop)."""
        from backend.services.training_manager import RUN_PAUSED

        try:
            runs = await asyncio.to_thread(
                self._get_db().list_training_runs, [RUN_PAUSED]
            )
        except Exception:
            logger.debug("reading paused training runs failed", exc_info=True)
            return
        self._paused_run = dict(runs[-1]) if runs else None

    def record_event(self, event: str, data: Dict[str, Any]) -> None:
        """A ``training`` channel event: failures stick until the next run."""
        if event == "run_failed":
            self._error = str(data.get("error") or "Run failed")[:ERROR_MAX]
        elif event == "run_started":
            self._error = None
        self._notify()

    def poke(self) -> None:
        self._notify()

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # internals ---------------------------------------------------------
    async def _resume_paused(self) -> None:
        if self._manager_status().get("run") is not None:
            return
        await self.refresh()
        run = self._paused_run
        if run is None:
            return
        await self._get_manager().resume(int(run["id"]))
        await self.refresh()
        self._notify()

    def _signature(self) -> Any:
        st = self._manager_status()
        run = st.get("run") or {}
        prog = run.get("progress") or {}
        briefs = self._briefs() or {}
        card = self._scorecard() or {}
        return (
            st.get("state"),
            run.get("run_id"),
            prog.get("step"),
            prog.get("stage"),
            briefs.get("done"),
            briefs.get("total"),
            card.get("phase"),
            card.get("done"),
        )

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self._poll_s)
            sig = self._signature()
            if sig != self._sig:
                self._sig = sig
                await self.refresh()
                self._notify()

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("training notify failed")
