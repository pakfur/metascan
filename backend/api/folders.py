"""REST endpoints for folders + smart folders."""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.dependencies import get_db
from backend.services.folders_service import FoldersService
from backend.ws.manager import ws_manager

router = APIRouter(prefix="/api/folders", tags=["folders"])


def _get_service() -> FoldersService:
    return FoldersService(get_db())


class CreateFolderRequest(BaseModel):
    id: str = Field(..., min_length=1)
    kind: Literal["manual", "smart"]
    name: str = Field(..., min_length=1)
    icon: Optional[str] = "pi-folder"
    rules: Optional[Dict[str, Any]] = None
    items: Optional[List[str]] = None
    sort_order: Optional[int] = 0


class PatchFolderRequest(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    rules: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class ItemsRequest(BaseModel):
    paths: List[str]


def _broadcast(event: str, payload: Dict[str, Any]) -> None:
    """Fire a folders-channel WS broadcast from a sync or async context."""
    ws_manager.broadcast_sync("folders", event, payload)


@router.get("")
async def list_folders(
    service: FoldersService = Depends(_get_service),
) -> List[Dict[str, Any]]:
    return await service.list_folders()


@router.post("", status_code=201)
async def create_folder(
    body: CreateFolderRequest,
    service: FoldersService = Depends(_get_service),
) -> Dict[str, Any]:
    if body.kind == "smart" and body.items:
        raise HTTPException(
            status_code=422,
            detail="Smart folders don't accept an items list — set rules instead.",
        )
    record = await service.create_folder(
        folder_id=body.id,
        kind=body.kind,
        name=body.name,
        icon=body.icon or "pi-folder",
        rules=body.rules,
        items=body.items,
        sort_order=body.sort_order or 0,
    )
    if record is None:
        # Most likely a duplicate id — surfaced as 409 so the client can
        # distinguish from a validation error.
        raise HTTPException(
            status_code=409,
            detail=f"Folder with id {body.id!r} already exists",
        )
    _broadcast("folder_created", {"folder": record})
    return record


@router.patch("/{folder_id}")
async def update_folder(
    folder_id: str,
    body: PatchFolderRequest,
    service: FoldersService = Depends(_get_service),
) -> Dict[str, Any]:
    existing = await service.get_folder(folder_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    if body.rules is not None and existing["kind"] != "smart":
        raise HTTPException(
            status_code=422,
            detail="Cannot set rules on a manual folder.",
        )
    record = await service.update_folder(
        folder_id,
        name=body.name,
        icon=body.icon,
        rules=body.rules,
        sort_order=body.sort_order,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    _broadcast("folder_updated", {"folder": record})
    return record


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: str,
    service: FoldersService = Depends(_get_service),
) -> Dict[str, str]:
    ok = await service.delete_folder(folder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Folder not found")
    _broadcast("folder_deleted", {"id": folder_id})
    return {"status": "deleted"}


@router.post("/{folder_id}/items")
async def add_items(
    folder_id: str,
    body: ItemsRequest,
    service: FoldersService = Depends(_get_service),
) -> Dict[str, int]:
    added = await service.add_folder_items(folder_id, body.paths)
    if added is None:
        # Either the folder doesn't exist, or it's a smart folder (rules-
        # based) which can't have explicit items. The two failure modes
        # map to different HTTP codes.
        existing = await service.get_folder(folder_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Folder not found")
        raise HTTPException(
            status_code=422,
            detail="Cannot add items to a smart folder; edit its rules instead.",
        )
    if added > 0:
        _broadcast(
            "folder_items_changed",
            {"folder_id": folder_id, "added": body.paths, "removed": []},
        )
    return {"added": added}


@router.delete("/{folder_id}/items")
async def remove_items(
    folder_id: str,
    body: ItemsRequest,
    service: FoldersService = Depends(_get_service),
) -> Dict[str, int]:
    removed = await service.remove_folder_items(folder_id, body.paths)
    if removed is None:
        existing = await service.get_folder(folder_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Folder not found")
        raise HTTPException(
            status_code=422,
            detail="Cannot remove items from a smart folder.",
        )
    if removed > 0:
        _broadcast(
            "folder_items_changed",
            {"folder_id": folder_id, "added": [], "removed": body.paths},
        )
    return {"removed": removed}
