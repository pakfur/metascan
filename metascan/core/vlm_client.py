"""Asyncio supervisor for the long-running Qwen3-VL inference server.

Spawns and manages a ``llama-server`` subprocess running an Abliterated
Qwen3-VL GGUF. Exposes typed async methods that POST to llama-server's
OpenAI-compatible ``/v1/chat/completions`` endpoint, plus lifecycle hooks
(``ensure_started``, ``swap_model``, ``shutdown``).

Mirrors :class:`metascan.core.inference_client.InferenceClient` for the
pieces that overlap. Diverges where the underlying transport differs:
llama-server speaks HTTP rather than NDJSON over stdio, so the reader-loop
is replaced by per-request HTTP calls and a periodic /health probe.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
from typing import Any, Callable, Dict, List, Optional

import httpx

from metascan.core.vlm_models import VlmModelSpec, get_spec
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path

logger = logging.getLogger(__name__)


STATE_IDLE = "idle"
STATE_SPAWNING = "spawning"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"


_RESPAWN_BACKOFF_SECONDS = (1.0, 3.0, 10.0)


StatusCb = Callable[[str, Dict[str, Any]], None]
ProgressCb = Callable[[Dict[str, Any]], None]


class VlmError(RuntimeError):
    """Raised when llama-server returns a non-2xx response or invalid body."""


def _free_port() -> int:
    """Pick an ephemeral port. The OS guarantees uniqueness inside this host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class VlmClient:
    """Supervises one llama-server subprocess. Singleton per FastAPI process."""

    def __init__(
        self,
        *,
        spawn_override: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._spawn_override = spawn_override

        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._waiter_task: Optional[asyncio.Task[None]] = None
        self._health_task: Optional[asyncio.Task[None]] = None

        self._base_url: Optional[str] = None
        self._port: Optional[int] = None
        self._model_id: Optional[str] = None
        self._spec: Optional[VlmModelSpec] = None

        self._stderr_ring: List[str] = []
        self._stderr_ring_max = 200

        self._start_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._ready_event = asyncio.Event()

        self._state = STATE_IDLE
        self._last_progress: Dict[str, Any] = {}
        self._last_error: Optional[str] = None

        self._stopping = False
        self._respawn_attempts = 0

        self._on_status_cbs: List[StatusCb] = []
        self._on_progress_cbs: List[ProgressCb] = []

        self._http: Optional[httpx.AsyncClient] = None

    # ---- Observability -------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def model_id(self) -> Optional[str]:
        return self._model_id

    @property
    def base_url(self) -> Optional[str]:
        return self._base_url

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def snapshot(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "model_id": self._model_id,
            "base_url": self._base_url,
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

    # ---- Lifecycle -----------------------------------------------------

    async def ensure_started(self, model_id: str) -> None:
        """Bring the worker to STATE_READY; idempotent.

        Holds ``_start_lock`` for the duration of the call, including any
        wait for the server to become ready. Concurrent callers serialize
        — acceptable for the singleton FastAPI pattern but worth knowing
        if a hot path could fan out: prefer one ``ensure_started`` from the
        coordinator and have other callers await the resulting state.
        """
        async with self._start_lock:
            if (
                self._state in (STATE_LOADING, STATE_READY)
                and self._model_id == model_id
            ):
                if self._state == STATE_LOADING:
                    await self._wait_ready(timeout=600.0)
                return
            await self.start(model_id, wait_ready=True)

    async def start(
        self,
        model_id: str,
        *,
        wait_ready: bool = True,
        ready_timeout: float = 300.0,
    ) -> None:
        if self._proc is not None and self._proc.poll() is None:
            if model_id == self._model_id and self._state in (
                STATE_LOADING,
                STATE_READY,
            ):
                if wait_ready:
                    await self._wait_ready(ready_timeout)
                return
            await self.shutdown()

        self._spec = get_spec(model_id)
        self._model_id = model_id
        self._last_error = None
        self._last_progress = {}
        self._ready_event.clear()
        self._stopping = False
        self._set_state(STATE_SPAWNING)

        if self._spawn_override is not None:
            self._base_url = self._spawn_override(model_id)
            self._proc = None
        else:
            self._port = _free_port()
            self._base_url = f"http://127.0.0.1:{self._port}"
            cmd = self._build_command(self._spec, self._port)
            logger.info("Spawning llama-server for %s on port %d", model_id, self._port)
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._stderr_task = asyncio.create_task(
                self._stderr_loop(), name="vlm-stderr"
            )
            self._waiter_task = asyncio.create_task(
                self._wait_exit(), name="vlm-waiter"
            )

        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)
        self._set_state(STATE_LOADING)

        # Probe /health asynchronously — flip to READY when it returns 200.
        self._health_task = asyncio.create_task(
            self._health_probe_loop(), name="vlm-health"
        )

        if wait_ready:
            await self._wait_ready(ready_timeout)

    def _build_command(self, spec: VlmModelSpec, port: int) -> List[str]:
        """Build the llama-server argv. KV-cache quant for 30B-A3B only."""
        models_dir = get_data_dir() / "models" / "vlm"
        gguf = models_dir / spec.gguf_filename
        mmproj = models_dir / spec.mmproj_filename
        cmd = [
            str(binary_path()),
            "--model",
            str(gguf),
            "--mmproj",
            str(mmproj),
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
            "--parallel",
            str(spec.parallel_slots),
            "--ctx-size",
            "8192",
            "--n-gpu-layers",
            "99",
        ]
        if spec.model_id == "qwen3vl-30b-a3b":
            cmd += ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]
        return cmd

    async def _wait_ready(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            # Cancel the background /health probe — otherwise it keeps polling
            # for up to 600s after the caller has already given up, holding
            # the httpx client open.
            if self._health_task is not None and not self._health_task.done():
                self._health_task.cancel()
            raise TimeoutError(
                f"llama-server did not become ready within {timeout:.0f}s"
            ) from e
        if self._state != STATE_READY:
            raise RuntimeError(
                f"llama-server ended in state {self._state}: "
                f"{self._last_error or 'no error reported'}"
            )

    async def _health_probe_loop(self) -> None:
        """Poll /health until it returns 200, then flip to READY."""
        assert self._http is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 600.0
        while loop.time() < deadline:
            if self._stopping:
                return
            try:
                r = await self._http.get("/health")
                if r.status_code == 200:
                    self._respawn_attempts = 0
                    self._ready_event.set()
                    self._set_state(STATE_READY)
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)

    async def shutdown(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # Wrap the blocking proc.wait() calls in run_in_executor so the
            # asyncio event loop stays free during shutdown — otherwise an
            # uncooperative llama-server can freeze the entire FastAPI server
            # for up to 5s on SIGTERM (and longer if SIGKILL is needed).
            loop = asyncio.get_running_loop()
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, proc.wait), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await loop.run_in_executor(None, proc.wait)
            except Exception:
                logger.exception("error terminating llama-server")
        self._proc = None
        for t in (self._stderr_task, self._waiter_task, self._health_task):
            if t is not None and not t.done():
                t.cancel()
        self._stderr_task = None
        self._waiter_task = None
        self._health_task = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        self._ready_event.clear()
        self._set_state(STATE_STOPPED)

    # ---- Reader / supervisor ------------------------------------------

    async def _stderr_loop(self) -> None:
        """Drain llama-server stderr — DO NOT remove. The pipe buffer fills
        within seconds during model load and the process hangs silently
        otherwise. Mirrors InferenceClient._stderr_loop."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, proc.stderr.readline)
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                self._stderr_ring.append(text)
                if len(self._stderr_ring) > self._stderr_ring_max:
                    self._stderr_ring = self._stderr_ring[-self._stderr_ring_max :]
                logger.info("llama-server: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("vlm stderr drainer crashed")

    async def _wait_exit(self) -> None:
        proc = self._proc
        if proc is None:
            return
        loop = asyncio.get_running_loop()
        try:
            rc = await loop.run_in_executor(None, proc.wait)
        except asyncio.CancelledError:
            return
        if self._stopping:
            return
        tail = "\n".join(self._stderr_ring[-20:])
        self._last_error = f"llama-server exited unexpectedly (rc={rc})" + (
            f": {tail[-1000:]}" if tail else ""
        )
        logger.error(self._last_error)
        self._ready_event.set()
        self._set_state(STATE_ERROR, error=self._last_error)


__all__ = [
    "VlmClient",
    "VlmError",
    "STATE_IDLE",
    "STATE_SPAWNING",
    "STATE_LOADING",
    "STATE_READY",
    "STATE_ERROR",
    "STATE_STOPPED",
]
