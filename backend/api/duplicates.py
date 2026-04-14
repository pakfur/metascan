"""Duplicate detection endpoints."""

import asyncio
import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from send2trash import send2trash

from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


class DeleteRequest(BaseModel):
    file_paths: List[str]


@router.post("/find")
async def find_duplicates(service: MediaService = Depends(_get_service)):
    """Find duplicate groups using perceptual hashing."""
    db = get_db()
    all_hashes = await asyncio.to_thread(db.get_all_phashes)

    # Group by pHash (exact or near-match)
    from imagehash import hex_to_hash

    hash_groups: dict = {}
    for file_path, phash_hex in all_hashes.items():
        try:
            h = hex_to_hash(phash_hex)
            # Find an existing group within threshold
            found_group = None
            for group_hash, group_paths in hash_groups.items():
                if abs(h - group_hash) <= 10:  # pHash distance threshold
                    found_group = group_hash
                    break
            if found_group is not None:
                hash_groups[found_group].append(file_path)
            else:
                hash_groups[h] = [file_path]
        except Exception:
            continue

    # Filter to groups with 2+ items
    groups = []
    for _, paths in hash_groups.items():
        if len(paths) >= 2:
            group_media = []
            for fp in paths:
                media = await service.get_media(fp)
                if media:
                    group_media.append(service.media_to_dict(media))
            if len(group_media) >= 2:
                groups.append(group_media)

    return {"groups": groups, "total_groups": len(groups)}


@router.post("/delete")
async def delete_duplicates(
    body: DeleteRequest,
    service: MediaService = Depends(_get_service),
):
    """Batch delete selected duplicate files."""
    deleted = 0
    for fp in body.file_paths:
        try:
            path = Path(fp)
            if path.exists():
                await asyncio.to_thread(send2trash, str(path))
            db = get_db()
            await asyncio.to_thread(db.delete_media, path)
            deleted += 1
        except Exception as e:
            logger.error(f"Failed to delete {fp}: {e}")
    return {"deleted": deleted}
