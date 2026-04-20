"""Service layer for folders/smart-folders persistence.

Thin asyncio wrapper around DatabaseManager's folder CRUD; matches the
pattern used by MediaService so FastAPI handlers stay uniform.
"""

import asyncio
from typing import Any, Dict, List, Optional

from metascan.core.database_sqlite import DatabaseManager


class FoldersService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def list_folders(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_folders)

    async def get_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_folder, folder_id)

    async def create_folder(
        self,
        folder_id: str,
        kind: str,
        name: str,
        icon: str = "pi-folder",
        rules: Optional[Dict[str, Any]] = None,
        items: Optional[List[str]] = None,
        sort_order: int = 0,
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.db.create_folder,
            folder_id,
            kind,
            name,
            icon,
            rules,
            items,
            sort_order,
        )

    async def update_folder(
        self,
        folder_id: str,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        rules: Optional[Dict[str, Any]] = None,
        sort_order: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.db.update_folder,
            folder_id,
            name,
            icon,
            rules,
            sort_order,
        )

    async def delete_folder(self, folder_id: str) -> bool:
        return await asyncio.to_thread(self.db.delete_folder, folder_id)

    async def add_folder_items(self, folder_id: str, paths: List[str]) -> Optional[int]:
        return await asyncio.to_thread(self.db.add_folder_items, folder_id, paths)

    async def remove_folder_items(
        self, folder_id: str, paths: List[str]
    ) -> Optional[int]:
        return await asyncio.to_thread(self.db.remove_folder_items, folder_id, paths)
