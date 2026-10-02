"""Start, watch and stop the llama-server the classifier talks to.

The server is the classifier's own: a free port on 127.0.0.1, the model
GGUF without ``--mmproj`` (captions are text), and its stderr drained line by
line into the classifier log so the pipe never fills during model load.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Callable, List, Optional

import httpx

from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path

logger = logging.getLogger("caption_classifier.server")


class ServerError(RuntimeError):
    """llama-server could not be started or kept healthy."""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_command(
    model_id: str, port: int, parallel: int, ctx_per_slot: int
) -> List[str]:
    if model_id not in REGISTRY:
        raise ServerError(
            f"unknown model {model_id!r}; choose from {', '.join(sorted(REGISTRY))}"
        )
    spec = REGISTRY[model_id]
    binary = binary_path()
    gguf = get_data_dir() / "models" / "vlm" / spec.gguf_filename
    for path in (binary, gguf):
        if not path.exists():
            raise ServerError(f"missing {path}")
    return [
        str(binary),
        "--model",
        str(gguf),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--parallel",
        str(parallel),
        "--ctx-size",
        str(parallel * ctx_per_slot),
        "--n-gpu-layers",
        "99",
        *spec.extra_args,
    ]


class LlamaServer:
    def __init__(
        self, command: Callable[[int], List[str]], *, health_timeout: float = 600.0
    ) -> None:
        self._command = command
        self._health_timeout = health_timeout
        self._port: Optional[int] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._drain: Optional["asyncio.Task[None]"] = None

    @property
    def base_url(self) -> str:
        if self._port is None:
            raise ServerError("llama-server has not been started")
        return f"http://127.0.0.1:{self._port}"

    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._port = free_port()
        cmd = self._command(self._port)
        logger.info("starting llama-server: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._drain = asyncio.create_task(self._drain_stderr(self._proc))
        try:
            await self._wait_healthy()
        except BaseException:
            await self.stop()
            raise
        logger.info("llama-server ready at %s", self.base_url)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            try:
                line = await proc.stderr.readline()
            except ValueError:
                # A line longer than the stream limit; readline drops it.
                # Keep draining or the pipe fills and llama-server hangs.
                logger.debug("llama-server: <overlong stderr line dropped>")
                continue
            if not line:
                return
            logger.debug("llama-server: %s", line.decode("utf-8", "replace").rstrip())

    async def _wait_healthy(self) -> None:
        assert self._proc is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._health_timeout
        async with httpx.AsyncClient(timeout=5.0) as http:
            while True:
                if self._proc.returncode is not None:
                    raise ServerError(
                        f"llama-server exited with code {self._proc.returncode} "
                        "while loading; see the classifier log"
                    )
                try:
                    resp = await http.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                if loop.time() > deadline:
                    raise ServerError(
                        f"llama-server not healthy after {self._health_timeout:.0f}s"
                    )
                await asyncio.sleep(0.5)

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        drain, self._drain = self._drain, None
        if drain is not None:
            try:
                await asyncio.wait_for(drain, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def restart(self) -> None:
        logger.warning("restarting llama-server")
        await self.stop()
        await self.start()
