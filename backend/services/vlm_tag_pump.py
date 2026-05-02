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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

        async def _one(p: str) -> None:
            if self._cancelled:
                return
            async with self._sem:
                try:
                    tags = await self._client.generate_tags(Path(p))
                    self._db.add_tag_indices(Path(p), tags, source="vlm")
                except Exception as e:
                    logger.warning("VLM tag failed for %s: %s", p, e)

        await asyncio.gather(*[_one(p) for p in paths])
        return len(paths)
