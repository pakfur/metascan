"""A minimal stand-in for llama-server used by VlmClient tests.

Spawned as a subprocess via ``sys.executable`` so the VlmClient under test
can manage it the same way it manages the real binary (start, /health,
SIGTERM, restart, etc.). One ephemeral port per fixture instance.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

import httpx


_FAKE_SCRIPT = textwrap.dedent(
    """
    import argparse, asyncio, sys
    from aiohttp import web

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--load-ms", type=int, default=0)
    ap.add_argument("--canned-response", type=str, default='["a","b","c"]')
    ap.add_argument("--crash-after-n", type=int, default=0)
    ap.add_argument("--health-fails-forever", action="store_true")
    args = ap.parse_args()

    state = {"ready": False, "served": 0}

    async def health(request):
        if args.health_fails_forever or not state["ready"]:
            return web.Response(status=503, text='{"status":"loading"}')
        return web.json_response({"status": "ok"})

    async def chat(request):
        await request.json()
        state["served"] += 1
        resp = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": args.canned_response},
                "finish_reason": "stop",
            }],
        }
        async def crash_later():
            await asyncio.sleep(0.05)
            sys.exit(1)
        if args.crash_after_n and state["served"] >= args.crash_after_n:
            asyncio.create_task(crash_later())
        return web.json_response(resp)

    async def main():
        if args.load_ms:
            await asyncio.sleep(args.load_ms / 1000.0)
        state["ready"] = True
        sys.stderr.write("READY\\n")
        sys.stderr.flush()
        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_post("/v1/chat/completions", chat)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", args.port)
        await site.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(main())
    """
).strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeLlamaServer:
    """Async context manager that spawns and tears down a fake llama-server."""

    def __init__(
        self,
        *,
        load_ms: int = 0,
        canned_response: str = '["a", "b", "c"]',
        crash_after_n_requests: int = 0,
        health_fails_forever: bool = False,
    ) -> None:
        self._load_ms = load_ms
        self._canned = canned_response
        self._crash_after = crash_after_n_requests
        self._health_fails = health_fails_forever
        self._port = _free_port()
        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._script_path: Optional[Path] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def port(self) -> int:
        return self._port

    def process_returncode(self) -> Optional[int]:
        if self._proc is None:
            return None
        return self._proc.poll()

    async def wait_ready(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{self.base_url}/health")
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
        raise TimeoutError("fake llama-server did not become ready")

    async def __aenter__(self) -> "FakeLlamaServer":
        fd = tempfile.NamedTemporaryFile(
            prefix="fake_llama_", suffix=".py", delete=False, mode="w"
        )
        fd.write(_FAKE_SCRIPT)
        fd.close()
        self._script_path = Path(fd.name)
        cmd = [
            sys.executable,
            str(self._script_path),
            "--port",
            str(self._port),
            "--load-ms",
            str(self._load_ms),
            "--canned-response",
            self._canned,
            "--crash-after-n",
            str(self._crash_after),
        ]
        if self._health_fails:
            cmd.append("--health-fails-forever")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        if self._script_path is not None:
            try:
                self._script_path.unlink()
            except FileNotFoundError:
                pass
