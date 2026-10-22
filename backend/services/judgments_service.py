"""Service layer for taste judgments (verdicts, reasons, seen) plus the
FAISS-backed embedding lookup used to snapshot tombstones."""

import asyncio
import logging
from typing import Any, List, Optional, Tuple

import numpy as np

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import JudgmentState
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)


class JudgmentsService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def get(self, path: str) -> Optional[JudgmentState]:
        return await asyncio.to_thread(self.db.get_judgment, path)

    async def mark_seen(self, path: str) -> Optional[JudgmentState]:
        return await asyncio.to_thread(self.db.mark_seen, path)

    async def set_verdict(
        self,
        path: str,
        verdict: Optional[str],
        score_shown: Optional[float] = None,
        sticky_category_id: Optional[int] = None,
    ) -> Optional[JudgmentState]:
        return await asyncio.to_thread(
            self.db.set_verdict,
            path,
            verdict,
            score_shown,
            None,
            sticky_category_id,
        )

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
    index, or None. Runs under the DB lock, so it never loads the index
    from disk: an unloaded index yields None."""
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


def backfill_tombstone_embeddings(db: DatabaseManager, fm: Any) -> int:
    """Fill NULL tombstone embeddings from a loaded FAISS manager.

    Tombstones written while the index wasn't in memory get NULL vectors;
    deleted items stay in the on-disk index until a full rebuild, so once
    it loads we can still recover them. The DB listing and each write
    batch take the lock briefly; every FAISS read happens outside it.
    Only rows still NULL are written. Returns the number filled."""
    if fm is None or not getattr(fm, "is_loaded", False):
        return 0
    model_key = (getattr(fm, "meta", None) or {}).get("model_key")
    if not model_key:
        return 0
    missing = db.tombstones_missing_embedding()
    if not missing:
        return 0
    filled = 0
    batch: List[Tuple[int, bytes, str]] = []
    for tomb_id, posix_path in missing:
        vec = None
        for key in dict.fromkeys((to_native_path(posix_path), posix_path)):
            try:
                vec = fm.get_embedding(key)
            except Exception:
                logger.debug("FAISS read failed for %s", key, exc_info=True)
                vec = None
            if vec is not None:
                break
        if vec is None:
            continue
        batch.append(
            (tomb_id, np.asarray(vec).astype(np.float32).tobytes(), str(model_key))
        )
        if len(batch) >= _BACKFILL_BATCH:
            filled += db.set_tombstone_embeddings(batch)
            batch = []
    filled += db.set_tombstone_embeddings(batch)
    logger.info(
        "Tombstone embedding backfill: filled %d of %d tombstones missing a vector",
        filled,
        len(missing),
    )
    return filled
