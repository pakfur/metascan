"""REST endpoints for prompt-writer adapters (taste-learning fine-tunes)."""

import asyncio
import logging
import shutil
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db
from backend.ws.manager import ws_manager
from metascan.core.writer_adapters import (
    STATUS_TRAINING,
    adapter_dir,
    adapter_writer_id,
)
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writers", tags=["writers"])

_NAME_CODES = {"invalid_name": 422, "duplicate_name": 409}


class RenameRequest(BaseModel):
    name: str


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {**row, "writer_id": adapter_writer_id(row["id"])}


def _live(adapter_id: int) -> Dict[str, Any]:
    row = get_db().get_writer_adapter(adapter_id)
    if row is None or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail={"code": "unknown_adapter"})
    return row


def _changed() -> None:
    ws_manager.broadcast_sync("training", "adapters_changed", {})


@router.get("/adapters")
async def list_adapters() -> List[Dict[str, Any]]:
    rows = await asyncio.to_thread(get_db().list_writer_adapters)
    return [_public(r) for r in rows]


def _rename(adapter_id: int, name: str) -> Dict[str, Any]:
    _live(adapter_id)
    try:
        get_db().rename_writer_adapter(adapter_id, name)
    except ValueError as exc:
        code = str(exc)
        if code not in _NAME_CODES:
            raise
        raise HTTPException(
            status_code=_NAME_CODES[code], detail={"code": code}
        ) from exc
    return _public(_live(adapter_id))


@router.patch("/adapters/{adapter_id}")
async def rename_adapter(adapter_id: int, body: RenameRequest) -> Dict[str, Any]:
    row = await asyncio.to_thread(_rename, adapter_id, body.name)
    _changed()
    return row


def _delete(adapter_id: int) -> None:
    row = _live(adapter_id)
    if row["status"] == STATUS_TRAINING:
        raise HTTPException(status_code=409, detail={"code": "adapter_training"})
    folder = adapter_dir(get_data_dir(), adapter_id)
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except OSError as exc:
        # Keep the row and its file_path so the delete can be retried.
        logger.warning("could not remove adapter %s files: %s", adapter_id, exc)
        raise HTTPException(
            status_code=409, detail={"code": "adapter_files_busy"}
        ) from exc
    get_db().soft_delete_writer_adapter(adapter_id)


@router.delete("/adapters/{adapter_id}")
async def delete_adapter(adapter_id: int) -> Dict[str, str]:
    await asyncio.to_thread(_delete, adapter_id)
    _changed()
    return {"status": "deleted"}
