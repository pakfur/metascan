"""Async wrappers over the ComfyUI preset/job DB methods.

DatabaseManager is synchronous and guarded by a threading.Lock; route
handlers must never call it directly on the event loop. Every method here
is a thin asyncio.to_thread hop, matching backend/services/folders_service.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


class ComfyService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_presets(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_workflow_presets)

    async def get_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_workflow_preset, preset_id)

    async def delete_preset(self, preset_id: int) -> bool:
        return await asyncio.to_thread(self.db.delete_workflow_preset, preset_id)

    async def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.get_generation_job, job_id)

    async def list_jobs(
        self, states: Optional[List[str]] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_generation_jobs, states, limit)
