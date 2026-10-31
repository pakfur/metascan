"""Applying ``faiss_pending_renames`` to a FAISS index.

A moved media row keeps its vector: the move writes ``(old, new)`` rows in
its own transaction, and whoever holds the index on disk renames the rows
(``FaissIndexManager.rename``), saves, and clears the pending rows. Two
callers, never at once: the embedding worker at the start of a run (before
it adds anything) and the server while no worker process is alive.

Order matters for the tombstone-vector rules (CLAUDE.md, "Tombstone
vectors"): the tombstones at both paths are stamped ``path_rewritten``
BEFORE the save -- a rename overwrites the row at ``new`` and removes the
one at ``old``, so a deleted file's tombstone there must never read it by
path again. A stamp that cannot be written means nothing is saved and the
in-memory change is thrown away (the index is reloaded from disk); the
pending rows stay for the next try.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Sequence, Tuple

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.embedding_manager import FaissIndexManager
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

STAMP_RETRY_DELAYS_S: Tuple[float, ...] = (0.5, 1.0, 2.0)


@dataclass
class RenameResult:
    renamed: int = 0  # rows re-keyed and saved
    cleared: int = 0  # pending rows dropped
    failed: bool = False  # stamp or save failed; pending rows kept


def apply_pending_renames(
    db: DatabaseManager,
    fm: FaissIndexManager,
    *,
    delays: Sequence[float] = STAMP_RETRY_DELAYS_S,
    sleep: Callable[[float], None] = time.sleep,
) -> RenameResult:
    """Rename the rows of ``fm`` (loaded or created by the caller, who owns
    the index files) for every pending rename, save, and clear the rows.

    A pending row is dropped without a rename when its old path is not in
    the index (never embedded, already applied, or the CLIP model changed
    and the index was replaced) or when the file at its new path is not
    marked embedded (the old path may hold a deleted file's stale row)."""
    pending = db.list_faiss_pending_renames()
    if not pending:
        return RenameResult()
    embedded = db.embedded_paths([new for _, new in pending])
    pairs = [
        (to_native_path(old), to_native_path(new))
        for old, new in pending
        if new in embedded and fm.has_file(to_native_path(old))
    ]
    renamed = fm.rename(pairs) if pairs else 0
    if renamed:
        paths = [p for pair in pairs for p in pair]
        if not _stamp(db, paths, list(delays), sleep) or not fm.save():
            logger.error(
                "FAISS rename of %d row(s) not saved; they stay pending", renamed
            )
            fm.load()  # drop the unsaved change
            return RenameResult(failed=True)
    cleared = db.clear_faiss_pending_renames(pending)
    if renamed:
        logger.info("Renamed %d FAISS row(s) after a move", renamed)
    return RenameResult(renamed=renamed, cleared=cleared)


def _stamp(
    db: DatabaseManager,
    paths: Sequence[str],
    delays: list,
    sleep: Callable[[float], None],
) -> bool:
    import sqlite3

    while True:
        try:
            db.mark_tombstone_paths_rewritten(paths)
            return True
        except sqlite3.OperationalError as e:
            if delays:
                sleep(delays.pop(0))
                continue
            logger.error("Could not stamp tombstones for a FAISS rename: %s", e)
            return False
        except Exception as e:
            logger.error("Could not stamp tombstones for a FAISS rename: %s", e)
            return False
