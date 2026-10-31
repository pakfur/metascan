"""Drains the durable ``vlm_tag_queue`` table into VlmClient.

The embedding worker (``tag_with_vlm=True``), the scan and the retag route
queue image paths with ``db.enqueue_tags``. This pump takes them in pages
(``db.next_tag_batch``), calls ``VlmClient.generate_tags`` with bounded
concurrency and writes the tags with ``source='vlm'``. A row leaves the
queue only once its tags are written (``finish_tag``), so a pause, a VLM
unload or a restart loses nothing.

Designed to run from the FastAPI process -- it shares the VlmClient
singleton with the on-demand /api/vlm/tag endpoint."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional, Set

from backend.services.background.protocol import Progress
from metascan.core.gpu_guard import gpu_reserved
from metascan.core.vlm_client import (
    STATE_READY,
    VlmNotReady,
    background_vlm_work,
)
from metascan.utils.path_utils import to_native_path_object

logger = logging.getLogger(__name__)

PAGE_SIZE = 50


def _format_eta(seconds: float) -> str:
    """Render an ETA in seconds as ``HH:MM:SS`` (or ``--:--:--`` for inf)."""
    if seconds == float("inf") or seconds != seconds:  # NaN check
        return "--:--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _client_unavailable(client: Any) -> bool:
    """Training holds the GPU, or the client is not ready right now: an
    unload or ``shutdown_all`` killed llama-server under a request. A failure
    seen then is the VLM going away, not the item's fault."""
    if gpu_reserved():
        return True
    state = getattr(client, "state", None)
    return isinstance(state, str) and state != STATE_READY


class VlmTagPump:
    def __init__(
        self,
        client: Any,
        db: Any,
        *,
        model_id: str,
        concurrency: int = 2,
        unless_stopped_since: Optional[int] = None,
    ) -> None:
        """``unless_stopped_since``: the client's ``_explicit_stops`` when
        the work was requested (scan start). A drain is a background
        re-ensure: if an explicit unload (ComfyUI render, /unload) landed
        since, it must not bring the VLM back -- the batch stays queued."""
        self._unless_stopped_since = unless_stopped_since
        self._client = client
        self._db = db
        self._model_id = model_id
        self._concurrency = concurrency
        self._sem = asyncio.Semaphore(concurrency)
        self._paused = False
        self._done = 0
        self._total = 0

    def pause(self) -> None:
        """Finish the items in flight and take no more; the queue keeps
        the rest."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def progress(self) -> Progress:
        return Progress(done=self._done, total=self._total, unit="images")

    async def drain_once(self) -> int:
        """Tag every queued image (and those queued meanwhile). Returns how
        many items were attempted.

        Background VLM work: when the roles take turns it never evicts the
        prompt writer while the writer is busy (it waits for it), and an
        interactive request may preempt it (items are retried)."""
        with background_vlm_work():
            return await self._drain_once()

    async def _drain_once(self) -> int:
        queued = await asyncio.to_thread(self._db.tag_queue_count)
        if not queued:
            return 0
        self._done = 0
        self._total = queued

        since = self._unless_stopped_since
        try:
            if since is None:
                await self._client.ensure_started(self._model_id)
            else:
                await self._client.ensure_started(
                    self._model_id, unless_stopped_since=since
                )
        except VlmNotReady as exc:
            # Unloaded since the scan began, retired, backing off or the GPU
            # held by training: the batch stays queued for the next drain.
            logger.info(
                "VLM tagging deferred (%s); %d image(s) stay queued", exc, queued
            )
            return 0

        ok = 0
        fail = 0
        empty = 0  # tags returned but list was empty (parser/grammar miss)
        not_ready = False  # the client went away: stop, rows stay queued
        attempted: Set[str] = set()  # rows tried this pass (never re-taken)
        start = time.monotonic()
        last_summary = start
        progress_lock = asyncio.Lock()
        SUMMARY_EVERY_N = 25
        SUMMARY_INTERVAL_S = 30.0

        logger.info(
            "VLM tagging batch start: %d images, model=%s, concurrency=%d",
            queued,
            self._model_id,
            self._concurrency,
        )

        async def _one(p: str) -> None:
            nonlocal ok, fail, empty, last_summary, not_ready
            if self._paused or not_ready:
                return
            ok_local = False
            empty_local = False
            async with self._sem:
                if self._paused or not_ready:
                    return
                attempted.add(p)
                try:
                    tags = await self._client.generate_tags(to_native_path_object(p))
                    if not tags and _client_unavailable(self._client):
                        # generate_tags answers [] for a request that died
                        # with the server: keep the row.
                        raise VlmNotReady("VLM went away mid-request")
                    # Move the sync DB write off the event loop -- see
                    # the matching note in backend/api/vlm.py.
                    await asyncio.to_thread(
                        self._db.add_tag_indices, to_native_path_object(p), tags, "vlm"
                    )
                    await asyncio.to_thread(self._db.finish_tag, p)
                    ok_local = True
                    empty_local = not tags
                    if empty_local:
                        # Nothing came back: hygiene must not queue it again.
                        await asyncio.to_thread(self._db.mark_tag_exhausted, p)
                except FileNotFoundError:
                    # The file is gone: nothing to tag, nothing to retry.
                    await asyncio.to_thread(self._db.finish_tag, p)
                    return
                except Exception as e:
                    if isinstance(e, VlmNotReady) or _client_unavailable(self._client):
                        # Unloaded / GPU taken under us: the item is fine, keep it.
                        if not not_ready:
                            logger.info(
                                "VLM tagging stopped: the VLM is not ready (%s); "
                                "the rest stays queued",
                                e,
                            )
                        not_ready = True
                        return
                    logger.warning("VLM tag failed for %s: %s", p, e)
                    await asyncio.to_thread(self._db.fail_tag, p, str(e))

            async with progress_lock:
                if ok_local:
                    ok += 1
                    if empty_local:
                        empty += 1
                else:
                    fail += 1
                done = ok + fail
                self._done = done
                now = time.monotonic()
                if (
                    done % SUMMARY_EVERY_N == 0
                    or (now - last_summary) >= SUMMARY_INTERVAL_S
                ):
                    last_summary = now
                    elapsed = now - start
                    rate = done / elapsed if elapsed > 0 else 0.0
                    remaining = self._total - done
                    eta_s = remaining / rate if rate > 0 else float("inf")
                    logger.info(
                        "VLM tagging [%d/%d] ok=%d (empty=%d) fail=%d  "
                        "rate=%.2f img/s  ETA=%s",
                        done,
                        self._total,
                        ok,
                        empty,
                        fail,
                        rate,
                        _format_eta(eta_s),
                    )

        while not self._paused and not not_ready:
            # Rows this pass already tried (a failed one keeps its place in
            # the queue until it is out of attempts) come first: ask for
            # enough to see past them.
            rows = await asyncio.to_thread(
                self._db.next_tag_batch, PAGE_SIZE + len(attempted)
            )
            page = [p for p in rows if p not in attempted][:PAGE_SIZE]
            if not page:
                break
            remaining = await asyncio.to_thread(self._db.tag_queue_count)
            self._total = max(self._total, self._done + remaining)
            await asyncio.gather(*[_one(p) for p in page])

        total = ok + fail
        elapsed = time.monotonic() - start
        rate = total / elapsed if elapsed > 0 else 0.0
        logger.info(
            "VLM tagging batch done: %d images in %ds (%.2f img/s) — "
            "ok=%d (empty=%d) fail=%d",
            total,
            int(elapsed),
            rate,
            ok,
            empty,
            fail,
        )
        return total
