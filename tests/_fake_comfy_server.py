"""An in-process stand-in for a ComfyUI server.

Speaks the subset of ComfyUI's API that ComfyClient uses: POST /prompt,
GET /history/{id}, GET /view, POST /upload/image, POST /queue, POST
/interrupt, and the /ws event stream.

In-process (not a subprocess like _fake_llama_server) because metascan
connects to an existing ComfyUI rather than spawning one.

Fidelity notes -- these mirror the real ComfyUI (verified against
``server.py`` / ``execution.py`` / ``main.py`` in a 2026 checkout) and
each one exists because the client used to get it wrong:

* **One prompt executes at a time.** ComfyUI has a single
  ``prompt_worker``; everything else sits *pending* in the queue. A
  metascan-side ``in_flight`` of 2 therefore means one running prompt
  and one pending prompt inside ComfyUI, which is exactly the shape in
  which a mis-scoped interrupt destroys a bystander.

* **/interrupt is scoped by ``prompt_id``.** ``server.py``'s
  ``post_interrupt`` only calls ``nodes.interrupt_processing()`` when the
  posted ``prompt_id`` matches the *currently running* prompt; a body
  with no ``prompt_id`` at all is an explicit **global** interrupt that
  kills whatever happens to be running.

* **An interrupted prompt reports ``execution_interrupted``**, not
  ``execution_error`` (``execution.py::handle_execution_error`` branches
  on ``InterruptProcessingException``).

* **/history is written only at end of prompt.**
  ``PromptQueue.task_done`` records the entry *after* ``execute()``
  returns -- i.e. after every ``executed`` frame and even after
  ``execution_success``, which is emitted from inside ``execute()``. The
  only signal that is strictly ordered *after* the history write is
  ``executing`` with ``node: null``, which ``main.py`` sends right after
  ``task_done``. Reading history on the first ``executed`` frame is a
  race the client loses whenever the save node isn't the last to run.

Knobs:
  fail_with            -- when set, jobs emit execution_error with this text
  execution_delay      -- seconds of simulated work per prompt
  images_per_job       -- how many output images each job produces
  post_save_delay      -- seconds between the MS_SAVE `executed` frame and
                          end-of-prompt (models a save node that isn't last)
  trailing_output_node -- emit a second `executed` for a non-save node
                          after MS_SAVE
  hold                 -- an asyncio.Event that must be *set* before a
                          prompt's work can complete. Lets a test pin a
                          prompt in the running state for as long as it
                          needs without relying on `execution_delay`
                          out-racing the test's own steps — a wall-clock
                          margin that does not survive a loaded CI box.
                          The prompt stays interruptible while held.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

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
        self.post_save_delay: float = 0.0
        self.trailing_output_node: bool = False
        self.hold: Optional[asyncio.Event] = None
        # When True, POST /prompt is accepted (200) but the response body
        # omits "prompt_id" — exercises ComfyClient's no-prompt_id branch.
        self.omit_prompt_id: bool = False
        # When True, /ws accepts the handshake and immediately closes —
        # simulates a flapping server for reconnect-backoff tests.
        self.close_after_connect: bool = False

        # Observability for assertions.
        self.submitted: List[Dict[str, Any]] = []
        self.uploaded: List[str] = []
        # Number of POST /interrupt requests received (any shape).
        self.interrupted: int = 0
        # The prompt_id of each request, or None for a global interrupt.
        self.interrupt_requests: List[Optional[str]] = []
        # Prompts an interrupt request actually killed. A request that
        # names a prompt which isn't running adds nothing here -- which
        # is the whole point of scoping the call.
        self.interrupted_prompts: List[str] = []
        self.deleted: List[str] = []

        self._history: Dict[str, Dict[str, Any]] = {}
        self._sockets: List[web.WebSocketResponse] = []
        self._runner: Optional[web.AppRunner] = None
        self._port: int = 0

        # Single-worker execution queue, as in ComfyUI's prompt_worker.
        self._pending: Deque[Tuple[str, Dict[str, Any]]] = deque()
        self._running_prompt: Optional[str] = None
        self._interrupt_flag: bool = False
        self._wake = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def running_prompt(self) -> Optional[str]:
        return self._running_prompt

    @property
    def pending_prompts(self) -> List[str]:
        return [pid for pid, _ in self._pending]

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
        self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
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
        if self.omit_prompt_id:
            return web.json_response({"number": 1})
        self._pending.append((prompt_id, body))
        self._wake.set()
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
        to_delete = body.get("delete", [])
        self.deleted.extend(to_delete)
        # Real ComfyUI's /queue delete only removes *pending* items; a
        # running prompt is untouched (that's what /interrupt is for).
        if to_delete:
            self._pending = deque(
                (pid, b) for pid, b in self._pending if pid not in to_delete
            )
        return web.json_response({})

    async def _post_interrupt(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        prompt_id = body.get("prompt_id")
        self.interrupted += 1
        self.interrupt_requests.append(prompt_id)
        if prompt_id:
            if self._running_prompt == prompt_id:
                self._interrupt_flag = True
                self.interrupted_prompts.append(prompt_id)
        elif self._running_prompt is not None:
            # No prompt_id => global interrupt, kills whatever is running.
            self._interrupt_flag = True
            self.interrupted_prompts.append(self._running_prompt)
        return web.json_response({})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.close_after_connect:
            await ws.close()
            return ws
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

    async def _worker_loop(self) -> None:
        """One prompt at a time, exactly like ComfyUI's prompt_worker."""
        while True:
            while self._pending:
                prompt_id, body = self._pending.popleft()
                self._running_prompt = prompt_id
                self._interrupt_flag = False
                try:
                    await self._execute(prompt_id, body)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                finally:
                    self._running_prompt = None
                    self._interrupt_flag = False
            self._wake.clear()
            try:
                async with asyncio.timeout(0.05):
                    await self._wake.wait()
            except TimeoutError:
                pass

    async def _work(self, seconds: float) -> bool:
        """Simulate `seconds` of work. False if interrupted part-way.

        While `hold` is present and unset the prompt stays running
        indefinitely — still interruptible, so a test can hold a prompt
        in exactly the state it needs to observe rather than betting on
        `execution_delay` being longer than its own next few steps.
        """
        remaining = seconds
        while True:
            if self._interrupt_flag:
                return False
            if self.hold is not None and not self.hold.is_set():
                await asyncio.sleep(0.005)
                continue
            if remaining <= 0:
                return True
            step = min(0.01, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _end_of_prompt(self, prompt_id: str, entry: Dict[str, Any]) -> None:
        """task_done() then the trailing `executing {node: null}` frame.

        This ordering is the whole point: history does not exist until
        this method runs, and `executing {node: null}` is the only frame
        guaranteed to arrive after it.
        """
        self._history[prompt_id] = entry
        await self.broadcast(
            {"type": "executing", "data": {"node": None, "prompt_id": prompt_id}}
        )

    async def _execute(self, prompt_id: str, body: Dict[str, Any]) -> None:
        save_node = self._save_node_id(body)
        await self.broadcast(
            {"type": "execution_start", "data": {"prompt_id": prompt_id}}
        )
        await self.broadcast(
            {
                "type": "executing",
                "data": {
                    "node": save_node,
                    "display_node": save_node,
                    "prompt_id": prompt_id,
                },
            }
        )

        if not await self._work(self.execution_delay):
            await self._interrupted(prompt_id, save_node)
            return

        if self.fail_with is not None:
            await self.broadcast(
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_id": save_node,
                        "node_type": "CheckpointLoaderSimple",
                        "exception_message": self.fail_with,
                        "exception_type": "ValueError",
                        "traceback": [],
                        "current_inputs": {},
                        "current_outputs": [],
                    },
                }
            )
            await self._end_of_prompt(
                prompt_id, {"status": {"completed": False}, "outputs": {}}
            )
            return

        images = [
            {
                "filename": f"{prompt_id[:8]}_{i:05d}_.png",
                "subfolder": "",
                "type": "output",
            }
            for i in range(self.images_per_job)
        ]
        await self.broadcast(
            {
                "type": "executed",
                "data": {
                    "prompt_id": prompt_id,
                    "node": save_node,
                    "display_node": save_node,
                    "output": {"images": images},
                },
            }
        )

        # A workflow whose MS_SAVE is not the last node to execute keeps
        # running here -- and /history is still empty the whole time.
        if self.post_save_delay and not await self._work(self.post_save_delay):
            await self._interrupted(prompt_id, save_node)
            return
        if self.trailing_output_node:
            await self.broadcast(
                {
                    "type": "executed",
                    "data": {
                        "prompt_id": prompt_id,
                        "node": "preview",
                        "display_node": "preview",
                        "output": {"images": []},
                    },
                }
            )

        # Emitted from inside execute(), i.e. still BEFORE task_done.
        await self.broadcast(
            {"type": "execution_success", "data": {"prompt_id": prompt_id}}
        )
        await self._end_of_prompt(
            prompt_id,
            {
                "status": {"completed": True},
                "outputs": {save_node: {"images": images}},
            },
        )

    async def _interrupted(self, prompt_id: str, node_id: str) -> None:
        await self.broadcast(
            {
                "type": "execution_interrupted",
                "data": {
                    "prompt_id": prompt_id,
                    "node_id": node_id,
                    "node_type": "KSampler",
                    "executed": [],
                },
            }
        )
        await self._end_of_prompt(
            prompt_id, {"status": {"completed": False}, "outputs": {}}
        )


@pytest.fixture
async def fake_comfy() -> AsyncIterator[FakeComfy]:
    server = FakeComfy()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()
