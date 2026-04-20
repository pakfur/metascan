"""Filter data and application endpoints."""

from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService

router = APIRouter(prefix="/api", tags=["filters"])


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


class FilterRequest(BaseModel):
    filters: Dict[str, List[str]]


@router.get("/filters")
async def get_filters(service: MediaService = Depends(_get_service)):
    """Get all filter groups with counts (source, model, ext, tag, etc.)."""
    return await service.get_filter_data()


@router.post("/filters/apply")
async def apply_filters(
    body: FilterRequest,
    service: MediaService = Depends(_get_service),
):
    """Apply filters and return matching file paths."""
    paths = await service.get_filtered_media_paths(body.filters)
    return {"paths": list(paths)}


class TagPathsRequest(BaseModel):
    keys: List[str]


@router.post("/filters/tag_paths")
async def get_tag_paths(
    body: TagPathsRequest,
    service: MediaService = Depends(_get_service),
):
    """Return ``{tag_key: [file_path, ...]}`` for the requested tag keys.

    Callers pass only the keys their smart folders actually reference so we
    don't serialize the whole (potentially multi-megabyte) inverted index on
    every refresh.
    """
    return await service.get_tag_path_index(body.keys)
