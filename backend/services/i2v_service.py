"""Async wrappers around the i2v DB methods + file trashing."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence

from metascan.core.comfy_bindings import BindingError, resolve_bindings
from metascan.core.i2v_form import form_state_for_row

from metascan.utils.trash import remove_files_to_trash


class I2vService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_videos(
        self, source_path: str, megapixel_options: Sequence[float]
    ) -> List[Dict[str, Any]]:
        """Clips for one source image, each with a COMPLETE ``form_state``:
        the stored one, or -- for a clip ingested before the column existed
        -- one built from its as-rendered facts. The dialog sees one shape."""
        rows = await asyncio.to_thread(self.db.list_i2v_videos, source_path)
        for row in rows:
            row["form_state"] = form_state_for_row(row, megapixel_options)
        return rows

    async def update_form_state(
        self,
        video_id: int,
        patch: Dict[str, Any],
        megapixel_options: Sequence[float],
    ) -> Optional[Dict[str, Any]]:
        """Merge a validated patch into one clip's editable form state and
        return the updated row, or None if the clip does not exist.

        Only ``form_state`` is written. The as-rendered columns are facts
        about the clip; see metascan/core/i2v_form.py.
        """

        def _apply() -> Optional[Dict[str, Any]]:
            row = self.db.get_i2v_video(video_id)
            if row is None:
                return None
            merged = {**form_state_for_row(row, megapixel_options), **patch}
            self.db.set_i2v_video_form_state(video_id, merged)
            row["form_state"] = merged
            return dict(row)

        return await asyncio.to_thread(_apply)

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
