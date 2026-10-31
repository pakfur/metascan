"""The Hygiene process: a placeholder panel until its real work lands."""

from __future__ import annotations

from typing import Tuple

from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)


class HygieneProcess:
    id = "hygiene"
    label = "Hygiene"
    order = 80
    holds: Tuple[str, ...] = ()

    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=False, can_pause=False, can_stop=False)

    def report(self) -> ProcessReport:
        return ProcessReport(State.IDLE, None, "Not running yet")

    async def start(self) -> None:
        return None

    async def pause(self) -> None:
        return None

    async def resume(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def aclose(self) -> None:
        return None
