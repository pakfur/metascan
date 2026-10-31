"""Types of the background-process protocol (spec section 5)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Tuple


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    HELD = "held"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    unit: str


@dataclass(frozen=True)
class Capabilities:
    can_start: bool
    can_pause: bool
    can_stop: bool
    confirm_stop: bool = False


@dataclass(frozen=True)
class ProcessReport:
    """What a process says about itself; the registry layers intent and holds on top."""

    state: State
    progress: Optional[Progress]
    activity: str
    error: Optional[str] = None
    warnings_count: int = 0


@dataclass
class ProcessStatus:
    id: str
    label: str
    order: int
    state: State
    progress: Optional[Progress]
    activity: str
    held_by: List[str]
    error: Optional[str]
    warnings_count: int
    capabilities: Capabilities
    updated_at: float = field(default=0.0)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class BackgroundProcess(Protocol):
    id: str
    label: str
    order: int
    holds: Tuple[str, ...]  # process ids held while this one is running

    def capabilities(self) -> Capabilities: ...

    def report(self) -> ProcessReport: ...

    async def start(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def stop(self) -> None: ...
