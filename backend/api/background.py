"""REST transport for the background-process registry (spec 5.5)."""

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_registry
from backend.services.background.protocol import ProcessStatus
from backend.services.background.registry import Action, CommandRefused, ProcessRegistry
from backend.ws.manager import ws_manager

router = APIRouter(prefix="/api/background", tags=["background"])


def publish_status(status: ProcessStatus) -> None:
    """Registry notify callback: one ``process_updated`` event per status."""
    ws_manager.broadcast_sync("background", "process_updated", status.to_dict())


@router.get("")
async def list_processes(
    registry: ProcessRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    return {
        "server_time": time.time(),
        "processes": [s.to_dict() for s in registry.statuses()],
    }


@router.post("/{process_id}/{action}")
async def command(
    process_id: str,
    action: Action,
    registry: ProcessRegistry = Depends(get_registry),
) -> Dict[str, Any]:
    try:
        status = await registry.command(process_id, action)
    except CommandRefused as exc:
        if exc.code == "unknown_process":
            raise HTTPException(status_code=404, detail={"code": exc.code})
        raise HTTPException(status_code=409, detail={"code": exc.code})
    return status.to_dict()
