"""Service layer wrapping DatabaseManager for async FastAPI access."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from send2trash import send2trash

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

logger = logging.getLogger(__name__)


class MediaService:
    def __init__(self, db: DatabaseManager, thumbnail_cache: ThumbnailCache) -> None:
        self.db = db
        self.thumbnail_cache = thumbnail_cache

    async def get_all_media(self) -> List[Media]:
        return await asyncio.to_thread(self.db.get_all_media_with_details)

    async def get_media(self, file_path: str) -> Optional[Media]:
        return await asyncio.to_thread(self.db.get_media_with_details, Path(file_path))

    async def delete_media(self, file_path: str) -> bool:
        """Delete a media file by moving it to trash and removing from DB."""
        path = Path(file_path)
        if path.exists():
            await asyncio.to_thread(send2trash, str(path))
        return await asyncio.to_thread(self.db.delete_media, path)

    async def set_favorite(self, file_path: str, is_favorite: bool) -> bool:
        return await asyncio.to_thread(
            self.db.set_favorite, Path(file_path), is_favorite
        )

    async def toggle_favorite(self, file_path: str) -> bool:
        return await asyncio.to_thread(self.db.toggle_favorite, Path(file_path))

    async def update_playback_speed(self, file_path: str, speed: float) -> bool:
        return await asyncio.to_thread(
            self.db.update_playback_speed, Path(file_path), speed
        )

    async def get_filter_data(self) -> Dict[str, List[Dict[str, Any]]]:
        return await asyncio.to_thread(self.db.get_filter_data)

    async def get_filtered_media_paths(self, filters: Dict[str, List[str]]) -> Set[str]:
        return await asyncio.to_thread(self.db.get_filtered_media_paths, filters)

    async def get_favorite_paths(self) -> Set[str]:
        return await asyncio.to_thread(self.db.get_favorite_media_paths)

    async def get_thumbnail_path(self, file_path: str) -> Optional[Path]:
        return await asyncio.to_thread(
            self.thumbnail_cache.get_or_create_thumbnail, Path(file_path)
        )

    async def get_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.db.get_stats)

    async def get_embedding_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.db.get_embedding_stats)

    def media_to_dict(self, media: Media) -> dict:
        """Convert a Media object to a JSON-serializable dict for the API."""
        return {
            "file_path": str(media.file_path),
            "file_name": media.file_name,
            "file_size": media.file_size,
            "width": media.width,
            "height": media.height,
            "format": media.format,
            "created_at": media.created_at.isoformat() if media.created_at else None,
            "modified_at": media.modified_at.isoformat() if media.modified_at else None,
            "metadata_source": media.metadata_source,
            "prompt": media.prompt,
            "negative_prompt": media.negative_prompt,
            "model": media.model,
            "sampler": media.sampler,
            "scheduler": media.scheduler,
            "steps": media.steps,
            "cfg_scale": media.cfg_scale,
            "seed": media.seed,
            "frame_rate": media.frame_rate,
            "duration": media.duration,
            "video_length": media.video_length,
            "tags": media.tags,
            "loras": [
                {"lora_name": lora.lora_name, "lora_weight": lora.lora_weight}
                for lora in media.loras
            ],
            "is_favorite": media.is_favorite,
            "is_video": media.is_video,
            "media_type": media.media_type,
            "playback_speed": media.playback_speed,
        }
