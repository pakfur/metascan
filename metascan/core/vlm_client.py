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
from pathlib import Path
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

    async def swap_model(
        self,
        new_model_id: str,
        *,
        ready_timeout: float = 300.0,
    ) -> None:
        """Tear down the current llama-server and bring up a new one.

        Caller is responsible for cancelling/draining any in-flight tagging
        jobs before calling — this method does not preserve a request queue
        across the swap.
        """
        if new_model_id == self._model_id and self._state == STATE_READY:
            return
        await self.shutdown()
        await self.start(new_model_id, wait_ready=True, ready_timeout=ready_timeout)

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
        otherwise. Mirrors InferenceClient._stderr_loop.

        Routine lines (per-request slot/health chatter, the chat-template
        dump on load, JSON metadata) go to DEBUG so a 10k-image scan doesn't
        bury the server log. Lines that look like errors are promoted to
        WARNING so they remain visible at the default level. The full ring
        buffer is still attached to crash reports by ``_wait_exit``.
        """
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
                lower = text.lower()
                if (
                    "error" in lower
                    or "failed" in lower
                    or "fatal" in lower
                    or "abort" in lower
                ):
                    logger.warning("llama-server: %s", text)
                else:
                    logger.debug("llama-server: %s", text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("vlm stderr drainer crashed")

    # NOTE: real-binary crash recovery is exercised by manual testing during
    # Phase 5/6 integration, not unit tests — the fake-server fixture uses
    # spawn_override which bypasses the subprocess we'd need to crash.
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

    # ---- Inference methods -------------------------------------------

    async def generate_tags(
        self,
        image_path: Path,
        *,
        timeout: float = 60.0,
    ) -> list[str]:
        """Tag a single image. Returns a normalized, deduped tag list.

        On parse / HTTP error returns an empty list rather than raising —
        the scan loop calls this for every image and we don't want a single
        bad response to crash the batch.
        """
        from metascan.core.vlm_prompts import (
            TAGGING_GRAMMAR,
            TAGGING_SYSTEM_PROMPT,
            TAGGING_USER_PROMPT,
            parse_tags_response,
        )

        if self._http is None or self._state != STATE_READY:
            raise RuntimeError(
                f"VlmClient not ready (state={self._state}); "
                "call ensure_started() first"
            )

        if not self.is_image_path(image_path):
            # Videos, text files, archives etc. — Qwen3-VL is image-only.
            # Returning empty here keeps the caller's success/fail accounting
            # honest: this isn't a model failure, it's an unsupported input.
            logger.debug("skipping non-image %s", image_path)
            return []

        image_b64 = await asyncio.to_thread(self._encode_image_b64, image_path)
        body = {
            "messages": [
                {"role": "system", "content": TAGGING_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TAGGING_USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                },
            ],
            "grammar": TAGGING_GRAMMAR,
            "max_tokens": 512,
            "temperature": 0.2,
        }
        try:
            r = await self._http.post(
                "/v1/chat/completions", json=body, timeout=timeout
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            # llama-server returns the real reason in the JSON body — surface
            # it so callers don't have to grep stderr for the matching
            # ``srv send_error`` line.
            detail = ""
            try:
                payload = e.response.json()
                detail = payload.get("error", {}).get("message") or str(payload)
            except Exception:
                detail = (e.response.text or "")[:300]
            logger.warning(
                "VLM tag request failed for %s: HTTP %d %s",
                image_path,
                e.response.status_code,
                detail,
            )
            return []
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("VLM tag request failed for %s: %s", image_path, e)
            return []

        tags = parse_tags_response(content)
        logger.debug("VLM tagged %s -> %d tags", image_path, len(tags))
        return tags

    # Cap the longest edge sent to the VLM. Qwen3-VL's vision encoder emits
    # roughly (W*H)/(28*28) tokens per image plane × n_deepstack_layers; a
    # 2K SDXL render at full res can exceed 8K context. 1024px keeps tagging
    # accuracy high (the model isn't reading fine print) while comfortably
    # fitting the 8192-token context budget set in ``_build_command``.
    _IMAGE_MAX_EDGE = 1024
    _IMAGE_JPEG_QUALITY = 85

    # Extensions the VLM will accept. Anything else (videos, text, archives)
    # short-circuits ``generate_tags`` with an empty result rather than
    # forwarding raw bytes to the model where they'd either 400 or worse.
    _SUPPORTED_IMAGE_EXTS = frozenset(
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tif",
            ".tiff",
            ".heic",
            ".heif",
            ".avif",
        }
    )

    @classmethod
    def is_image_path(cls, path: Path) -> bool:
        return path.suffix.lower() in cls._SUPPORTED_IMAGE_EXTS

    @classmethod
    def _encode_image_b64(cls, path: Path) -> str:
        """Resize + base64-encode an image for inline submission to llama-server.

        Reads with Pillow (so HEIC/AVIF/WebP all work via the project's
        existing decoders), downscales to ``_IMAGE_MAX_EDGE``, and re-encodes
        as JPEG. Animated/multi-frame inputs collapse to the first frame.
        """
        import base64
        import io

        from PIL import Image

        with Image.open(path) as im:
            im.load()
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            w, h = im.size
            max_edge = max(w, h)
            if max_edge > cls._IMAGE_MAX_EDGE:
                scale = cls._IMAGE_MAX_EDGE / max_edge
                im = im.resize(
                    (int(w * scale), int(h * scale)), Image.Resampling.LANCZOS
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=cls._IMAGE_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii")


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
