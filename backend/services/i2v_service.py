"""Async wrappers around the i2v DB methods + file trashing."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from metascan.core.comfy_bindings import BindingError, resolve_bindings

from metascan.utils.trash import remove_files_to_trash


class I2vService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_videos(self, source_path: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.db.list_i2v_videos, source_path)

    async def preset_supports_steps(self, preset_id: Optional[int]) -> bool:
        """True when the preset's workflow binds MS_STEPS. Resolved from
        workflow_json at call time (not the stored bindings snapshot), so
        a preset registered before MS_STEPS existed reads correctly."""
        if not preset_id:
            return False
        preset = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if preset is None:
            return False
        try:
            workflow = json.loads(preset["workflow_json"])
            return resolve_bindings(workflow, preset["kind"]).steps is not None
        except (BindingError, TypeError, ValueError):
            return False

    async def delete_video(self, video_id: int) -> bool:
        deleted, purged = await asyncio.to_thread(self.db.delete_i2v_video, video_id)
        if deleted and purged:
            await asyncio.to_thread(remove_files_to_trash, purged)
        return deleted
