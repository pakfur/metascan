"""Debounced incremental CLIP index build after T2I ingests.

Taste scores need a CLIP vector, and vectors only come from an index build.
A T2I ingest adds media rows only, so without this the result tiles would
stay unscored until the next library scan or a manual "Build Index". After
the last ingest the scheduler waits ``delay_s``, then starts an incremental
build (``rebuild=False``: only the unembedded files). The embedding queue's
completion hook then rescores and broadcasts ``scores_updated``.

GPU politeness: the embedding worker loads CLIP on the GPU when one is
available, so the build waits while an index build is already running or
while any generation work (a T2I batch, or any ComfyUI job) is in flight --
it re-arms and checks again ``delay_s`` later until the work has settled.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_DELAY_S = 15.0


class AutoIndexScheduler:
    def __init__(
        self,
        start_build: Callable[[], Awaitable[Any]],
        is_indexing: Callable[[], bool],
        is_busy: Callable[[], bool],
        delay_s: float = DEFAULT_DELAY_S,
    ) -> None:
        self._start_build = start_build
        self._is_indexing = is_indexing
        self._is_busy = is_busy
        self.delay_s = float(delay_s)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._task: Optional[asyncio.Task] = None
        self._deadline = 0.0
        # Set by every notify; cleared when a build starts. A notify that
        # lands while a build is starting schedules one more build.
        self._dirty = False
        self._closed = False

    # ------------------------------------------------------------------

    def on_t2i_event(self, channel: str, event: str, data: Any = None) -> None:
        """``T2iRunner.on_event`` listener: new images were ingested."""
        if channel == "t2i" and event == "t2i_images_changed":
            self.notify()

    def notify(self) -> None:
        """(Re)start the debounce. Safe from any thread."""
        if self._closed:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is None:
            self._loop = running
        loop = self._loop
        if loop is None:
            logger.debug("auto index build skipped: no event loop")
            return
        if running is loop:
            self._arm()
            return
        try:
            loop.call_soon_threadsafe(self._arm)
        except RuntimeError:
            logger.debug("auto index build skipped: event loop closed")

    def _arm(self) -> None:
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        self._dirty = True
        self._deadline = loop.time() + self.delay_s
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run(), name="auto-index-build")

    def _blocked(self) -> bool:
        try:
            return bool(self._is_indexing()) or bool(self._is_busy())
        except Exception:
            # A probe that cannot answer counts as busy: try again later.
            logger.debug("auto index busy probe failed", exc_info=True)
            return True

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._closed:
            remaining = self._deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            if self._blocked():
                self._deadline = loop.time() + self.delay_s
                continue
            self._dirty = False
            try:
                await self._start_build()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info("auto index build did not start: %s", exc)
            if not self._dirty:
                return
            # More images arrived while the build was starting: their
            # paths may have missed that build's snapshot.
            self._deadline = loop.time() + self.delay_s

    async def aclose(self) -> None:
        self._closed = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def generation_busy(t2i_runner: Any, comfy_client: Any, i2v_runner: Any = None) -> bool:
    """True while a T2I batch is registered (prompting or rendering), an I2V
    run is still writing prompts, or ComfyUI still has any job queued or
    running (I2V, storyboards too)."""
    if t2i_runner is not None and t2i_runner.active_batches():
        return True
    if i2v_runner is not None and i2v_runner.is_prompting():
        return True
    return bool(comfy_client is not None and comfy_client.has_pending_jobs())
