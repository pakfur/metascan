"""The Import process: today's library scan behind the registry (spec 6.2, 9.4).

This stage only makes the scan visible and stoppable; the scan route still owns
the work. Start and pause are not offered. Stop is the ``/cancel`` path
(``scan.request_cancel``), so a Full clean stopped here is a cancelled one: the
marker and the pending file stay and nothing is tombstoned. Until the task has
ended the process reports ``winding_down``.

The activity line carries the phase, counts and the bare file name, never a
path. A failure shows the exception's type only."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._registry = registry
        self._status = status
        self._cancel = cancel

    def capabilities(self) -> Capabilities:
        st = self._status()
        running = bool(st["running"])
        return Capabilities(
            can_start=False,
            can_pause=False,
            can_stop=running,
            confirm_stop=running and bool(st["full_clean"]),
        )

    def report(self) -> ProcessReport:
        st = self._status()
        if not st["running"]:
            if st["error"]:
                return ProcessReport(
                    State.FAILED, None, "Scan failed", error=st["error"]
                )
            return ProcessReport(State.IDLE, None, "Not running")
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

    # The route owns the work; the owner cannot start a scan from the panel.
    async def start(self) -> None:
        return None

    async def pause(self) -> None:
        return None

    async def resume(self) -> None:
        return None

    async def stop(self) -> None:
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
            self._registry.notify(self.id)
        except Exception:
            logger.exception("import notify failed")

    async def aclose(self) -> None:
        return None
