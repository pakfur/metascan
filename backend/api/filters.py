"""Filter data and application endpoints."""

import logging
import time
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["filters"])


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


class FilterRequest(BaseModel):
    filters: Dict[str, List[str]]


@router.get("/filters")
async def get_filters(service: MediaService = Depends(_get_service)):
    """Get all filter groups with counts (source, model, ext, tag, etc.)."""
    t_start = time.perf_counter()
    try:
        return await service.get_filter_data()
    finally:
        logger.info(
            "GET /api/filters: total=%.1fms",
            (time.perf_counter() - t_start) * 1000,
        )


@router.post("/filters/apply")
async def apply_filters(
    body: FilterRequest,
    service: MediaService = Depends(_get_service),
):
    """Apply filters and return matching file paths."""
    t_start = time.perf_counter()
    try:
        paths = await service.get_filtered_media_paths(body.filters)
        return {"paths": list(paths)}
    finally:
        logger.info(
            "POST /api/filters/apply: total=%.1fms groups=%d",
            (time.perf_counter() - t_start) * 1000,
            len(body.filters),
        )
