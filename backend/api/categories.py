"""REST endpoints for user-defined taste categories."""

from typing import Any, Dict, List, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db
from backend.services.categories_service import CategoriesService
from backend.ws.manager import ws_manager

router = APIRouter(prefix="/api", tags=["categories"])

_NAME_CODES = {"invalid_name": 422, "duplicate_name": 409}


def _get_service() -> CategoriesService:
    return CategoriesService(get_db())


class NameRequest(BaseModel):
    name: str


class AddItemsRequest(BaseModel):
    file_paths: List[str]
    # generation / sticky are server-internal; anything else fails validation.
    source: Literal["viewer", "bulk"]


class RemoveItemsRequest(BaseModel):
    file_paths: List[str]


def _changed() -> None:
    ws_manager.broadcast_sync("categories", "categories_changed", {})


def _http(exc: ValueError, codes: Dict[str, int]) -> HTTPException:
    code = str(exc)
    if code not in codes:
        raise exc
    return HTTPException(status_code=codes[code], detail={"code": code})


def _unknown() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "unknown_category"})


@router.get("/categories")
async def list_categories(
    service: CategoriesService = Depends(_get_service),
) -> List[Dict[str, Any]]:
    return await service.list_categories()


@router.post("/categories")
async def create_category(
    body: NameRequest, service: CategoriesService = Depends(_get_service)
) -> Dict[str, Any]:
    try:
        ref = await service.create_category(body.name)
    except ValueError as exc:
        raise _http(exc, _NAME_CODES)
    _changed()
    return {"id": ref.id, "name": ref.name}


@router.patch("/categories/{category_id}")
async def rename_category(
    category_id: int,
    body: NameRequest,
    service: CategoriesService = Depends(_get_service),
) -> Dict[str, Any]:
    try:
        ref = await service.rename_category(category_id, body.name)
    except ValueError as exc:
        raise _http(exc, _NAME_CODES)
    if ref is None:
        raise _unknown()
    _changed()
    return {"id": ref.id, "name": ref.name}


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int, service: CategoriesService = Depends(_get_service)
) -> Dict[str, str]:
    if not await service.delete_category(category_id):
        raise _unknown()
    _changed()
    return {"status": "deleted"}


@router.post("/categories/{category_id}/items")
async def add_items(
    category_id: int,
    body: AddItemsRequest,
    service: CategoriesService = Depends(_get_service),
) -> Dict[str, int]:
    try:
        added = await service.add_items(category_id, body.file_paths, body.source)
    except ValueError as exc:
        raise _http(exc, {"unknown_category": 404})
    if added > 0:
        _changed()
    return {"added": added}


@router.delete("/categories/{category_id}/items")
async def remove_items(
    category_id: int,
    body: RemoveItemsRequest,
    service: CategoriesService = Depends(_get_service),
) -> Dict[str, Any]:
    removed = await service.remove_items(category_id, body.file_paths)
    if removed > 0:
        _changed()
    return {"status": "deleted", "removed": removed}
