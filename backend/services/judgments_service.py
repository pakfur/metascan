"""Service layer for taste judgments (verdicts, reasons, seen) plus the
FAISS-backed embedding lookup used to snapshot tombstones."""

import asyncio
import logging
from typing import Optional, Tuple

import numpy as np

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import JudgmentState
from metascan.utils.path_utils import to_posix_path

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
        from backend.api.similarity import _get_faiss_manager

        fm = _get_faiss_manager()
        if not fm.is_loaded:
            return None
        model_key = fm.meta.get("model_key")
        if not model_key:
            return None
        for key in (native_path, to_posix_path(native_path)):
            vec = fm.get_embedding(key)
            if vec is not None:
                return vec, str(model_key)
    except Exception:
        logger.debug("embedding lookup failed for %s", native_path, exc_info=True)
    return None
