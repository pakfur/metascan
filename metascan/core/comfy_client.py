"""Asyncio driver for a ComfyUI server.

Modeled on metascan.core.inference_client.InferenceClient: constructed
once in the FastAPI lifespan, installed as a singleton, and owning all
network state for the subsystem.

This module talks to a ComfyUI that already exists — it never spawns
one. All job bookkeeping lives in the generation_jobs table so state
survives a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
import websockets

from metascan.core.comfy_bindings import (
    Bindings,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_SECONDS = (1.0, 3.0, 10.0)

JobEventCb = Callable[[str, Dict[str, Any]], None]


class ComfyError(RuntimeError):
    """A ComfyUI request failed, or was made against unusable state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        output_root: Path,
        db: Any,
        scanner: Any = None,
        in_flight: int = 2,
        request_timeout_s: float = 30.0,
        client_id: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_root = Path(output_root)
        self.db = db
        self.scanner = scanner
        self.in_flight = max(1, int(in_flight))
        self.client_id = client_id or str(uuid4())
        self._http = httpx.AsyncClient(timeout=request_timeout_s)
        # ComfyUI's prompt_id -> our generation_jobs.id. Populated at
        # dispatch and rehydrated on reconnect (Task 7). Exists so the
        # event reader never touches SQLite: ComfyUI emits `progress`
        # many times per second per job.
        self._prompt_to_job: Dict[str, int] = {}
        self.connected: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._listeners: List[JobEventCb] = []
        self._job_done: Dict[int, asyncio.Event] = {}
        self._stopping = False

    # ---- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "client_id": self.client_id,
            "in_flight": self.in_flight,
        }

    # ---- presets -----------------------------------------------------

    async def register_preset(
        self, name: str, kind: str, workflow: Dict[str, Any]
    ) -> int:
        """Resolve MS_* bindings and persist the preset.

        Raises BindingError (from comfy_bindings) before anything is
        written, so an unusable workflow never reaches the database.
        """
        bindings = resolve_bindings(workflow, kind)
        preset_id = await asyncio.to_thread(
            self.db.create_workflow_preset,
            name,
            kind,
            json.dumps(workflow),
            bindings.to_json(),
        )
        return int(preset_id)

    async def _load_preset(self, preset_id: int) -> Tuple[Dict[str, Any], Bindings]:
        row = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if row is None:
            raise ComfyError(f"No workflow preset with id {preset_id}")
        return json.loads(row["workflow_json"]), Bindings.from_json(row["bindings"])

    # ---- submission --------------------------------------------------

    async def submit_now(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
    ) -> int:
        """Bypass the queue and hand a job straight to ComfyUI.

        Returns the generation_jobs row id.
        """
        workflow, bindings = await self._load_preset(preset_id)
        graph = apply_overrides(workflow, bindings, params)

        job_id = int(
            await asyncio.to_thread(
                self.db.create_generation_job,
                preset_id,
                params.to_json(),
                panel_id,
            )
        )
        try:
            resp = await self._http.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": self.client_id},
            )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ComfyError(
                    f"ComfyUI at {self.base_url} accepted the prompt but "
                    "returned no id"
                )
        except ComfyError as exc:
            message = str(exc)
            logger.warning(message)
            await asyncio.to_thread(
                self.db.update_generation_job,
                job_id,
                state="failed",
                error=message,
                finished_at=_now(),
            )
            raise
        except Exception as exc:
            message = f"ComfyUI at {self.base_url} rejected the job: {exc}"
            logger.warning(message)
            await asyncio.to_thread(
                self.db.update_generation_job,
                job_id,
                state="failed",
                error=message,
                finished_at=_now(),
            )
            raise ComfyError(message) from exc

        self._prompt_to_job[str(prompt_id)] = job_id
        await asyncio.to_thread(
            self.db.update_generation_job,
            job_id,
            state="running",
            comfy_prompt_id=prompt_id,
            started_at=_now(),
        )
        return job_id

    # ---- event stream --------------------------------------------------

    def _ws_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse(
            (scheme, parsed.netloc, "/ws", "", f"clientId={self.client_id}", "")
        )

    async def start(self) -> None:
        """Open the persistent event socket and begin consuming events."""
        if self._ws_task is not None:
            return
        self._stopping = False
        ready = asyncio.Event()
        self._ws_task = asyncio.create_task(self._reader_loop(ready))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ready.wait(), timeout=5.0)

    async def shutdown(self) -> None:
        self._stopping = True
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
        self.connected = False
        await self.aclose()

    def on_job_event(self, cb: JobEventCb) -> None:
        self._listeners.append(cb)

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, payload)
            except Exception:
                logger.debug("comfy job-event listener raised", exc_info=True)

    async def _reader_loop(self, ready: asyncio.Event) -> None:
        """Consume ComfyUI's event stream, reconnecting on drop."""
        attempt = 0
        while not self._stopping:
            try:
                async with websockets.connect(self._ws_url()) as ws:
                    self.connected = True
                    attempt = 0
                    await self._rehydrate_prompt_map()
                    ready.set()
                    async for raw in ws:
                        try:
                            await self._handle_event(json.loads(raw))
                        except Exception:
                            logger.debug("bad comfy event: %r", raw, exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("comfy websocket dropped: %s", exc)
            finally:
                self.connected = False
                ready.set()  # never block start() on an unreachable server

            if self._stopping:
                return
            delay = _RECONNECT_BACKOFF_SECONDS[
                min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)

    async def _rehydrate_prompt_map(self) -> None:
        """Rebuild prompt_id -> job_id after a reconnect.

        A drop mid-run leaves in-flight jobs whose events we still need to
        recognise. This is the only DB read the event path ever does, and
        it happens once per connection rather than once per event.
        """
        try:
            rows = await asyncio.to_thread(
                self.db.list_generation_jobs, ["running"], 1000
            )
        except Exception:
            logger.debug("could not rehydrate comfy prompt map", exc_info=True)
            return
        for row in rows:
            prompt_id = row.get("comfy_prompt_id")
            if prompt_id:
                self._prompt_to_job.setdefault(str(prompt_id), int(row["id"]))

    def _job_id_for(self, prompt_id: Optional[str]) -> Optional[int]:
        """Resolve an event's prompt_id from memory only.

        Never hits the database: ComfyUI emits `progress` many times per
        second per job, and a SELECT per event would stall the reader on
        slow filesystems even off-thread.
        """
        if not prompt_id:
            return None
        return self._prompt_to_job.get(str(prompt_id))

    async def _resolve_job_id(self, prompt_id: Optional[str]) -> Optional[int]:
        """`_job_id_for`, with a brief in-memory retry.

        `submit_now()` only populates `_prompt_to_job` after its POST
        /prompt response resolves. A fast ComfyUI -- or, in tests, the
        in-process fake -- can broadcast `execution_start`/`executed`
        over the websocket before that continuation gets a scheduler
        turn, so the very first event for a prompt can otherwise race
        the map write. This still never touches the database; it only
        gives the already-scheduled submit_now() coroutine a few short
        chances to catch up before we treat the event as unrecognised.
        """
        if not prompt_id:
            return None
        for delay in (0.0, 0.01, 0.02, 0.05, 0.1):
            if delay:
                await asyncio.sleep(delay)
            job_id = self._job_id_for(prompt_id)
            if job_id is not None:
                return job_id
        return None

    async def _handle_event(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type")
        data = msg.get("data") or {}
        job_id = await self._resolve_job_id(data.get("prompt_id"))
        if job_id is None:
            return  # an event for someone else's client, or a stale prompt

        if kind == "progress":
            self._emit(
                "job_progress",
                {
                    "job_id": job_id,
                    "value": data.get("value"),
                    "max": data.get("max"),
                },
            )
            return

        if kind == "execution_error":
            error = "{}: {}".format(
                data.get("node_type") or "unknown node",
                data.get("exception_message") or "execution failed",
            )
            await self._finish_job(job_id, "failed", error=error)
            return

        if kind == "executed":
            await self._on_executed(job_id, data)

    async def _on_executed(self, job_id: int, data: Dict[str, Any]) -> None:
        """Overridden in Task 9 to download outputs before finishing."""
        await self._finish_job(job_id, "done")

    async def _finish_job(
        self, job_id: int, state: str, error: Optional[str] = None
    ) -> None:
        fields: Dict[str, Any] = {"state": state, "finished_at": _now()}
        if error is not None:
            fields["error"] = error
        await asyncio.to_thread(self.db.update_generation_job, job_id, **fields)
        self._emit("job_update", {"job_id": job_id, "state": state, "error": error})
        event = self._job_done.get(job_id)
        if event is not None:
            event.set()

    async def wait_for_job(self, job_id: int, timeout: float = 10.0) -> Dict[str, Any]:
        """Block until a job leaves 'running'. Returns the final row."""
        current = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if current is None:
            raise ComfyError(f"No generation job with id {job_id}")
        if current["state"] not in ("queued", "running"):
            return dict(current)

        event = self._job_done.setdefault(job_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ComfyError(f"Job {job_id} did not finish within {timeout}s") from exc
        finally:
            self._job_done.pop(job_id, None)
        return dict(await asyncio.to_thread(self.db.get_generation_job, job_id))

    # ---- history -----------------------------------------------------

    async def fetch_history(self, prompt_id: str) -> Dict[str, Any]:
        """Return ComfyUI's history entry for a prompt, or {} if absent."""
        try:
            resp = await self._http.get(f"{self.base_url}/history/{prompt_id}")
            resp.raise_for_status()
        except Exception as exc:
            raise ComfyError(
                f"Could not read history from {self.base_url}: {exc}"
            ) from exc
        payload = resp.json()
        entry = payload.get(prompt_id)
        return entry if isinstance(entry, dict) else {}


__all__ = ["ComfyClient", "ComfyError"]
