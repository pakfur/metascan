"""Async wrappers over the storyboard CRUD DB methods.

DatabaseManager is synchronous and guarded by a threading.Lock; route
handlers must never call it directly on the event loop. Every method here
is a thin asyncio.to_thread hop, matching backend/services/comfy_service.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


class ParentNotFoundError(RuntimeError):
    """A create call referenced a parent row that doesn't exist.

    storyboard_subjects / scenes reference storyboards(id) and panels
    reference scenes(id), all NOT NULL. Rather than let a bad parent id
    surface as a raw sqlite3.IntegrityError (or a 500), the service
    pre-checks the parent and raises this so the route can turn it into a
    404.
    """


def _row_exists_sync(db: Any, table: str, row_id: int) -> bool:
    """Cheap existence check for tables with no dedicated getter.

    ``table`` is always a hardcoded literal from call sites in this file,
    never user input, so the f-string is not a SQL-injection surface.
    Mirrors the ``db.lock`` / ``db._get_connection()`` idiom already used
    for ad hoc queries in backend/api/vlm.py's ``_list_paths_for_retag``.
    """
    with db.lock, db._get_connection() as conn:
        row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return row is not None


class StoryboardService:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ---- storyboards ----------------------------------------------------

    async def list_storyboards(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_storyboards)

    async def create_storyboard(self, **fields: Any) -> int:
        return await asyncio.to_thread(self.db.create_storyboard, **fields)

    async def get_storyboard(self, storyboard_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_storyboard, storyboard_id)

    async def get_storyboard_tree(self, storyboard_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_storyboard_tree, storyboard_id)

    async def update_storyboard(self, storyboard_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_storyboard, storyboard_id, **fields)

    async def delete_storyboard(self, storyboard_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_storyboard, storyboard_id)

    # ---- subjects ---------------------------------------------------------

    async def subject_exists(self, subject_id: int) -> bool:
        return await asyncio.to_thread(
            _row_exists_sync, self.db, "storyboard_subjects", subject_id
        )

    async def create_subject(self, storyboard_id: int, **fields: Any) -> int:
        if await self.get_storyboard(storyboard_id) is None:
            raise ParentNotFoundError(f"no storyboard with id {storyboard_id}")
        return await asyncio.to_thread(self.db.create_subject, storyboard_id, **fields)

    async def update_subject(self, subject_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_subject, subject_id, **fields)

    async def delete_subject(self, subject_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_subject, subject_id)

    # ---- scenes -------------------------------------------------------------

    async def scene_exists(self, scene_id: int) -> bool:
        return await asyncio.to_thread(_row_exists_sync, self.db, "scenes", scene_id)

    async def create_scene(self, storyboard_id: int, **fields: Any) -> int:
        if await self.get_storyboard(storyboard_id) is None:
            raise ParentNotFoundError(f"no storyboard with id {storyboard_id}")
        return await asyncio.to_thread(self.db.create_scene, storyboard_id, **fields)

    async def update_scene(self, scene_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_scene, scene_id, **fields)

    async def delete_scene(self, scene_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_scene, scene_id)

    # ---- panels ---------------------------------------------------------

    async def create_panel(self, scene_id: int, **fields: Any) -> int:
        if not await self.scene_exists(scene_id):
            raise ParentNotFoundError(f"no scene with id {scene_id}")
        return await asyncio.to_thread(self.db.create_panel, scene_id, **fields)

    async def get_panel(self, panel_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_panel, panel_id)

    async def update_panel(self, panel_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_panel, panel_id, **fields)

    async def delete_panel(self, panel_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_panel, panel_id)

    async def select_panel_image(self, panel_id: int, image_id: Optional[int]) -> bool:
        return await asyncio.to_thread(self.db.select_panel_image, panel_id, image_id)
