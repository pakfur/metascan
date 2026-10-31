"""The Tagging process: the durable VLM tag queue behind the registry (spec 6.2, 7, 8).

``kick()`` starts a drain (``VlmTagPump``) unless the registry has Tagging
held, paused or stopped. Drains never overlap: a kick that lands mid-drain
sets a rerun flag and the running drain makes one more pass. A drain that
stops with rows left (the VLM unloaded, retired, backing off or the GPU in
use) leaves them queued; the panel then says ``waiting`` and a slow timer
tries again. A registry release (``resume``) kicks too.

Everything runs on the server loop; ``kick`` and ``report`` are safe from
any thread (the embedding poller's worker thread kicks after a build)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any, Callable, Optional, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry
from backend.services.scan_dispatch import preferred_vlm_model_id
from backend.services.vlm_tag_pump import VlmTagPump
from metascan.core.hardware import detect_hardware
from metascan.core.vlm_models import REGISTRY as VLM_REGISTRY
from metascan.core.vlm_select import vlm_model_installed

logger = logging.getLogger(__name__)

RETRY_S = 30.0  # while rows remain and the state is waiting
DEFER_POLL_S = 5.0  # while ComfyUI generation is in flight

_PHASE_IDLE = "idle"
_PHASE_DEFERRED = "deferred"  # waiting for generation to settle
_PHASE_RUNNING = "running"
_PHASE_WAITING = "waiting"  # drain stopped with rows left: the VLM is not ready
_PHASE_CLIP_ONLY = "clip_only"

# (general client, its ``_explicit_stops``) as captured when a scan started.
ScanStops = Optional[Tuple[Any, int]]


class TaggingProcess:
    id = "tagging"
    label = "Tagging"
    order = 30
    holds: Tuple[str, ...] = ()

    def __init__(
        self,
        registry: ProcessRegistry,
        get_client: Callable[[], Any],
        get_db: Callable[[], Any],
        generation_busy: Callable[[], bool],
        *,
        retry_s: float = RETRY_S,
        defer_poll_s: float = DEFER_POLL_S,
    ) -> None:
        self._registry = registry
        self._get_client = get_client
        self._get_db = get_db
        self._generation_busy = generation_busy
        self._retry_s = retry_s
        self._defer_poll_s = defer_poll_s
        try:
            self._loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._phase = _PHASE_IDLE
        self._paused = False
        self._unloaded = False  # the owner unloaded the model: says why paused
        self._error: Optional[str] = None
        self._queued = 0
        self._last_progress: Optional[Progress] = None
        self._pump: Optional[VlmTagPump] = None
        # Touched only on the loop thread (inside _drain).
        self._running = False
        self._rerun = False
        self._future: Optional["concurrent.futures.Future[None]"] = None
        self._retry_handle: Optional[asyncio.TimerHandle] = None
        self._closed = False

    # protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(can_start=True, can_pause=True, can_stop=False)

    def report(self) -> ProcessReport:
        if self._error:
            return ProcessReport(State.FAILED, None, "Tagging failed", self._error)
        if self._paused or self._unloaded:
            note = "Paused: model unloaded" if self._unloaded else "Paused"
            return ProcessReport(State.PAUSED, self._progress(), note)
        if self._phase == _PHASE_RUNNING:
            p = self._progress()
            text = f"Tagging {p.done} of {p.total} images" if p else "Tagging"
            return ProcessReport(State.RUNNING, p, text)
        if self._phase == _PHASE_DEFERRED:
            return ProcessReport(
                State.WAITING, self._progress(), "Waiting for image generation"
            )
        if self._phase == _PHASE_WAITING:
            return ProcessReport(
                State.WAITING,
                self._progress(),
                f"Waiting for the model, {self._queued} queued",
            )
        if self._phase == _PHASE_CLIP_ONLY:
            return ProcessReport(State.IDLE, None, "CLIP tagging is in use")
        if self._queued:
            return ProcessReport(State.IDLE, None, f"{self._queued} queued")
        return ProcessReport(State.IDLE, None, "Nothing to tag")

    async def start(self) -> None:
        self.kick(fresh=True)

    async def pause(self) -> None:
        """Finish the images in flight and take no more."""
        self._paused = True
        if self._pump is not None:
            self._pump.pause()
        self._notify()

    async def resume(self) -> None:
        self._paused = False
        self._unloaded = False
        if self._pump is not None:
            self._pump.resume()
        self.kick(fresh=True)
        self._notify()

    async def stop(self) -> None:
        # Not stoppable (can_stop is False): the queue is durable, pause is the stop.
        return None

    # the owner's actions -----------------------------------------------
    async def owner_unloaded(self) -> None:
        """The owner unloaded the general model: pause, and stay paused after
        holds release (the intent persists in the registry)."""
        self._unloaded = True
        try:
            await self._registry.command(self.id, "pause")
        except CommandRefused as exc:
            logger.debug("tagging owner pause refused: %s", exc.code)

    # driving -----------------------------------------------------------
    def kick(self, *, fresh: bool = False, scan_stops: ScanStops = None) -> bool:
        """Start a drain on the server loop unless Tagging is blocked.

        ``scan_stops``: the (client, ``_explicit_stops``) taken when the scan
        began, so an unload since then wins over the post-scan drain. Any
        other kick re-snapshots the stops when it runs. Returns whether a
        drain was scheduled (a running one coalesces into a rerun)."""
        try:
            if self._registry.blocked(self.id):
                logger.debug("tagging kick skipped: held, paused or stopped")
                return False
        except CommandRefused:
            logger.warning("tagging kick skipped: process not registered")
            return False
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            logger.warning(
                "VLM drain not scheduled: server event loop unavailable; "
                "pending tags stay queued for the next drain"
            )
            return False
        coro = self._drain(fresh, scan_stops)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            logger.warning(
                "VLM drain not scheduled: server event loop closed; "
                "pending tags stay queued for the next drain"
            )
            return False
        # A coalesced request returns at once; keep tracking the real drain.
        if self._future is None or self._future.done():
            self._future = fut
        return True

    async def kick_if_queued(self) -> bool:
        """Startup: the queue outlives the process, so resume what remains."""
        await self._refresh_queue()
        if not self._queued:
            return False
        self._notify()
        return self.kick(fresh=True)

    async def aclose(self) -> None:
        self._closed = True
        self._cancel_retry()
        fut = self._future
        self._future = None
        if fut is not None and not fut.done():
            fut.cancel()

    # internals ---------------------------------------------------------
    def _progress(self) -> Optional[Progress]:
        pump = self._pump
        if pump is not None and self._phase == _PHASE_RUNNING:
            self._last_progress = pump.progress()
        return self._last_progress

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("tagging notify failed")

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self._notify()

    async def _refresh_queue(self) -> None:
        try:
            self._queued = int(await asyncio.to_thread(self._get_db().tag_queue_count))
        except Exception:
            logger.exception("could not count the tag queue")

    def _busy(self) -> bool:
        try:
            return bool(self._generation_busy())
        except Exception:
            # A probe that cannot answer counts as busy: check again later.
            logger.debug("generation busy probe failed", exc_info=True)
            return True

    async def _wait_for_generation_to_settle(self) -> bool:
        """Wait (on the loop) while generation work is in flight. True when it
        had to wait."""
        if not self._busy():
            return False
        logger.info(
            "VLM drain deferred: ComfyUI generation in flight; "
            "it runs when the generation settles"
        )
        self._set_phase(_PHASE_DEFERRED)
        while self._busy():
            await asyncio.sleep(self._defer_poll_s)
        return True

    async def _model_for(self, client: Any) -> Optional[str]:
        """The general role's model when installed, else the installed
        hardware pick (tagging never waits on a download)."""
        mid = getattr(client, "model_id", None) or getattr(
            client, "preferred_model_id", None
        )
        if not mid or not await asyncio.to_thread(vlm_model_installed, mid):
            mid = preferred_vlm_model_id(detect_hardware())
        return mid or None

    @staticmethod
    def _stops(client: Any) -> Optional[int]:
        stops = getattr(client, "_explicit_stops", None)
        return stops if isinstance(stops, int) else None

    async def _drain(self, fresh: bool, scan_stops: ScanStops) -> None:
        if self._running:
            self._rerun = True
            logger.info("VLM drain: one already running, will rerun after")
            return
        self._running = True
        self._cancel_retry()
        self._error = None
        try:
            deferred = await self._wait_for_generation_to_settle()
            client = self._get_client()  # the general role's client
            if client is None:
                logger.debug("VLM drain skipped: no VlmClient installed")
                self._set_phase(_PHASE_CLIP_ONLY)
                return
            mid = await self._model_for(client)
            if not mid:
                logger.debug("VLM drain skipped: no preferred VLM model for this host")
                self._set_phase(_PHASE_CLIP_ONLY)
                return
            await self._refresh_queue()
            spec = VLM_REGISTRY.get(mid)
            # The scan's snapshot only guards the post-scan drain that did not
            # wait; a drain that waited for a render, a resume and a retry
            # snapshot now, because the render's own unload is behind them.
            if scan_stops is not None and not deferred and scan_stops[0] is client:
                since: Optional[int] = scan_stops[1]
            else:
                since = self._stops(client)
            pump = VlmTagPump(
                client=client,
                db=self._get_db(),
                model_id=mid,
                concurrency=spec.parallel_slots if spec is not None else 2,
                unless_stopped_since=since,
            )
            self._pump = pump
            if self._paused:
                pump.pause()
            while not self._paused:
                self._rerun = False
                if await self._wait_for_generation_to_settle():
                    pump._unless_stopped_since = self._stops(client)
                self._set_phase(_PHASE_RUNNING)
                drained = await pump.drain_once()
                logger.info("VLM drain: tagged %d file(s)", drained)
                if not self._rerun:
                    break
            await self._refresh_queue()
            if self._queued and not self._paused:
                # The pump stopped with rows left: the VLM is not ready.
                self._last_progress = pump.progress()
                self._phase = _PHASE_WAITING
                self._arm_retry()
            else:
                self._phase = _PHASE_IDLE
            self._notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("VLM drain failed")
            self._error = f"Tagging failed: {type(exc).__name__}"
            self._phase = _PHASE_IDLE
            self._notify()
        finally:
            self._running = False
            self._rerun = False

    def _arm_retry(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            return
        self._retry_handle = loop.call_later(self._retry_s, self._on_retry)

    def _on_retry(self) -> None:
        self._retry_handle = None
        self.kick()

    def _cancel_retry(self) -> None:
        handle = self._retry_handle
        self._retry_handle = None
        if handle is not None:
            handle.cancel()
