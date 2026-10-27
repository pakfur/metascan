"""Async wrappers around the t2i_images DB methods + file trashing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from metascan.core.judgments import GRADES
from metascan.core.scorecard_blind import blind_t2i_row
from metascan.core.t2i_form import form_state_for_row
from metascan.core.training_archive import delete_generated
from metascan.utils.trash import trash_file

logger = logging.getLogger(__name__)


class T2iService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_images(
        self, limit: int, before_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        """Generated images, newest first, each with a COMPLETE
        ``form_state``: the stored one, or one built from its as-rendered
        facts for an image ingested after a restart. The dialog sees one
        shape. A blind scorecard image (no grade yet) carries neither its
        prompt nor its writer (``scorecard_blind``)."""
        rows: List[Dict[str, Any]] = await asyncio.to_thread(
            self.db.list_t2i_images, limit, before_id
        )
        for row in rows:
            row["form_state"] = form_state_for_row(row)
            blind_t2i_row(row)
        return rows

    async def list_paths(self) -> List[str]:
        return list(await asyncio.to_thread(self.db.list_t2i_paths))

    async def update_form_state(
        self, image_id: int, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge a validated patch into one image's editable form state and
        return the updated row, or None if the image does not exist.

        Only ``form_state`` is written. The as-rendered columns (``prompt_used``,
        ``seed``, ``model``, ...) are facts about the picture; see
        metascan/core/t2i_form.py.
        """

        def _apply() -> Optional[Dict[str, Any]]:
            row = self.db.get_t2i_image(image_id)
            if row is None:
                return None
            merged = {**form_state_for_row(row), **patch}
            self.db.set_t2i_image_form_state(image_id, merged)
            row["form_state"] = merged
            return dict(blind_t2i_row(dict(row, form_state=dict(merged))))

        return await asyncio.to_thread(_apply)

    async def delete_graded_scorecard_images(self, scorecard_id: str) -> Dict[str, Any]:
        """Delete every graded live image of one scorecard, as
        :meth:`delete_image` deletes one: the tombstone keeps its grade,
        lineage and archived thumbnail, so the scorecard's pairs, the
        adapter's promotion and the taste labels are unchanged. Ungraded
        images stay. Each image's grade is read again just before it goes
        (one cleared meanwhile stays). A file that cannot be trashed is
        counted in ``failed`` and kept. ``{deleted, deleted_paths,
        kept_ungraded, failed}``; paths are never logged."""

        def run() -> Dict[str, Any]:
            graded, ungraded = self.db.scorecard_cleanup_candidates(scorecard_id)
            deleted_paths: List[str] = []
            failed = 0
            for image_id in graded:
                row = self.db.get_t2i_image(image_id)
                if row is None:
                    continue
                if row.get("grade") not in GRADES:
                    ungraded += 1
                    continue
                try:
                    gone = delete_generated(
                        self.db,
                        row["file_path"],
                        trash_file,
                        lambda thumbs, i=image_id: self.db.delete_t2i_image(i, thumbs),
                    )
                except OSError:
                    failed += 1
                    continue
                if gone:
                    deleted_paths.append(row["file_path"])
            if failed:
                logger.warning(
                    "scorecard cleanup: %d image(s) could not be trashed", failed
                )
            return {
                "deleted": len(deleted_paths),
                "deleted_paths": deleted_paths,
                "kept_ungraded": ungraded,
                "failed": failed,
            }

        return await asyncio.to_thread(run)

    async def delete_image(self, image_id: int) -> bool:
        """Delete one generated image: a graded one's thumbnail is archived
        from the live file, the file goes to the OS trash (or is unlinked),
        then the rows go and the tombstone keeps the thumbnail's path
        (``training_archive.delete_generated``). A file a storyboard still
        uses is released, never trashed. A failed trash raises and keeps
        the rows; False for an unknown image."""

        def run() -> bool:
            row = self.db.get_t2i_image(image_id)
            if row is None:
                return False
            return delete_generated(
                self.db,
                row["file_path"],
                trash_file,
                lambda thumbs: self.db.delete_t2i_image(image_id, thumbs),
            )

        return await asyncio.to_thread(run)
