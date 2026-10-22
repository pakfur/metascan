"""Tag generated media with the categories chosen in the dialog that made it.

Shared by T2iRunner and I2vRunner. The route validates the ids before
anything is queued (``unknown_category_ids``); by ingest time a category may
have been deleted, which is logged and skipped -- tagging never fails an
ingest.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Iterable, List

logger = logging.getLogger(__name__)

GENERATION_SOURCE = "generation"

EmitCb = Callable[[str, str, Dict[str, Any]], None]


def unique_category_ids(category_ids: Iterable[int]) -> List[int]:
    """The ids once each, in the order given."""
    return list(dict.fromkeys(int(cid) for cid in category_ids))


def unknown_category_ids(db: Any, category_ids: Iterable[int]) -> List[int]:
    """The ids that name no category. Sync: call it via asyncio.to_thread."""
    wanted = unique_category_ids(category_ids)
    if not wanted:
        return []
    known = {int(row["id"]) for row in db.list_categories()}
    return [cid for cid in wanted if cid not in known]


async def tag_generated_media(
    db: Any, paths: List[str], category_ids: Iterable[int], emit: EmitCb
) -> int:
    """Link every path to every category with source ``generation``, then
    broadcast ``categories_changed`` once if any link was added. A path
    with no media row is skipped by the DB (the scanner ingests a job's
    files before its outputs are announced). Never raises; returns the
    number of links added."""
    ids = unique_category_ids(category_ids)
    if not paths or not ids:
        return 0
    added = 0
    for cid in ids:
        try:
            added += await asyncio.to_thread(
                db.add_media_categories, list(paths), cid, GENERATION_SOURCE
            )
        except ValueError as exc:
            if str(exc) == "unknown_category":
                logger.warning(
                    "Category %s was deleted before its generated file(s) "
                    "were ingested; skipped",
                    cid,
                )
            else:
                logger.warning(
                    "Could not tag %d generated file(s) with category %s: %s",
                    len(paths),
                    cid,
                    exc,
                )
        except Exception:
            logger.warning(
                "Could not tag %d generated file(s) with category %s",
                len(paths),
                cid,
                exc_info=True,
            )
    if added:
        emit("categories", "categories_changed", {})
    return added
