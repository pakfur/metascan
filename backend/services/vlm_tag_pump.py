"""Drains the embedding-worker's vlm_pending.jsonl into VlmClient.

The embedding worker writes one line per embedded file when
``tag_with_vlm=True``. This pump reads those lines, calls
``VlmClient.generate_tags`` (with bounded concurrency), and writes the
tags to the DB with ``source='vlm'``.

Designed to run from the FastAPI process — it shares the VlmClient
singleton with the on-demand /api/vlm/tag endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _format_eta(seconds: float) -> str:
    """Render an ETA in seconds as ``HH:MM:SS`` (or ``--:--:--`` for inf)."""
    if seconds == float("inf") or seconds != seconds:  # NaN check
        return "--:--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class VlmTagPump:
    def __init__(
        self,
        queue_dir: Path,
        client: Any,
        db: Any,
        *,
        model_id: str,
        concurrency: int = 2,
    ) -> None:
        self._queue_dir = queue_dir
        self._client = client
        self._db = db
        self._model_id = model_id
        self._sem = asyncio.Semaphore(concurrency)
        self._cancelled = False

    @property
    def pending_file(self) -> Path:
        return self._queue_dir / "vlm_pending.jsonl"

    def cancel(self) -> None:
        self._cancelled = True

    async def drain_once(self) -> int:
        """Process every line currently in vlm_pending.jsonl. Returns count."""
        if not self.pending_file.exists():
            return 0
        # Snapshot + truncate atomically — any new lines after this point go
        # to a freshly-empty file and are picked up by the next drain.
        text = self.pending_file.read_text(encoding="utf-8")
        self.pending_file.write_text("", encoding="utf-8")
        paths: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                paths.append(json.loads(line)["path"])
            except (json.JSONDecodeError, KeyError):
                logger.warning("dropping malformed line: %s", line[:200])
        if not paths:
            return 0

        await self._client.ensure_started(self._model_id)

        total = len(paths)
        ok = 0
        fail = 0
        empty = 0  # tags returned but list was empty (parser/grammar miss)
        start = time.monotonic()
        last_summary = start
        progress_lock = asyncio.Lock()
        SUMMARY_EVERY_N = 25
        SUMMARY_INTERVAL_S = 30.0

        logger.info(
            "VLM tagging batch start: %d images, model=%s, concurrency=%d",
            total,
            self._model_id,
            self._sem._value,
        )

        async def _one(p: str) -> None:
            nonlocal ok, fail, empty, last_summary
            if self._cancelled:
                return
            async with self._sem:
                try:
                    tags = await self._client.generate_tags(Path(p))
                    # Move the sync DB write off the event loop — see
                    # the matching note in backend/api/vlm.py.
                    await asyncio.to_thread(
                        self._db.add_tag_indices, Path(p), tags, "vlm"
                    )
                    if tags:
                        ok_local = True
                        empty_local = False
                    else:
                        ok_local = True
                        empty_local = True
                except Exception as e:
                    logger.warning("VLM tag failed for %s: %s", p, e)
                    ok_local = False
                    empty_local = False

            async with progress_lock:
                if ok_local:
                    ok += 1
                    if empty_local:
                        empty += 1
                else:
                    fail += 1
                done = ok + fail
                now = time.monotonic()
                if (
                    done % SUMMARY_EVERY_N == 0
                    or (now - last_summary) >= SUMMARY_INTERVAL_S
                ):
                    last_summary = now
                    elapsed = now - start
                    rate = done / elapsed if elapsed > 0 else 0.0
                    remaining = total - done
                    eta_s = remaining / rate if rate > 0 else float("inf")
                    logger.info(
                        "VLM tagging [%d/%d] ok=%d (empty=%d) fail=%d  "
                        "rate=%.2f img/s  ETA=%s",
                        done,
                        total,
                        ok,
                        empty,
                        fail,
                        rate,
                        _format_eta(eta_s),
                    )

        await asyncio.gather(*[_one(p) for p in paths])

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
