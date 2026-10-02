"""Send captions to llama-server and write one record per caption.

A pool of workers takes row ids off a shared queue. Each caption gets up to
``attempts`` tries; a parse failure, an HTTP error status or a timeout is
retried, and after the last try the row is written with ``status: "error"``
so the run moves on. A dropped connection with a dead server restarts the
server once; a second death raises ``FatalServerError``. ``request_stop``
lets in-flight captions finish and then ends the run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Protocol, Sequence

import httpx

from metascan.core.t2i_captions import CaptionRow

from .grammar import GRAMMAR
from .parse import ParseError, build_record
from .prompt import SYSTEM_PROMPT, user_message
from .results import caption_sha1

logger = logging.getLogger("caption_classifier.runner")

REQUEST_MAX_TOKENS = 700


class FatalServerError(RuntimeError):
    """llama-server died again after its one restart."""


class RowSource(Protocol):
    def get(self, row_id: int) -> CaptionRow: ...


class ServerLike(Protocol):
    @property
    def base_url(self) -> str: ...

    def alive(self) -> bool: ...

    async def restart(self) -> None: ...


class RecordSink(Protocol):
    def write(self, record: Dict[str, Any]) -> None: ...


def request_body(row: CaptionRow) -> Dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message(row)},
        ],
        "temperature": 0.0,
        "max_tokens": REQUEST_MAX_TOKENS,
        "grammar": GRAMMAR,
        "logprobs": True,
        "top_logprobs": 20,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }


@dataclass
class RunStats:
    total: int = 0
    done: int = 0
    ok: int = 0
    errors: int = 0
    restarts: int = 0
    stopped: bool = False


class Runner:
    def __init__(
        self,
        *,
        rows: RowSource,
        server: ServerLike,
        writer: RecordSink,
        model_id: str,
        prompt_version: str,
        workers: int,
        timeout: float = 60.0,
        attempts: int = 3,
        max_restarts: int = 1,
        progress_every: float = 30.0,
    ) -> None:
        self._rows = rows
        self._server = server
        self._writer = writer
        self._model_id = model_id
        self._prompt_version = prompt_version
        self._workers = workers
        self._timeout = timeout
        self._attempts = attempts
        self._max_restarts = max_restarts
        self._progress_every = progress_every
        self._stop = False
        self._restart_lock: Optional[asyncio.Lock] = None
        self._http: Optional[httpx.AsyncClient] = None
        self.stats = RunStats()

    def request_stop(self) -> None:
        self._stop = True

    async def run(self, row_ids: Sequence[int]) -> RunStats:
        self.stats = RunStats(total=len(row_ids))
        self._restart_lock = asyncio.Lock()
        queue: Deque[int] = deque(row_ids)
        started = time.monotonic()
        progress = asyncio.create_task(self._progress(started))
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                self._http = http
                try:
                    async with asyncio.TaskGroup() as group:
                        for _ in range(self._workers):
                            group.create_task(self._worker(queue))
                except BaseExceptionGroup as eg:
                    raise eg.exceptions[0]
        finally:
            progress.cancel()
            self._http = None
        self.stats.stopped = self._stop and self.stats.done < self.stats.total
        self._log_progress(started)
        return self.stats

    async def _worker(self, queue: Deque[int]) -> None:
        while queue and not self._stop:
            row = self._rows.get(queue.popleft())
            record = await self._classify(row)
            self._writer.write(record)
            self.stats.done += 1
            if record["status"] == "ok":
                self.stats.ok += 1
            else:
                self.stats.errors += 1
                logger.warning("row %d failed: %s", row.id, record["error"])

    async def _classify(self, row: CaptionRow) -> Dict[str, Any]:
        reason = ""
        for _ in range(self._attempts):
            try:
                return self._record(row, "ok", await self._classify_once(row))
            except ParseError as exc:
                reason = f"unparseable answer: {exc}"
            except httpx.HTTPStatusError as exc:
                reason = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            except httpx.TimeoutException:
                reason = f"timed out after {self._timeout:.0f}s"
            except httpx.TransportError as exc:
                reason = f"connection failed: {exc!r}"
                await self._recover()
        return self._record(row, "error", {"error": reason})

    async def _classify_once(self, row: CaptionRow) -> Dict[str, Any]:
        assert self._http is not None
        resp = await self._http.post(
            f"{self._server.base_url}/v1/chat/completions", json=request_body(row)
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        tokens = (choice.get("logprobs") or {}).get("content") or []
        if not tokens:
            raise ParseError("response carries no logprobs")
        return build_record(
            caption=row.caption,
            males=row.males,
            females=row.females,
            content=choice["message"]["content"],
            tokens=tokens,
        )

    async def _recover(self) -> None:
        assert self._restart_lock is not None
        async with self._restart_lock:
            for _ in range(10):  # the exit can lag the dropped connection
                if not self._server.alive():
                    break
                await asyncio.sleep(0.1)
            else:
                return  # still running: a transient drop, just retry
            if self.stats.restarts >= self._max_restarts:
                raise FatalServerError("llama-server crashed again after a restart")
            self.stats.restarts += 1
            await self._server.restart()

    def _record(
        self, row: CaptionRow, status: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "row_id": row.id,
            "caption_sha1": caption_sha1(row.caption),
            "prompt_version": self._prompt_version,
            "model": self._model_id,
            "status": status,
            **fields,
        }

    async def _progress(self, started: float) -> None:
        while True:
            await asyncio.sleep(self._progress_every)
            self._log_progress(started)

    def _log_progress(self, started: float) -> None:
        s = self.stats
        elapsed = max(time.monotonic() - started, 1e-6)
        rate = s.done / elapsed
        left = (s.total - s.done) / rate if rate > 0 else float("inf")
        eta = (
            "?"
            if left == float("inf")
            else time.strftime("%H:%M:%S", time.gmtime(left))
        )
        logger.info(
            "%d/%d rows · %.2f rows/s · ETA %s · %d errors · %d restarts",
            s.done,
            s.total,
            rate,
            eta,
            s.errors,
            s.restarts,
        )
