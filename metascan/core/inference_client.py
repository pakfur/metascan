"""Asyncio client for the long-running CLIP inference subprocess.

Spawns and supervises ``metascan/workers/inference_worker.py``, exposes
``async encode_text`` / ``async encode_image`` to the FastAPI server, and
broadcasts state transitions via callbacks so the UI can render a loading
indicator.

Design v1: requests are serialized with an ``asyncio.Lock``. A dedicated
reader task consumes the worker's stdout line by line and routes each line
to either a per-request future or to the event callbacks. This keeps
unsolicited events (``loading`` / ``ready`` / ``error``) from being mis-read
as responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# State machine strings. Kept as plain strings so they can be broadcast
# over JSON WebSocket frames without translation.
STATE_IDLE = "idle"
STATE_SPAWNING = "spawning"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"

_RESPAWN_BACKOFF_SECONDS = (1.0, 3.0, 10.0)


StatusCb = Callable[[str, Dict[str, Any]], None]
ProgressCb = Callable[[Dict[str, Any]], None]


class InferenceError(RuntimeError):
    """Raised when the worker returns ok=false for a request."""


class InferenceClient:
    """Supervises an ``inference_worker`` subprocess for the server process.

    One instance per server; constructed by the FastAPI lifespan. Not
    thread-safe — all ``async`` methods must be awaited from the same
    event loop.
    """

    def __init__(
        self,
        worker_script: Optional[Path] = None,
        extra_worker_args: Optional[List[str]] = None,
    ) -> None:
        # Overridable for tests — production callers use the defaults to
        # spawn ``metascan/workers/inference_worker.py`` via ``sys.executable``.
        self._worker_script = (
            worker_script
            or Path(__file__).parent.parent / "workers" / "inference_worker.py"
        )
        self._extra_worker_args: List[str] = list(extra_worker_args or [])
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._waiter_task: Optional[asyncio.Task[None]] = None
        self._request_lock = asyncio.Lock()
        # Ring buffer of recent stderr lines so crash diagnostics aren't
        # silent after we drain the pipe in _stderr_loop.
        self._stderr_ring: List[str] = []
        self._stderr_ring_max = 200
        # Serializes ``start()`` / ``ensure_started()`` so concurrent requests
        # that hit a cold worker don't race to spawn multiple processes.
        self._start_lock = asyncio.Lock()

        self._pending: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._next_id = 0

        self._state = STATE_IDLE
        self._last_progress: Dict[str, Any] = {}
        self._model_key: Optional[str] = None
        self._device: Optional[str] = None
        self._resolved_device: Optional[str] = None
        self._dim: Optional[int] = None
        self._last_error: Optional[str] = None

        self._ready_event = asyncio.Event()
        self._stopping = False
        self._respawn_attempts = 0

        self._on_status_cbs: List[StatusCb] = []
        self._on_progress_cbs: List[ProgressCb] = []

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def model_key(self) -> Optional[str]:
        return self._model_key

    @property
    def device(self) -> Optional[str]:
        return self._resolved_device or self._device

    @property
    def dim(self) -> Optional[int]:
        return self._dim

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def snapshot(self) -> Dict[str, Any]:
        """Compact JSON-safe view of the client's current state."""
        return {
            "state": self._state,
            "model_key": self._model_key,
            "device": self.device,
            "dim": self._dim,
            "progress": dict(self._last_progress),
            "error": self._last_error,
        }

    def on_status(self, cb: StatusCb) -> None:
        self._on_status_cbs.append(cb)

    def on_progress(self, cb: ProgressCb) -> None:
        self._on_progress_cbs.append(cb)

    def _set_state(self, new_state: str, **extra: Any) -> None:
        if new_state == self._state and not extra:
            return
        self._state = new_state
        payload = {"state": new_state, **extra, **self.snapshot()}
        for cb in list(self._on_status_cbs):
            try:
                cb(new_state, payload)
            except Exception:
                logger.exception("on_status callback raised")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_started(self, model_key: str, device: str = "auto") -> None:
        """Start the worker only if it isn't already running. Safe to call
        from any request handler — concurrent calls are serialized so they
        can't spawn duplicate processes."""
        async with self._start_lock:
            if (
                self._proc is not None
                and self._proc.returncode is None
                and self._state in (STATE_LOADING, STATE_READY)
                and model_key == self._model_key
                and device == self._device
            ):
                return
            if self._state in (STATE_SPAWNING,):
                # Another call is already spawning. Fall through to start()
                # which will detect the live process and no-op.
                return
            await self.start(model_key=model_key, device=device, wait_ready=False)

    async def start(
        self,
        model_key: str,
        device: str = "auto",
        wait_ready: bool = True,
        ready_timeout: float = 300.0,
    ) -> None:
        """Spawn the worker subprocess. If ``wait_ready`` is True, block
        until the worker emits the ``ready`` event or ``ready_timeout``
        elapses."""
        if self._proc is not None and self._proc.returncode is None:
            # Already running — caller likely meant ``reload``.
            if (
                model_key == self._model_key
                and device == self._device
                and self._state in (STATE_LOADING, STATE_READY)
            ):
                if wait_ready:
                    await self._wait_ready(ready_timeout)
                return
            await self.shutdown()

        self._model_key = model_key
        self._device = device
        self._resolved_device = None
        self._dim = None
        self._last_error = None
        self._last_progress = {}
        self._ready_event.clear()
        self._stopping = False
        self._set_state(STATE_SPAWNING)

        cmd = [
            sys.executable,
            str(self._worker_script),
            "--model-key",
            model_key,
            "--device",
            device,
            *self._extra_worker_args,
        ]
        logger.info("Spawning inference worker: model=%s device=%s", model_key, device)
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_ring.clear()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="inference-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name="inference-stderr"
        )
        self._waiter_task = asyncio.create_task(
            self._wait_exit(), name="inference-waiter"
        )
        self._set_state(STATE_LOADING)

        if wait_ready:
            await self._wait_ready(ready_timeout)

    async def _wait_ready(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"Inference worker did not become ready within {timeout:.0f}s"
            ) from e
        if self._state != STATE_READY:
            raise RuntimeError(
                f"Inference worker ended in state {self._state}: "
                f"{self._last_error or 'no error reported'}"
            )

    async def reload(self, model_key: str, device: str = "auto") -> None:
        """Terminate the current worker and spawn a new one with different
        settings. Safe to call while the client is idle or ready."""
        await self.shutdown()
        await self.start(model_key=model_key, device=device)

    async def shutdown(self) -> None:  # noqa: C901
        """Request a clean exit and wait for the worker process to die."""
        self._stopping = True
        proc = self._proc
        if proc is None:
            self._set_state(STATE_STOPPED)
            return
        if proc.returncode is None:
            try:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.write(b'{"type":"shutdown"}\n')
                    try:
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Inference worker did not exit in 10s — terminating")
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Inference worker still alive — killing")
                    proc.kill()
                    await proc.wait()
        self._proc = None
        for task in (self._reader_task, self._stderr_task, self._waiter_task):
            if task is not None and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None
        self._waiter_task = None
        self._fail_pending("worker shut down")
        self._ready_event.clear()
        self._set_state(STATE_STOPPED)

    # ------------------------------------------------------------------
    # Request / response
    # ------------------------------------------------------------------

    async def encode_text(self, text: str, timeout: float = 60.0) -> np.ndarray:
        resp = await self._request(
            {"type": "encode_text", "text": text}, timeout=timeout
        )
        return self._extract_embedding(resp)

    async def encode_image(self, path: str, timeout: float = 120.0) -> np.ndarray:
        resp = await self._request(
            {"type": "encode_image", "path": str(path)}, timeout=timeout
        )
        return self._extract_embedding(resp)

    async def encode_video(
        self, path: str, num_keyframes: int = 4, timeout: float = 180.0
    ) -> np.ndarray:
        resp = await self._request(
            {
                "type": "encode_video",
                "path": str(path),
                "num_keyframes": int(num_keyframes),
            },
            timeout=timeout,
        )
        return self._extract_embedding(resp)

    async def ping(self, timeout: float = 10.0) -> bool:
        resp = await self._request({"type": "ping"}, timeout=timeout)
        return bool(resp.get("pong"))

    async def _request(
        self,
        body: Dict[str, Any],
        *,
        timeout: float,
        ready_timeout: float = 600.0,
    ) -> Dict[str, Any]:
        if self._stopping:
            raise RuntimeError("inference client is shutting down")
        # Cold starts legitimately take tens of seconds (CLIP ViT-H-14 is
        # ~4 GB), so wait for the ready event with a generous budget that
        # is separate from the short request/response IPC timeout below.
        await self._wait_ready(timeout=ready_timeout)
        async with self._request_lock:
            proc = self._proc
            if proc is None or proc.returncode is not None or proc.stdin is None:
                raise RuntimeError("inference worker is not running")
            self._next_id += 1
            req_id = f"r{self._next_id}"
            body_with_id = {**body, "id": req_id}
            fut: asyncio.Future[Dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending[req_id] = fut
            try:
                proc.stdin.write((json.dumps(body_with_id) + "\n").encode("utf-8"))
                await proc.stdin.drain()
            except Exception:
                self._pending.pop(req_id, None)
                raise
            try:
                resp = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                raise
        if not resp.get("ok"):
            raise InferenceError(resp.get("error", "unknown worker error"))
        return resp

    @staticmethod
    def _extract_embedding(resp: Dict[str, Any]) -> np.ndarray:
        emb = resp.get("embedding")
        if not isinstance(emb, list):
            raise InferenceError("worker returned no embedding")
        return np.asarray(emb, dtype=np.float32)

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Reader / supervisor
    # ------------------------------------------------------------------

    async def _stderr_loop(self) -> None:
        """Drain the worker's stderr line by line, forwarding each line to
        the server logger and keeping the most recent N lines in memory.

        If we don't drain stderr, the kernel pipe buffer fills once the
        worker has emitted ~64 KB of logs (trivially hit during CLIP load
        because of huggingface_hub / open_clip chatter) and every further
        ``sys.stderr.write`` in the worker blocks. That freezes model
        loading and makes the client look permanently stuck."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                self._stderr_ring.append(text)
                if len(self._stderr_ring) > self._stderr_ring_max:
                    self._stderr_ring = self._stderr_ring[-self._stderr_ring_max :]
                # Use info so these are visible under the default server
                # log level, where model-load progress actually matters.
                logger.info("inference_worker: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("inference stderr drainer crashed")

    async def _reader_loop(self) -> None:  # noqa: C901
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    logger.debug("Dropping non-JSON line from worker: %r", line)
                    continue
                if "event" in msg:
                    self._handle_event(msg)
                elif "id" in msg:
                    req_id = str(msg.get("id"))
                    fut = self._pending.pop(req_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("inference reader crashed")

    def _handle_event(self, msg: Dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "loading":
            self._last_progress = {
                "stage": msg.get("stage"),
                "percent": msg.get("percent"),
            }
            for cb in list(self._on_progress_cbs):
                try:
                    cb(dict(self._last_progress))
                except Exception:
                    logger.exception("on_progress callback raised")
        elif event == "ready":
            self._resolved_device = msg.get("device")
            self._dim = int(msg.get("dim", 0)) or None
            self._last_error = None
            self._last_progress = {"stage": "ready", "percent": 1.0}
            # Observed a healthy worker — the crash streak is broken, so
            # future crashes start the respawn backoff over from the
            # shortest delay.
            self._respawn_attempts = 0
            self._ready_event.set()
            self._set_state(STATE_READY)
        elif event == "error":
            self._last_error = str(msg.get("error", "unknown error"))
            self._ready_event.set()  # unblock any pending waiters → they'll see STATE_ERROR
            self._set_state(STATE_ERROR, error=self._last_error)
        else:
            logger.debug("Unknown event from worker: %r", msg)

    async def _wait_exit(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            return
        if self._stopping:
            return
        # stderr is already being drained into ``_stderr_ring`` by
        # ``_stderr_loop``. Grab its tail for the error string so
        # diagnostics survive the process exit.
        tail = "\n".join(self._stderr_ring[-20:])
        self._last_error = f"worker exited unexpectedly (rc={rc})" + (
            f": {tail[-1000:]}" if tail else ""
        )
        logger.error(self._last_error)
        self._fail_pending(self._last_error)
        self._ready_event.set()
        self._set_state(STATE_ERROR, error=self._last_error)
        # Attempt auto-respawn unless we've exhausted backoff.
        if self._respawn_attempts < len(_RESPAWN_BACKOFF_SECONDS):
            delay = _RESPAWN_BACKOFF_SECONDS[self._respawn_attempts]
            self._respawn_attempts += 1
            logger.info(
                "Attempting inference worker respawn #%d in %.1fs",
                self._respawn_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            if self._stopping:
                return
            assert self._model_key is not None and self._device is not None
            try:
                await self.start(self._model_key, self._device, wait_ready=False)
            except Exception:
                logger.exception("Respawn attempt failed")


# Convenience alias for the server process to attach onto app.state.
__all__ = ["InferenceClient", "InferenceError"]
