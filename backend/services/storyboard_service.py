"""Async wrappers over the storyboard CRUD DB methods.

DatabaseManager is synchronous and guarded by a threading.Lock; route
handlers must never call it directly on the event loop. Every method here
is a thin asyncio.to_thread hop, matching backend/services/comfy_service.py.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from metascan.utils.trash import remove_files_to_trash as _remove_files_sync

logger = logging.getLogger(__name__)


class ParentNotFoundError(RuntimeError):
    """A create call referenced a parent row that doesn't exist.

    storyboard_subjects / scenes reference storyboards(id) and panels
    reference scenes(id), all NOT NULL. Rather than let a bad parent id
    surface as a raw sqlite3.IntegrityError (or a 500), the service
    pre-checks the parent and raises this so the route can turn it into a
    404.
    """


class InvalidReferenceError(RuntimeError):
    """A subject's ``reference_path`` doesn't name a row in ``media``.

    ``storyboard_subjects.reference_path`` FKs ``media(file_path)``; an
    unknown path raises ``sqlite3.IntegrityError`` from SQLite. That's not
    actionable as a raw 500, so create/update_subject catch it here and
    the route maps this to a 400.
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

    async def delete_storyboard(
        self, storyboard_id: int, purge_images: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """Returns ``(deleted, deleted_folder_id)`` -- the route broadcasts
        folder_deleted for the storyboard's library folder, which is
        removed along with the storyboard."""
        ok, purged_files, folder_id = await asyncio.to_thread(
            self.db.delete_storyboard, storyboard_id, purge_images
        )
        if purged_files:
            await asyncio.to_thread(_remove_files_sync, purged_files)
        return ok, folder_id

    # ---- subjects ---------------------------------------------------------

    async def subject_exists(self, subject_id: int) -> bool:
        return await asyncio.to_thread(
            _row_exists_sync, self.db, "storyboard_subjects", subject_id
        )

    async def get_subject(self, subject_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_subject, subject_id)

    async def create_subject(self, storyboard_id: int, **fields: Any) -> int:
        if await self.get_storyboard(storyboard_id) is None:
            raise ParentNotFoundError(f"no storyboard with id {storyboard_id}")
        try:
            return await asyncio.to_thread(
                self.db.create_subject, storyboard_id, **fields
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidReferenceError(
                "reference image is not in the media library"
            ) from exc

    async def update_subject(self, subject_id: int, **fields: Any) -> None:
        try:
            await asyncio.to_thread(self.db.update_subject, subject_id, **fields)
        except sqlite3.IntegrityError as exc:
            raise InvalidReferenceError(
                "reference image is not in the media library"
            ) from exc

    async def subject_references(self, subject_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.db.subject_references, subject_id)

    async def delete_subject(self, subject_id: int, mode: str = "unlink") -> bool:
        ok, purged = await asyncio.to_thread(self.db.delete_subject, subject_id, mode)
        if purged:
            await asyncio.to_thread(_remove_files_sync, purged)
        return ok

    # ---- scenes -------------------------------------------------------------

    async def scene_exists(self, scene_id: int) -> bool:
        return await asyncio.to_thread(_row_exists_sync, self.db, "scenes", scene_id)

    async def get_scene(self, scene_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_scene, scene_id)

    async def create_scene(self, storyboard_id: int, **fields: Any) -> int:
        if await self.get_storyboard(storyboard_id) is None:
            raise ParentNotFoundError(f"no storyboard with id {storyboard_id}")
        try:
            return await asyncio.to_thread(
                self.db.create_scene, storyboard_id, **fields
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidReferenceError(
                "reference image is not in the media library"
            ) from exc

    async def update_scene(self, scene_id: int, **fields: Any) -> None:
        try:
            await asyncio.to_thread(self.db.update_scene, scene_id, **fields)
        except sqlite3.IntegrityError as exc:
            raise InvalidReferenceError(
                "reference image is not in the media library"
            ) from exc

    async def delete_scene(self, scene_id: int, purge_images: bool = False) -> bool:
        ok, purged_files = await asyncio.to_thread(
            self.db.delete_scene, scene_id, purge_images
        )
        if purged_files:
            await asyncio.to_thread(_remove_files_sync, purged_files)
        return ok

    # ---- panels ---------------------------------------------------------

    async def create_panel(self, scene_id: int, **fields: Any) -> int:
        if not await self.scene_exists(scene_id):
            raise ParentNotFoundError(f"no scene with id {scene_id}")
        return await asyncio.to_thread(self.db.create_panel, scene_id, **fields)

    async def get_panel(self, panel_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_panel, panel_id)

    async def update_panel(self, panel_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_panel, panel_id, **fields)

    async def delete_panel(self, panel_id: int, purge_images: bool = False) -> bool:
        ok, purged_files = await asyncio.to_thread(
            self.db.delete_panel, panel_id, purge_images
        )
        if purged_files:
            await asyncio.to_thread(_remove_files_sync, purged_files)
        return ok

    async def panel_exists(self, panel_id: int) -> bool:
        return await asyncio.to_thread(_row_exists_sync, self.db, "panels", panel_id)

    async def delete_panel_video(self, video_id: int) -> bool:
        ok, purged_files = await asyncio.to_thread(self.db.delete_panel_video, video_id)
        if purged_files:
            await asyncio.to_thread(_remove_files_sync, purged_files)
        return ok

    # ---- beats ------------------------------------------------------------

    async def create_beat(self, panel_id: int, **fields: Any) -> int:
        return await asyncio.to_thread(self.db.create_beat, panel_id, **fields)

    async def get_beat(self, beat_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_beat, beat_id)

    async def update_beat(self, beat_id: int, **fields: Any) -> None:
        await asyncio.to_thread(self.db.update_beat, beat_id, **fields)

    async def delete_beat(self, beat_id: int, purge_images: bool = False) -> bool:
        ok, purged = await asyncio.to_thread(self.db.delete_beat, beat_id, purge_images)
        if purged:
            await asyncio.to_thread(_remove_files_sync, purged)
        return ok

    async def select_beat_image(self, beat_id: int, image_id: Optional[int]) -> bool:
        return await asyncio.to_thread(self.db.select_beat_image, beat_id, image_id)

    async def beat_exists(self, beat_id: int) -> bool:
        return await asyncio.to_thread(_row_exists_sync, self.db, "beats", beat_id)
