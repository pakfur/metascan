"""Async wrappers around the i2v DB methods + file trashing."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from metascan.utils.trash import remove_files_to_trash


class I2vService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_videos(self, source_path: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_i2v_videos, source_path)

    async def delete_video(self, video_id: int) -> bool:
        deleted, purged = await asyncio.to_thread(self.db.delete_i2v_video, video_id)
        if deleted and purged:
            await asyncio.to_thread(remove_files_to_trash, purged)
        return deleted
