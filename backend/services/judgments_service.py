"""Service layer for taste judgments (★ favorite, the pass / fail grade,
reasons, seen) plus the FAISS-backed embedding lookup used to snapshot
tombstones."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import Grade, JudgmentState
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)


class JudgmentsService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def get(self, path: str) -> Optional[JudgmentState]:
        return await asyncio.to_thread(self.db.get_judgment, path)

    async def batch_position(self, path: str) -> Optional[Dict[str, int]]:
        """``{"index", "size"}`` of a T2I image in its batch, else None."""
        pos = await asyncio.to_thread(self.db.get_t2i_batch_position, path)
        return {"index": pos[0], "size": pos[1]} if pos is not None else None

    async def scorecard(self, path: str) -> Optional[Dict[str, Any]]:
        """``{"id", "blind"}`` of a scorecard image, else None."""
        info = await asyncio.to_thread(self.db.scorecard_for_path, path)
        if info is None:
            return None
        return {"id": info["scorecard_id"], "blind": bool(info["blind"])}

    async def mark_seen(
        self, path: str, score_shown: Optional[float] = None
    ) -> Optional[JudgmentState]:
        return await asyncio.to_thread(self.db.mark_seen, path, None, score_shown)

    async def set_grade(
        self,
        path: str,
        grade: Optional[Grade],
        score_shown: Optional[float] = None,
        sticky_category_id: Optional[int] = None,
    ) -> Optional[JudgmentState]:
        return await asyncio.to_thread(
            self.db.set_grade,
            path,
            grade,
            score_shown,
            None,
            sticky_category_id,
        )

    async def set_favorite(self, path: str, on: bool) -> Optional[JudgmentState]:
        """★ only; never the grade, its reasons or the scorecard."""
        return await asyncio.to_thread(self.db.set_favorite_state, path, on)

    async def set_reason(
        self,
        path: str,
        reason: str,
        on: bool,
        score_shown: Optional[float] = None,
    ) -> Optional[JudgmentState]:
        return await asyncio.to_thread(
            self.db.set_reason, path, reason, on, score_shown
        )


def faiss_embedding_lookup(native_path: str) -> Optional[Tuple[np.ndarray, str]]:
    """Return ``(vector, model_key)`` for a file from the in-memory FAISS
    index, or None: what a tombstone keeps in ``tombstone_vectors`` under
    ``model_key`` when the file is deleted. Runs under the DB lock, so it
    never loads the index from disk: an unloaded index yields None."""
    try:
        from backend.api import similarity

        # Read the module global; _get_faiss_manager() would construct the
        # manager and load the index from disk, which must not happen here.
        fm = similarity._faiss_manager
        if fm is None or not fm.is_loaded:
            return None
        model_key = fm.meta.get("model_key")
        if not model_key:
            return None
        for key in (native_path, to_posix_path(native_path)):
            vec = fm.get_embedding(key)
            if vec is not None:
                return vec, str(model_key)
    except Exception:
        logger.warning("embedding lookup failed for %s", native_path, exc_info=True)
    return None


_BACKFILL_BATCH = 500


def _read_vector(fm: Any, posix_path: str) -> Optional[np.ndarray]:
    """The index may key a file by its native or its POSIX path."""
    for key in dict.fromkeys((to_native_path(posix_path), posix_path)):
        try:
            vec = fm.get_embedding(key)
        except Exception:
            logger.debug("FAISS read failed for %s", key, exc_info=True)
            continue
        if vec is not None:
            return np.asarray(vec)
    return None


def backfill_tombstone_embeddings(db: DatabaseManager, fm: Any) -> int:
    """Give tombstones a vector for a loaded FAISS manager's CLIP model from
    the index itself.

    Tombstones written while the index wasn't in memory have no vector; a
    deleted file's vector stays in the on-disk index until its CLIP model
    changes, so once it loads we can still recover them, into
    ``tombstone_vectors`` under the index's model. The index is read by
    path, so only tombstones whose path can still be their own are
    (Review Focus 5; ``db.tombstones_for_vector_backfill``): never one that
    already has a vector for any model (it was deleted before an index of
    another model was built), one with an archived thumbnail (the
    re-embedder computes its vector from its own image), or a path a live
    media row holds or another tombstone shares. The DB listing and each
    write batch take the lock briefly; every FAISS read happens outside it.
    Returns the number filled."""
    if fm is None or not getattr(fm, "is_loaded", False):
        return 0
    model_key = (getattr(fm, "meta", None) or {}).get("model_key")
    if not model_key:
        return 0
    model_key = str(model_key)
    missing = db.tombstones_for_vector_backfill()
    if not missing:
        return 0
    filled = 0
    batch: List[Tuple[int, str, bytes]] = []
    for tomb_id, posix_path in missing:
        vec = _read_vector(fm, posix_path)
        if vec is None:
            continue
        batch.append((tomb_id, model_key, np.asarray(vec).astype(np.float32).tobytes()))
        if len(batch) >= _BACKFILL_BATCH:
            filled += db.add_tombstone_vectors(batch)
            batch = []
    filled += db.add_tombstone_vectors(batch)
    logger.info(
        "Tombstone embedding backfill: filled %d of %d tombstones missing a "
        "vector for CLIP model %s",
        filled,
        len(missing),
        model_key,
    )
    return filled
