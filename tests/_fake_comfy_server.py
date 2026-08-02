"""An in-process stand-in for a ComfyUI server.

Speaks the subset of ComfyUI's API that ComfyClient uses: POST /prompt,
GET /history/{id}, GET /view, POST /upload/image, POST /queue, POST
/interrupt, and the /ws event stream.

In-process (not a subprocess like _fake_llama_server) because metascan
connects to an existing ComfyUI rather than spawning one.

Knobs:
  fail_with        -- when set, jobs emit execution_error with this text
  execution_delay  -- seconds between execution_start and completion
  images_per_job   -- how many output images each job produces
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest
from aiohttp import web
from PIL import Image


def _png_bytes(color: tuple) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeComfy:
    """A minimal ComfyUI look-alike bound to an ephemeral port."""

    def __init__(self) -> None:
        self.fail_with: Optional[str] = None
        self.execution_delay: float = 0.0
        self.images_per_job: int = 2

        # Observability for assertions.
        self.submitted: List[Dict[str, Any]] = []
        self.uploaded: List[str] = []
        self.interrupted: int = 0
        self.deleted: List[str] = []

        self._history: Dict[str, Dict[str, Any]] = {}
        self._sockets: List[web.WebSocketResponse] = []
        self._tasks: List[asyncio.Task] = []
        self._runner: Optional[web.AppRunner] = None
        self._port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/prompt", self._post_prompt)
        app.router.add_get("/history/{prompt_id}", self._get_history)
        app.router.add_get("/view", self._get_view)
        app.router.add_post("/upload/image", self._post_upload)
        app.router.add_post("/queue", self._post_queue)
        app.router.add_post("/interrupt", self._post_interrupt)
        app.router.add_get("/ws", self._ws)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_str(json.dumps(message))
            except Exception:
                pass

    # ---- routes ------------------------------------------------------

    async def _post_prompt(self, request: web.Request) -> web.Response:
        body = await request.json()
        prompt_id = str(uuid.uuid4())
        self.submitted.append({"prompt_id": prompt_id, "body": body})
        self._tasks.append(asyncio.create_task(self._execute(prompt_id, body)))
        return web.json_response({"prompt_id": prompt_id, "number": 1})

    async def _get_history(self, request: web.Request) -> web.Response:
        prompt_id = request.match_info["prompt_id"]
        entry = self._history.get(prompt_id)
        return web.json_response({prompt_id: entry} if entry else {})

    async def _get_view(self, request: web.Request) -> web.Response:
        filename = request.query.get("filename", "")
        return web.Response(
            body=_png_bytes((len(filename) * 7 % 255, 100, 150)),
            content_type="image/png",
        )

    async def _post_upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        name = getattr(field, "filename", None) or "upload.png"
        await field.read()
        self.uploaded.append(name)
        return web.json_response({"name": name, "subfolder": "", "type": "input"})

    async def _post_queue(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.deleted.extend(body.get("delete", []))
        return web.json_response({})

    async def _post_interrupt(self, request: web.Request) -> web.Response:
        self.interrupted += 1
        return web.json_response({})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._sockets.append(ws)
        try:
            async for _ in ws:
                pass
        finally:
            if ws in self._sockets:
                self._sockets.remove(ws)
        return ws

    # ---- simulated execution ----------------------------------------

    def _save_node_id(self, body: Dict[str, Any]) -> str:
        """Find the node metascan will collect outputs from."""
        graph = body.get("prompt", {})
        for node_id, node in graph.items():
            if (node.get("_meta") or {}).get("title") == "MS_SAVE":
                return str(node_id)
        return "9"

    async def _execute(self, prompt_id: str, body: Dict[str, Any]) -> None:
        await self.broadcast(
            {"type": "execution_start", "data": {"prompt_id": prompt_id}}
        )
        if self.execution_delay:
            await asyncio.sleep(self.execution_delay)

        if self.fail_with is not None:
            self._history[prompt_id] = {
                "status": {"completed": False},
                "outputs": {},
            }
            await self.broadcast(
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_type": "CheckpointLoaderSimple",
                        "exception_message": self.fail_with,
                    },
                }
            )
            return

        save_node = self._save_node_id(body)
        images = [
            {
                "filename": f"{prompt_id[:8]}_{i:05d}_.png",
                "subfolder": "",
                "type": "output",
            }
            for i in range(self.images_per_job)
        ]
        self._history[prompt_id] = {
            "status": {"completed": True},
            "outputs": {save_node: {"images": images}},
        }
        await self.broadcast(
            {
                "type": "executed",
                "data": {
                    "prompt_id": prompt_id,
                    "node": save_node,
                    "output": {"images": images},
                },
            }
        )


@pytest.fixture
async def fake_comfy() -> AsyncIterator[FakeComfy]:
    server = FakeComfy()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()
