"""Async wrappers over the ComfyUI preset/job DB methods.

DatabaseManager is synchronous and guarded by a threading.Lock; route
handlers must never call it directly on the event loop. Every method here
is a thin asyncio.to_thread hop, matching backend/services/folders_service.py.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Dict, List, Optional


class PresetInUseError(RuntimeError):
    """A preset cannot be deleted because generation jobs reference it.

    ``generation_jobs.preset_id`` is NOT NULL REFERENCES
    workflow_presets(id) with no ``ON DELETE`` clause, and
    ``PRAGMA foreign_keys = ON``, so SQLite refuses the delete once any
    job row exists. That's intentional: cascading would destroy job
    history and a nullable column would orphan it. This translates the
    raw ``sqlite3.IntegrityError`` — which used to escape uncaught and
    500 the route — into something the API layer can turn into a 409.
    """

    def __init__(self, preset_id: int, job_count: int) -> None:
        self.preset_id = preset_id
        self.job_count = job_count
        super().__init__(
            f"Preset {preset_id} still has {job_count} generation job(s) "
            "referencing it. Delete those jobs first; preset history is "
            "never removed automatically."
        )


class ComfyService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_presets(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_workflow_presets)

    async def get_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_workflow_preset, preset_id)

    async def count_jobs_for_preset(self, preset_id: int) -> int:
        return await asyncio.to_thread(self.db.count_jobs_for_preset, preset_id)

    async def delete_preset(self, preset_id: int) -> bool:
        """Delete a preset, or raise PresetInUseError if jobs reference it."""
        try:
            return await asyncio.to_thread(self.db.delete_workflow_preset, preset_id)
        except sqlite3.IntegrityError as exc:
            count = await self.count_jobs_for_preset(preset_id)
            raise PresetInUseError(preset_id, count) from exc

    async def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_generation_job, job_id)

    async def list_jobs(
        self, states: Optional[List[str]] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_generation_jobs, states, limit)
