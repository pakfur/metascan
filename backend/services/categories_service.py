"""Async wrappers around DatabaseManager's category methods."""

import asyncio
from typing import Any, Dict, List, Optional

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import CategoryRef


class CategoriesService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def list_categories(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_categories)

    async def create_category(self, name: str) -> CategoryRef:
        return await asyncio.to_thread(self.db.create_category, name)

    async def rename_category(
        self, category_id: int, name: str
    ) -> Optional[CategoryRef]:
        return await asyncio.to_thread(self.db.rename_category, category_id, name)

    async def delete_category(self, category_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_category, category_id)

    async def add_items(
        self, category_id: int, file_paths: List[str], source: str
    ) -> int:
        return await asyncio.to_thread(
            self.db.add_media_categories, file_paths, category_id, source
        )

    async def remove_items(self, category_id: int, file_paths: List[str]) -> int:
        return await asyncio.to_thread(
            self.db.remove_media_categories, file_paths, category_id
        )
