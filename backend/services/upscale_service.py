"""Accept, revert and discard: the async face of ``UpscaleStore``.

The store owns the ordering of a swap; this adds the rules of the running
server. Copies happen before ``scan.move_gate`` is taken (a long hold would
make ``start_scan`` give up), the critical section runs under it with a scan
check beside it, and a Full clean that is not finished refuses a swap: the
pending snapshot names paths a swap would move. After a swap come the things
every mover owes: ``after_media_move``, a thumbnail, a pHash and a build."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from metascan.core.phash_utils import compute_phash_for_file
from metascan.core.upscale_defaults import generation_defaults
from metascan.core.upscale_refine_prompts import default_store, guess_refine_prompt
from metascan.core.upscale_runner import is_video
from metascan.core.upscale_store import UpscaleError, UpscaleStore
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

CODE_LIBRARY_BUSY = "library_busy"
CODE_ORIGINALS_MOVING = "originals_moving"
GATE_POLL_S = 0.05

Broadcast = Callable[[str, str, Dict[str, Any]], None]


class UpscaleService:
    def __init__(
        self,
        db: Any,
        store: UpscaleStore,
        thumbnails: Any,
        data_dir: Path,
        broadcast: Broadcast,
        is_indexing: Callable[[], bool],
        after_move: Callable[[], Any],
        kick_embedding: Callable[[], Any],
        originals_moving: Callable[[], bool] = lambda: False,
    ) -> None:
        self.db = db
        self.store = store
        self.thumbnails = thumbnails
        self.data_dir = data_dir
        self.broadcast = broadcast
        self.is_indexing = is_indexing
        self.after_move = after_move
        self.kick_embedding = kick_embedding
        self.originals_moving = originals_moving
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Swapped while a build ran: the worker may mark the old vector
        # embedded after the swap's reset, so these are reset again after it.
        self._swapped_during_build: Set[str] = set()
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---- what the dialog shows -------------------------------------------
    def state(self, media_path: str) -> Dict[str, Any]:
        """Thread. ``media_path`` is the path the client holds (native)."""
        posix = to_posix_path(media_path)
        record = self.db.upscale_record(posix)
        return {
            "record": _record_dict(record) if record else None,
            "candidates": [
                _candidate_dict(c) for c in self.db.list_upscale_candidates(posix)
            ],
            "generation": self.generation(posix),
        }

    def generation(self, posix: str) -> Dict[str, Any]:
        """What the item was generated with (``upscale_defaults``). The
        media record of an upscaled item carries its original's metadata
        (``Scanner._process_media_file``), so this is the original's too."""
        scorecard = self.db.scorecard_for_path(posix)
        native = Path(to_native_path(posix))
        video = is_video(native)
        out = generation_defaults(
            self.db.get_media(native),
            self.db.t2i_loras_for_path(posix),
            blind=bool(scorecard and scorecard["blind"]),
            # An I2V clip's own prompt, preset model and LoRAs: a MiniMax
            # refine drifts without them.
            i2v=self.db.i2v_generation_for_path(posix) if video else None,
        )
        # The refine prompt for the image's subject (people, a scene or an
        # illustration), guessed from its tags; the dialog starts on it at a
        # low denoise. A video takes none: its refine redraws the whole clip
        # with its own prompt, and a detail prompt would replace it.
        out["refine_prompt"] = (
            None
            if video
            else guess_refine_prompt(
                self.db.get_tags_for_file(native), default_store().prompts()
            )
        )
        return out

    # ---- swaps ------------------------------------------------------------
    async def accept(self, candidate_id: int) -> str:
        self._refuse_while_moving()
        journal_id = await asyncio.to_thread(self.store.prepare_accept, candidate_id)
        return await self._commit(journal_id)

    async def revert(self, media_path: str) -> str:
        self._refuse_while_moving()
        journal_id = await asyncio.to_thread(
            self.store.prepare_revert, to_posix_path(media_path)
        )
        return await self._commit(journal_id)

    def _refuse_while_moving(self) -> None:
        if self.originals_moving():
            raise UpscaleError(
                CODE_ORIGINALS_MOVING,
                "The Original folder is being moved. Try again when it is done.",
            )

    async def _commit(self, journal_id: int) -> str:
        row = await asyncio.to_thread(self.db.get_upscale_journal, journal_id)
        old_path = row["media_path"] if row else ""
        source = to_native_path(old_path)
        new_posix = await asyncio.to_thread(self._critical, journal_id)
        new_path = to_native_path(new_posix)
        await self._after_swap(old_path, new_posix)
        self.broadcast(
            "upscale", "media_swapped", {"path": source, "new_path": new_path}
        )
        self.broadcast("upscale", "candidates_changed", {"source_path": new_path})
        return new_path

    def _critical(self, journal_id: int) -> str:
        """Thread. The polling of ``scan.py``'s gate rule: a worker thread may
        wait for it, a coroutine must never hand a blocking acquire away."""
        from backend.api import scan
        from backend.services.background.hygiene_checks import _unfinished

        deadline = time.monotonic() + scan.MOVE_GATE_WAIT_S
        while not scan.move_gate.acquire(blocking=False):
            if time.monotonic() >= deadline:
                self.store.abandon(journal_id)
                raise UpscaleError(CODE_LIBRARY_BUSY, "The library is busy. Try again.")
            time.sleep(GATE_POLL_S)
        try:
            if _unfinished(scan, self.data_dir):
                self.store.abandon(journal_id)
                raise UpscaleError(
                    CODE_LIBRARY_BUSY,
                    "A scan or Full clean is unfinished. Try again when it is done.",
                )
            return self.store.commit(journal_id)
        finally:
            scan.move_gate.release()

    async def _after_swap(self, old_path: str, new_path: str) -> None:
        indexing = self.is_indexing()
        if indexing:
            with self._lock:
                self._swapped_during_build.add(new_path)
        try:
            await self.after_move()
        except Exception:
            logger.exception("after_media_move failed")
        await asyncio.to_thread(self._refresh_derived, new_path)
        if not indexing:
            await self._kick()

    def _refresh_derived(self, posix: str) -> None:
        """Thread. Nothing here is required: hygiene refills what fails."""
        path = Path(to_native_path(posix))
        try:
            self.thumbnails.get_or_create_thumbnail(path)
            phash = compute_phash_for_file(path)
            if phash:
                self.db.save_media_hash(path, phash)
        except Exception as exc:
            logger.warning("upscale refresh failed: %s", type(exc).__name__)

    async def _kick(self) -> None:
        try:
            await self.kick_embedding()
        except Exception:
            logger.exception("embedding kick after an upscale failed")

    def embedding_run_finished(self) -> None:
        """Runs on the embedding poller's thread."""
        with self._lock:
            paths = list(self._swapped_during_build)
            self._swapped_during_build.clear()
        if not paths:
            return
        self.db.mark_unembedded(paths)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._kick(), self._loop)

    async def discard(self, candidate_id: int) -> None:
        row = await asyncio.to_thread(self.db.get_upscale_candidate, candidate_id)
        if row is None:
            raise UpscaleError("not_found", "That result no longer exists.")
        await asyncio.to_thread(self.store.discard_candidate, candidate_id)
        self.broadcast(
            "upscale",
            "candidates_changed",
            {"source_path": to_native_path(row["source_path"])},
        )


def _record_dict(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "original_path": to_native_path(record["original_path"]),
        "original_name": record["original_name"],
        "preset_id": record["preset_id"],
        "upscaled_at": record["upscaled_at"],
    }


def _candidate_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **row,
        "source_path": to_native_path(row["source_path"]),
        "file_path": to_native_path(row["file_path"]),
        "audio_lost": bool(row["audio_lost"]),
    }


def candidates_of(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_candidate_dict(r) for r in rows]
