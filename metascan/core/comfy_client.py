"""Asyncio driver for a ComfyUI server.

Modeled on metascan.core.inference_client.InferenceClient: constructed
once in the FastAPI lifespan, installed as a singleton, and owning all
network state for the subsystem.

This module talks to a ComfyUI that already exists — it never spawns
one. All job bookkeeping lives in the generation_jobs table so state
survives a restart: ``start()`` re-enqueues every ``queued`` row into the
pump and reconciles rows left at ``running`` by a previous process (see
``_rehydrate_jobs``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Set,
    Tuple,
)
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
from metascan.core.scanner import Scanner

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_SECONDS = (1.0, 3.0, 10.0)

# A connection that stayed open at least this long is trusted as "healthy"
# even if it never delivered a frame (e.g. we connected but no job was
# submitted during that window) -- see _next_backoff.
_STABLE_CONNECTION_SECONDS = 2.0

# Event kinds that structurally never carry a job we need to act on.
# Dispatched on before touching _resolve_job_id so a shared ComfyUI's
# broadcast traffic for *other* clients' jobs never pays for a lookup.
#
# NOTE: `executing` is deliberately NOT in here. ComfyUI overloads it:
# `{node: <id>}` is a per-node progress ping (many per prompt, ignorable)
# but `{node: null}` is the end-of-prompt signal, emitted by main.py
# immediately after `PromptQueue.task_done()` — the only frame guaranteed
# to arrive *after* the history entry exists. _handle_event filters the
# node-carrying variant explicitly so the fast path is preserved.
_EVENT_KINDS_WITHOUT_JOB_ID: FrozenSet[str] = frozenset(
    {"execution_start", "execution_cached", "status", "progress_state"}
)

# How long collect_outputs will wait for ComfyUI to publish a history
# entry after signalling end-of-prompt. `execution_success` is emitted
# from *inside* PromptExecutor.execute() (execution.py), while the
# history entry is only recorded by PromptQueue.task_done() after
# execute() returns — so a client that reacts to the earlier of the two
# end-of-prompt signals can legitimately arrive a few milliseconds
# early. These delays sum to ~2.2s, orders of magnitude more than that
# gap, after which an absent entry is a real failure rather than a race.
_HISTORY_POLL_DELAYS: Tuple[float, ...] = (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.5, 0.5)

JobEventCb = Callable[[str, Dict[str, Any]], None]

# States a generation_jobs row never leaves once reached. _finish_job uses
# this to refuse to move a job *out* of one of these -- the real guard
# against a stale completion (e.g. a collection task that was already
# in flight when cancel() ran) resurrecting a cancelled/failed row.
_TERMINAL_STATES: FrozenSet[str] = frozenset({"done", "failed", "cancelled"})

# Keys a ComfyUI save/output node's history entry can carry outputs
# under. "images" is the classic SaveImage/PreviewImage shape; video
# nodes (VHS_VideoCombine and friends) commonly emit their file under
# "gifs" even when it's an mp4, sometimes alongside (or instead of)
# "videos"/"video"; "audio" covers nodes that emit a separate track.
# collect_outputs scans all of them and takes every dict entry that
# carries a "filename", regardless of which key it sat under.
_OUTPUT_KEYS: Tuple[str, ...] = ("images", "gifs", "videos", "video", "audio")


class ComfyError(RuntimeError):
    """A ComfyUI request failed, or was made against unusable state."""


class PresetNotFoundError(ComfyError):
    """A request named a preset id that does not exist.

    Subclasses ComfyError so every existing `except ComfyError` handler
    keeps working, while callers that need to distinguish "bad request"
    (404) from "ComfyUI is unreachable" (503) can catch this first.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_backoff(attempt: int, got_frame: bool, survived: bool) -> Tuple[int, float]:
    """Decide the next reconnect attempt counter and delay after a drop.

    A connection only counts as healthy -- resetting the backoff to its
    shortest tier -- if it delivered at least one frame or stayed open
    past `_STABLE_CONNECTION_SECONDS`. Without this, a server that
    accepts a connection and immediately closes it (a flapping ComfyUI,
    or a proxy briefly unavailable) would have its `attempt` reset by
    every "successful" connect and loop at the 1s tier forever instead
    of ever escalating to the longer tiers.
    """
    next_attempt = 0 if (got_frame or survived) else attempt + 1
    delay = _RECONNECT_BACKOFF_SECONDS[
        min(next_attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)
    ]
    return next_attempt, delay


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
        # Count of POST /prompt calls currently awaiting ComfyUI's response.
        # _resolve_job_id only retries an unresolved prompt_id while this
        # is > 0 -- that's the only window in which a websocket event can
        # race _prompt_to_job's write. See _track_submit.
        self._submits_in_flight: int = 0
        # Metascan-side queue: submit() appends/appendlefts job ids here;
        # _pump_loop drains it into ComfyUI, never letting more than
        # `in_flight` jobs sit inside ComfyUI at once. `_running` is the
        # admission-control set the pump checks; `_pump_wake` lets submit,
        # cancel, and _finish_job all nudge the pump without polling.
        self._queue: "deque[int]" = deque()
        self._running: "set[int]" = set()
        self._pump_wake = asyncio.Event()
        self._pump_task: Optional[asyncio.Task] = None
        # Job ids cancel()/cancel_all() have flagged before _dispatch could
        # get them fully into ComfyUI. Added synchronously (no await before
        # the add) so a concurrently-running _dispatch, which checks this
        # right before and right after its POST, can never miss a
        # cancellation that lands in that window. See cancel()/_dispatch.
        self._cancelled: "set[int]" = set()
        # Output-collection tasks spawned by _on_prompt_end. A bare
        # asyncio.create_task result is only weakly referenced by the
        # event loop and can be garbage-collected mid-flight; holding a
        # strong reference here (and self-evicting once done) keeps them
        # alive, and shutdown() cancels any still outstanding.
        self._collect_tasks: "set[asyncio.Task[None]]" = set()
        # Job ids with an in-flight _complete_job task. ComfyUI signals
        # end-of-prompt twice for every prompt -- `execution_success`
        # from inside execute(), then `executing {node: null}` right
        # after task_done() -- so two frames reach _on_prompt_end for the
        # same job, microseconds apart.
        # Collection runs as a fire-and-forget task rather than an inline
        # await, so `_finish_job`'s prompt-map eviction -- which used to
        # be what made a second frame resolve to no job at all -- doesn't
        # happen until the first frame's download/ingest finishes, well
        # after a fast second frame has already resolved to this job_id.
        # `_on_prompt_end` checks this set (and _complete_job discards from
        # it in a finally) so only the first frame spawns a task.
        self._collecting: "set[int]" = set()
        # Serializes _finish_job's read-then-write of a job's state so two
        # concurrent terminal transitions for the same job_id (e.g. a
        # cancel() racing a _complete_job) can't both observe "not yet
        # terminal" and both write.
        self._finish_lock = asyncio.Lock()
        # Reference-image uploads, keyed by SHA-256 of file content (not
        # path) -> ComfyUI-side filename. Two different local paths with
        # identical bytes (e.g. a subject reused across panels) must
        # upload once and share the resolved name.
        self._upload_cache: Dict[str, str] = {}

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
            raise PresetNotFoundError(f"No workflow preset with id {preset_id}")
        # Resolve from workflow_json rather than deserializing the stored
        # bindings snapshot: a preset registered before a newer optional
        # MS_* title existed (e.g. MS_LORA_STACK) would otherwise never
        # bind the node without re-registration. The snapshot remains a
        # registration-time validation artifact.
        workflow = json.loads(row["workflow_json"])
        return workflow, resolve_bindings(workflow, row["kind"])

    # ---- submission --------------------------------------------------

    @contextlib.asynccontextmanager
    async def _track_submit(self) -> AsyncIterator[None]:
        """Mark a POST to ComfyUI as in flight, for _resolve_job_id's gate.

        Every call site that POSTs a prompt to ComfyUI must wrap that
        POST (and only the POST -- the map write right after it runs
        synchronously, with no intervening await, so it can't itself be
        raced) in this context manager: `submit_now` here, and Task 8's
        queue-drain `_dispatch` next. Anything that doesn't participate
        makes its own prompt_id resolution racy again.
        """
        self._submits_in_flight += 1
        try:
            yield
        finally:
            self._submits_in_flight -= 1

    async def submit_now(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
        output_dir: Optional[Path] = None,
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
                str(output_dir) if output_dir else None,
            )
        )
        try:
            async with self._track_submit():
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
        await self._mark_running(job_id, prompt_id)
        return job_id

    async def _mark_running(self, job_id: int, prompt_id: str) -> bool:
        """Write state="running", but never *out* of a terminal state.

        The prompt-map entry has to be published before this write (an
        `execution_start` for a fast prompt can beat a to_thread DB hop),
        which means an `execution_error` — "checkpoint not found" fires
        within milliseconds of dispatch — can resolve to this job and
        drive `_finish_job` to "failed" while this write is still on the
        worker thread. Unguarded, the "running" write then lands on top,
        leaving a permanently-running row carrying an error message and a
        finished_at, with its `_running` slot already released.

        Shares `_finish_lock` with `_finish_job` so the read-then-write
        pair here and there are serialized against each other rather than
        merely each being individually atomic. Returns False when the job
        was already terminal, in which case only `comfy_prompt_id` is
        recorded (useful for debugging, and not a state transition).
        """
        async with self._finish_lock:
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is not None and current["state"] in _TERMINAL_STATES:
                logger.debug(
                    "Job %s went terminal (%r) while its 'running' write was "
                    "in flight; not resurrecting it",
                    job_id,
                    current["state"],
                )
                await asyncio.to_thread(
                    self.db.update_generation_job,
                    job_id,
                    comfy_prompt_id=prompt_id,
                )
                return False
            await asyncio.to_thread(
                self.db.update_generation_job,
                job_id,
                state="running",
                comfy_prompt_id=prompt_id,
                started_at=_now(),
            )
            return True

    # ---- reference images ---------------------------------------------

    async def upload_file(self, path: Path) -> str:
        """Upload a local file to ComfyUI's input directory.

        Generalizes what used to be image-only reference upload: despite
        the endpoint name, ComfyUI's `/upload/image` stores whatever
        bytes it is given under its input directory, so this is also how
        a to-be-animated source image or an i2v/v2v input file reaches
        ComfyUI. MIME type is derived from the real suffix via
        `mimetypes.guess_type` (falling back to
        `application/octet-stream` for anything unrecognized); the
        uploaded name keeps that suffix so ComfyUI-side nodes that sniff
        the extension still work.

        Returns the ComfyUI-side filename for GenerationParams.ref_image.
        Cached by content hash, so a subject's reference (or any other
        repeated input) is uploaded once per run rather than once per
        panel.
        """
        path = Path(path)
        try:
            payload = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise ComfyError(f"Cannot read {path}: {exc}") from exc

        digest = hashlib.sha256(payload).hexdigest()
        cached = self._upload_cache.get(digest)
        if cached is not None:
            return cached

        suffix = path.suffix or ".png"
        upload_name = f"metascan_{digest[:16]}{suffix}"
        mime_type, _ = mimetypes.guess_type(upload_name)
        files = {
            "image": (upload_name, payload, mime_type or "application/octet-stream")
        }
        try:
            resp = await self._http.post(
                f"{self.base_url}/upload/image",
                files=files,
                data={"overwrite": "true"},
            )
            resp.raise_for_status()
        except Exception as exc:
            raise ComfyError(f"Upload to {self.base_url} failed: {exc}") from exc

        body = resp.json()
        name = body.get("name") or upload_name
        subfolder = body.get("subfolder") or ""
        resolved = f"{subfolder}/{name}" if subfolder else str(name)
        self._upload_cache[digest] = resolved
        return resolved

    # Callers unchanged: upload_image was the original (image-only) name.
    upload_image = upload_file

    async def list_loras(self) -> List[str]:
        """Lora filenames installed on the ComfyUI server, for pickers.

        Best-effort: any failure (unreachable server, unexpected payload
        shape) returns [] rather than raising -- the UI degrades to a
        free-text field.
        """
        try:
            resp = await self._http.get(f"{self.base_url}/object_info/LoraLoader")
            resp.raise_for_status()
            choices = resp.json()["LoraLoader"]["input"]["required"]["lora_name"][0]
        except Exception as exc:
            logger.warning("list_loras: cannot query %s: %s", self.base_url, exc)
            return []
        if not isinstance(choices, list):
            return []
        return [str(c) for c in choices]

    # ---- queue ---------------------------------------------------------

    def queue_depth(self) -> int:
        return len(self._queue)

    async def submit(
        self,
        preset_id: int,
        params: GenerationParams,
        panel_id: Optional[int] = None,
        priority: bool = False,
        output_dir: Optional[Path] = None,
        beat_id: Optional[int] = None,
    ) -> int:
        """Enqueue a job. Returns its id immediately; it reaches ComfyUI
        when a slot frees up.

        The preset is validated up front -- both that it exists
        (PresetNotFoundError) and that `params` binds cleanly against it
        (BindingError, e.g. a negative prompt supplied against a workflow
        with no MS_NEGATIVE node) -- so a bad request fails synchronously
        at the call site with an accurate error, rather than reaching
        `_dispatch` on the background pump and failing later with a
        message that blames ComfyUI for a purely local validation
        problem. `apply_overrides`'s result is discarded here; `_dispatch`
        re-derives the graph from the same preset+params when the job
        actually reaches the front of the queue, so this is cheap and
        changes no happy-path behaviour.
        """
        workflow, bindings = await self._load_preset(preset_id)
        apply_overrides(workflow, bindings, params)
        job_id = int(
            await asyncio.to_thread(
                self.db.create_generation_job,
                preset_id,
                params.to_json(),
                panel_id,
                str(output_dir) if output_dir else None,
                beat_id,
            )
        )
        if priority:
            self._queue.appendleft(job_id)
        else:
            self._queue.append(job_id)
        self._pump_wake.set()
        self._emit("job_update", {"job_id": job_id, "state": "queued", "error": None})
        return job_id

    async def _pump_loop(self) -> None:
        """Keep at most ``in_flight`` jobs inside ComfyUI."""
        while True:
            try:
                while self._queue and len(self._running) < self.in_flight:
                    job_id = self._queue.popleft()
                    # Added to _running in the same synchronous step as the
                    # popleft above -- no await between them -- so a job id
                    # is never observably absent from *both* _queue and
                    # _running at once. cancel_all() relies on that: it
                    # reads _queue then _running with nothing awaited in
                    # between, so every outstanding job is caught by one
                    # collection or the other, never dropped in the gap.
                    self._running.add(job_id)
                    job = await asyncio.to_thread(self.db.get_generation_job, job_id)
                    if job is None or job["state"] != "queued":
                        self._running.discard(job_id)
                        continue  # cancelled while waiting
                    await self._dispatch(job_id, job)
                self._pump_wake.clear()
                try:
                    async with asyncio.timeout(0.5):  # 3.11+, uncancel-aware
                        await self._pump_wake.wait()
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("comfy pump loop error")
                await asyncio.sleep(0.5)

    async def _dispatch(self, job_id: int, job: Dict[str, Any]) -> None:
        """Hand one queued job to ComfyUI. Caller has already added
        job_id to `_running`.

        Mirrors submit_now's POST handling, including wrapping the POST
        in _track_submit() -- without it, a queued job's prompt_id can be
        resolved by the reader loop before this method's map write runs,
        and the resulting event gets silently dropped with no retry.

        Checks `_cancelled` twice: once before the POST (a cancel() that
        landed while we were still loading the preset -- a to_thread DB
        read, 50-100ms+ on WSL2 /mnt -- must stop this job from ever
        reaching ComfyUI), and once after (a cancel() that landed while
        the POST itself was in flight; the job already reached ComfyUI by
        then, so we undo it there instead of letting it run to
        completion). Without both checks, cancel()'s "queued" branch can
        lose this race silently: it finds the job already popped from
        `_queue`, marks the DB row cancelled, and returns -- while this
        method, unaware, still POSTs the job and overwrites the row back
        to "running".
        """
        try:
            workflow, bindings = await self._load_preset(job["preset_id"])
            graph = apply_overrides(
                workflow, bindings, GenerationParams.from_json(job["params"])
            )
            if job_id in self._cancelled:
                self._cancelled.discard(job_id)
                await self._finish_job(job_id, "cancelled")
                return
            async with self._track_submit():
                resp = await self._http.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": graph, "client_id": self.client_id},
                )
            resp.raise_for_status()
            prompt_id = resp.json().get("prompt_id")
            if not prompt_id:
                raise ComfyError("ComfyUI returned no prompt_id")
            # Register in the map BEFORE the DB write: ComfyUI can emit
            # execution_start for this prompt while the write is still on
            # the worker thread, and an event that arrives before the map
            # entry exists is dropped as unrecognised.
            self._prompt_to_job[str(prompt_id)] = job_id
            if not await self._mark_running(job_id, prompt_id):
                # An execution_error (or a cancel) already took this job
                # terminal while the "running" write was in flight. The
                # row is correct as it stands; just stop owning it.
                self._cancelled.discard(job_id)
                self._prompt_to_job.pop(str(prompt_id), None)
                return
            if job_id in self._cancelled:
                self._cancelled.discard(job_id)
                await self._stop_prompt(prompt_id)
                self._prompt_to_job.pop(str(prompt_id), None)
                await self._finish_job(job_id, "cancelled")
                return
            self._emit(
                "job_update", {"job_id": job_id, "state": "running", "error": None}
            )
        except Exception as exc:
            message = f"ComfyUI at {self.base_url} rejected the job: {exc}"
            logger.warning(message)
            self._cancelled.discard(job_id)
            await self._finish_job(job_id, "failed", error=message)

    async def _stop_prompt(self, prompt_id: Optional[str]) -> None:
        """Remove one prompt from ComfyUI, wherever it currently sits.

        `/queue {delete: [...]}` drops it if it is still *pending*;
        `/interrupt {prompt_id: ...}` kills it if it is *running*.

        The prompt_id in the interrupt body is load-bearing, not
        decorative. ComfyUI's `post_interrupt` (server.py) treats a body
        with no `prompt_id` as an explicit **global** interrupt -- it
        logs "Global interrupt (no prompt_id specified)" and calls
        `nodes.interrupt_processing()` on whatever prompt happens to be
        executing. Since ComfyUI runs one prompt at a time and metascan
        keeps `in_flight` (default 2) of them there, the job a user
        cancels is routinely the *pending* one while a sibling is
        mid-generation: a bodyless interrupt destroyed that sibling's
        work every time. With the id present, ComfyUI no-ops unless the
        named prompt is the running one. Older ComfyUI builds that
        predate the scoping simply ignore the unknown key and behave as
        before, so this is backwards-safe.
        """
        if not prompt_id:
            return
        try:
            await self._http.post(
                f"{self.base_url}/queue", json={"delete": [prompt_id]}
            )
            await self._http.post(
                f"{self.base_url}/interrupt", json={"prompt_id": prompt_id}
            )
        except Exception as exc:
            logger.warning("Could not interrupt ComfyUI: %s", exc)

    async def cancel(self, job_id: int) -> None:
        """Cancel a queued or running job.

        Queued jobs are simply dropped. A running job is interrupted in
        ComfyUI; already-generated images from earlier jobs are kept.

        `_cancelled.add(job_id)` is the very first statement -- before
        any `await` -- so it lands atomically with respect to any other
        coroutine, including a `_dispatch` that has already popped this
        job out of `_queue` but hasn't yet reached (or finished) its POST.
        See `_dispatch` for the other half of this handshake.
        """
        self._cancelled.add(job_id)
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None or job["state"] not in ("queued", "running"):
            self._cancelled.discard(job_id)
            return

        if job["state"] == "queued":
            try:
                self._queue.remove(job_id)
            except ValueError:
                if job_id in self._running:
                    # Already popped by _pump_loop: a _dispatch for this
                    # job is in flight (or about to be). Leave _cancelled
                    # set -- _dispatch will discover it and finish the job
                    # itself. Finishing it here too would race _dispatch's
                    # own write.
                    return
                # Not in _queue and not in _running: nothing in this
                # process owns the row. That's a job orphaned by a
                # restart (or one already drained by cancel_all). It has
                # never reached ComfyUI, so finishing it here is the only
                # way it can ever leave "queued" -- previously this path
                # returned silently, leaving the row queued forever while
                # POST /jobs/{id}/cancel reported success.
                self._cancelled.discard(job_id)
                await self._finish_job(job_id, "cancelled")
                return
            self._cancelled.discard(job_id)
            await self._finish_job(job_id, "cancelled")
            return

        # job["state"] == "running": _dispatch already completed for this
        # job (it only writes "running" after the POST resolves), so
        # there's no in-flight _dispatch left to observe _cancelled.
        self._cancelled.discard(job_id)
        prompt_id = job["comfy_prompt_id"]
        await self._stop_prompt(prompt_id)
        if prompt_id:
            self._prompt_to_job.pop(str(prompt_id), None)
        await self._finish_job(job_id, "cancelled")

    async def cancel_all(self) -> None:
        """Drop every queued job and interrupt anything running.

        Not every job is resolved by the time this returns: a job whose
        `_dispatch` is parked mid-POST is left flagged in `_cancelled`
        and reaches its terminal state slightly later, when `_dispatch`
        observes the flag (see the handshake in `cancel`/`_dispatch`).
        Everything else -- queued rows this process owns, and jobs
        already running in ComfyUI -- is finished before returning.
        """
        queued = list(self._queue)
        self._queue.clear()
        for job_id in queued:
            await self._finish_job(job_id, "cancelled")
        for job_id in list(self._running):
            await self.cancel(job_id)

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
        await self._rehydrate_jobs()
        ready = asyncio.Event()
        self._ws_task = asyncio.create_task(self._reader_loop(ready))
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(self._pump_loop())
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ready.wait(), timeout=5.0)

    async def shutdown(self) -> None:
        self._stopping = True
        collect_tasks = list(self._collect_tasks)
        for task in collect_tasks:
            task.cancel()
        if collect_tasks:
            # Wait for cancellation to actually be delivered before
            # clearing -- clearing immediately would drop the strong
            # references the set exists to hold in the first place,
            # before the cancel has landed.
            await asyncio.gather(*collect_tasks, return_exceptions=True)
        self._collect_tasks.clear()
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
            self._pump_task = None
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
            connected_at: Optional[float] = None
            got_frame = False
            try:
                async with websockets.connect(self._ws_url()) as ws:
                    self.connected = True
                    connected_at = asyncio.get_running_loop().time()
                    await self._rehydrate_prompt_map()
                    ready.set()
                    async for raw in ws:
                        got_frame = True
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
            survived = (
                connected_at is not None
                and (asyncio.get_running_loop().time() - connected_at)
                >= _STABLE_CONNECTION_SECONDS
            )
            attempt, delay = _next_backoff(attempt, got_frame, survived)
            await asyncio.sleep(delay)

    async def _rehydrate_jobs(self) -> None:
        """Reconcile the generation_jobs table with a fresh process.

        Runs exactly once, from `start()`, before the reader and pump
        tasks exist. Two halves:

        * Rows left at "running" belonged to a process that is gone. No
          event will ever arrive for them, so they would sit "running"
          forever *and* — worse — a naive re-adoption would let them
          over-subscribe `in_flight`. We cannot reconcile against
          ComfyUI reliably (its history is capped and a restart of
          ComfyUI itself loses the queue), so they are marked failed with
          an explicit reason. Honest, and it frees the slot.

        * Rows still at "queued" are re-enqueued into `_queue` in id
          order so the pump picks them up. Without this the module's own
          promise that "state survives a restart" was false: a restart
          with queued work left it queued forever, invisible to the pump.

        Deliberately NOT done on websocket reconnect — mid-session,
        "running" rows are live jobs whose events are still coming, and
        `_rehydrate_prompt_map` exists to re-adopt exactly those.
        """
        try:
            stale = await asyncio.to_thread(
                self.db.list_generation_jobs, ["running"], 10000
            )
        except Exception:
            logger.debug("could not read running comfy jobs", exc_info=True)
            stale = []
        for row in stale:
            await self._finish_job(
                int(row["id"]),
                "failed",
                error="Interrupted by a metascan restart while running; "
                "resubmit it to try again.",
            )
        if stale:
            logger.info(
                "Marked %d in-flight ComfyUI job(s) failed after a restart",
                len(stale),
            )

        try:
            pending = await asyncio.to_thread(
                self.db.list_generation_jobs, ["queued"], 10000
            )
        except Exception:
            logger.debug("could not read queued comfy jobs", exc_info=True)
            return
        requeued = 0
        for row in pending:
            job_id = int(row["id"])
            if job_id in self._queue or job_id in self._running:
                continue
            self._queue.append(job_id)
            requeued += 1
        if requeued:
            logger.info(
                "Re-enqueued %d queued ComfyUI job(s) after a restart", requeued
            )
            self._pump_wake.set()

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
        """`_job_id_for`, with a brief in-memory retry while a submit races.

        `submit_now()` (and Task 8's `_dispatch`) only populate
        `_prompt_to_job` after their POST /prompt response resolves. A
        fast ComfyUI -- or, in tests, the in-process fake -- can
        broadcast `execution_start`/`executed` over the websocket before
        that continuation gets a scheduler turn, so the very first event
        for a prompt can otherwise race the map write.

        The retry is gated on `_submits_in_flight`: with nothing in
        flight this is a single dict lookup and returns immediately, so
        a shared ComfyUI's traffic for another client's job never pays a
        sleep. It only ever reads `_prompt_to_job` -- never the database.
        """
        if not prompt_id:
            return None
        job_id = self._job_id_for(prompt_id)
        if job_id is not None:
            return job_id
        for delay in (0.01, 0.02, 0.05, 0.1):
            if self._submits_in_flight <= 0:
                return None
            await asyncio.sleep(delay)
            job_id = self._job_id_for(prompt_id)
            if job_id is not None:
                return job_id
        return None

    async def _handle_event(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind in _EVENT_KINDS_WITHOUT_JOB_ID:
            return  # never carries a job to resolve; skip before any lookup
        data = msg.get("data") or {}
        if kind == "executing" and data.get("node") is not None:
            # Per-node progress ping, many per prompt. Only the
            # `{node: null}` variant is end-of-prompt (see
            # _EVENT_KINDS_WITHOUT_JOB_ID); bail before any lookup so the
            # common case stays as cheap as it was.
            return
        if kind == "executed":
            # One per output-producing node, emitted from *inside*
            # execute() while the prompt is still running. Informational
            # only: /history does not exist yet (see _on_prompt_end).
            return
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

        if kind == "execution_interrupted":
            # ComfyUI reports an *interrupted* prompt with this, not with
            # execution_error (execution.py::handle_execution_error
            # branches on InterruptProcessingException). Without a branch
            # here the job never reaches a terminal state: its row stays
            # "running" and its id stays in `_running` forever, so a
            # couple of incidents permanently stall the pump. The
            # terminal-state guard in `_finish_job` makes this a no-op
            # for jobs metascan cancelled itself.
            await self._finish_job(job_id, "cancelled")
            return

        if kind == "execution_success" or kind == "executing":
            # Both are end-of-prompt signals (`executing` has already
            # been narrowed to node=None above). Whichever lands first
            # starts collection; `_collecting` makes the other a no-op.
            await self._on_prompt_end(job_id, data)

    def output_dir_for(self, job_id: int) -> Path:
        """Where a job's images land. Flat per-job in Phase A; Phase B
        overrides this with a storyboard/scene/panel tree."""
        return self.output_root / f"job_{job_id:06d}"

    async def _on_prompt_end(self, job_id: int, data: Dict[str, Any]) -> None:
        """Kick off output collection; the job stays 'running' until the
        files are on disk and ingested.

        Driven by ComfyUI's *end-of-prompt* signals — `execution_success`
        and `executing {node: null}` — never by `executed`. `executed`
        fires per output-producing node from inside `execute()`, while
        the history entry that collection reads is only written by
        `PromptQueue.task_done()` after the whole prompt finishes. Any
        workflow whose MS_SAVE is not the last node to execute (a second
        output node, a preview or upscale branch continuing after the
        save) therefore lost its images deterministically: the read
        returned {}, which flowed to `files: []` and `_finish_job(...,
        "done")` with no retry and no error.

        Spawned as a task rather than awaited so downloading one job's
        images never blocks the reader from seeing another job's events.
        Guarded by `_collecting` (see __init__) so the second
        end-of-prompt frame — both signals arrive for every prompt, back
        to back — is a no-op rather than a second, concurrent collection
        of the same images. The task is held in `_collect_tasks` (see
        __init__) so it isn't garbage-collected mid-flight, and
        self-evicts (via `add_done_callback`) once done.
        """
        if job_id in self._collecting:
            return
        self._collecting.add(job_id)
        prompt_id = str(data.get("prompt_id") or "")
        task = asyncio.create_task(self._complete_job(job_id, prompt_id))
        self._collect_tasks.add(task)
        task.add_done_callback(self._collect_tasks.discard)

    async def _complete_job(self, job_id: int, prompt_id: str) -> None:
        """Download, write, and ingest one job's images, then finish it.

        The `_cancelled` checks below are a cheap, harmless early-out for
        the common case, but they are NOT what makes cancellation safe:
        `cancel()`'s running-job branch discards `job_id` from
        `_cancelled` *before* it actually interrupts ComfyUI or writes
        "cancelled" -- by the time a cancel has taken effect the flag is
        already gone, so a check against it here can miss the exact
        window it exists to catch. The real guards are (1)
        `collect_outputs` bailing when the job is no longer
        queued/running, both up front and again around each image, and
        (2) `_finish_job` refusing to move a job out of a terminal state.
        Between those two, a cancel that lands at any point -- before
        collection starts, mid-download, or after every image already
        landed -- can neither trigger further downloads/ingests nor
        resurrect the row.
        """
        try:
            if job_id in self._cancelled:
                return
            try:
                files = await self.collect_outputs(job_id, prompt_id)
            except Exception as exc:
                logger.warning("Output collection failed for job %s: %s", job_id, exc)
                if job_id in self._cancelled:
                    return
                await self._finish_job(job_id, "failed", error=str(exc))
                return
            if job_id in self._cancelled:
                return
            # `collect_outputs` returns [] both for "the job produced no
            # images" and for "the job went terminal, so I bailed". Only
            # the first is this job's output; publishing the second would
            # announce an empty result set for a job that was cancelled.
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is not None and current["state"] in _TERMINAL_STATES:
                return
            self._emit(
                "job_outputs", {"job_id": job_id, "files": [str(f) for f in files]}
            )
            await self._finish_job(job_id, "done")
        finally:
            self._collecting.discard(job_id)

    async def _download_image(self, entry: Dict[str, Any], target: Path) -> None:
        """Fetch one output-node entry via `/view` and write it to disk.

        Filename-agnostic: the entry's `filename`/`subfolder`/`type`
        triplet is all `/view` needs, whether it names an image, a
        video, or an audio file, so this one implementation covers every
        key `collect_outputs` scans. Kept under this name -- rather than
        the more accurate `_download_output` -- because
        `tests/test_comfy_client.py` monkeypatches it by name;
        `_download_output` below is an alias for new call sites.
        """
        resp = await self._http.get(
            f"{self.base_url}/view",
            params={
                "filename": entry.get("filename", ""),
                "subfolder": entry.get("subfolder", ""),
                "type": entry.get("type", "output"),
            },
        )
        resp.raise_for_status()
        # Multi-MB files (PNGs, but now also video) onto a WSL2 /mnt
        # output root are exactly the stall class this module goes out
        # of its way to keep off the event loop everywhere else.
        await asyncio.to_thread(target.write_bytes, resp.content)

    _download_output = _download_image

    async def collect_outputs(self, job_id: int, prompt_id: str) -> List[Path]:
        """Fetch, persist, and ingest every output file the job produced.

        Files are pulled over HTTP rather than read from ComfyUI's output
        directory so a remote or containerized ComfyUI works unchanged.
        Reads from `/history` (not the websocket event payload): ComfyUI
        emits one `executed` per output-producing node, not one per
        prompt, so trusting the event payload would lose images for a
        workflow with more than one output node (see `_on_prompt_end`'s
        `_collecting` guard for the other half of that same fact).

        The save node's history entry is scanned across every key in
        `_OUTPUT_KEYS` (`images`, `gifs`, `videos`, `video`, `audio`) --
        video-combine nodes commonly emit an mp4 under `gifs` -- and
        every dict entry carrying a `filename`, under any of those keys,
        is downloaded. A downloaded file whose suffix isn't one
        `Scanner` recognizes (e.g. a sidecar `.json` some video nodes
        emit alongside the media file) is still written to disk -- so
        nothing is silently lost -- but is excluded from both the
        ingested set and the returned/`job_outputs` list, and logged at
        INFO by name.

        Bails immediately if the job is no longer queued/running (a
        cancel that completed before this call even started), and
        re-checks around every file inside the loop -- both before
        starting its download and again right after -- so a cancel
        landing mid-batch stops further downloads/ingests instead of
        finishing the batch, and doesn't ingest the one file that was
        already in flight when the cancel landed either. A file that was
        already written to disk before the cancel took effect is left in
        place (harmless orphan): a retry re-downloads over it.
        """
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None:
            raise ComfyError(f"No generation job with id {job_id}")
        if job["state"] not in ("queued", "running"):
            return []

        entry = await self._await_history(prompt_id)
        _, bindings = await self._load_preset(job["preset_id"])
        node_output = (entry.get("outputs") or {}).get(bindings.save) or {}
        # A video node can legitimately list the same file under more
        # than one key (e.g. VHS_VideoCombine's mp4 under both "gifs"
        # and "videos"); de-dupe on the (filename, subfolder, type)
        # triplet -- the same identity /view resolves the file by -- so
        # it's downloaded, ingested, and reported exactly once.
        entries: List[Dict[str, Any]] = []
        seen: Set[Tuple[Any, Any, Any]] = set()
        for key in _OUTPUT_KEYS:
            for item in node_output.get(key) or []:
                if not isinstance(item, dict) or not item.get("filename"):
                    continue
                identity = (
                    item.get("filename"),
                    item.get("subfolder", ""),
                    item.get("type", "output"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                entries.append(item)

        stored = job.get("output_dir")
        target_dir = Path(stored) if stored else self.output_dir_for(job_id)
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

        written: List[Path] = []
        for entry_item in entries:
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is None or current["state"] not in ("queued", "running"):
                break
            # `filename` comes straight off the wire. Joining it onto
            # target_dir unvalidated let a name containing `../` write
            # outside output_root entirely; Path(...).name keeps only the
            # final component, which is all ComfyUI ever legitimately
            # sends (`subfolder` carries the rest, and we don't use it
            # for the local layout).
            name = Path(str(entry_item.get("filename") or "")).name
            if not name or name in (".", ".."):
                continue
            target = target_dir / name
            await self._download_image(entry_item, target)
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is None or current["state"] not in ("queued", "running"):
                # Cancelled while this file's download was in flight --
                # the bytes may already be on disk, but they must not be
                # ingested or counted as this job's output.
                break
            if target.suffix.lower() not in Scanner.SUPPORTED_EXTENSIONS:
                logger.info(
                    "collect_outputs: downloaded unsupported output %s for "
                    "job %s; excluded from ingest",
                    target.name,
                    job_id,
                )
                continue
            written.append(target)
            if self.scanner is not None:
                try:
                    await asyncio.to_thread(self.scanner.ingest_file, target)
                except Exception as exc:
                    # A file that fails to ingest is still on disk and still
                    # reported; losing the whole job over it would be worse.
                    logger.warning("Could not ingest %s: %s", target, exc)
        return written

    async def _finish_job(
        self, job_id: int, state: str, error: Optional[str] = None
    ) -> None:
        # Release the pump's admission-control slot as soon as a job goes
        # terminal, regardless of which caller (executed/error event,
        # cancel, cancel_all, or a dispatch failure) got it here.
        self._running.discard(job_id)
        # The real anti-resurrection guard: a job already in a terminal
        # state never moves to a different one. Without this, a stale
        # completion -- e.g. a _complete_job task that was already
        # in-flight when cancel() ran and finished the job first -- can
        # silently overwrite "cancelled"/"failed" back to "done". Locked
        # so two concurrent _finish_job calls for the same job_id can't
        # both read "not yet terminal" and both write.
        async with self._finish_lock:
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is not None and current["state"] in _TERMINAL_STATES:
                logger.debug(
                    "Ignoring _finish_job(%s, %r): already terminal at %r",
                    job_id,
                    state,
                    current["state"],
                )
            else:
                fields: Dict[str, Any] = {"state": state, "finished_at": _now()}
                if error is not None:
                    fields["error"] = error
                await asyncio.to_thread(self.db.update_generation_job, job_id, **fields)
                self._emit(
                    "job_update", {"job_id": job_id, "state": state, "error": error}
                )
        # Evict the reverse-mapping entry now that the job is terminal --
        # otherwise _prompt_to_job grows by one entry per job for the
        # life of the process once Task 8's queue is submitting steadily.
        # Safe to do even when the write above was skipped: the job was
        # already terminal, so the entry (if any) should already be gone,
        # and this is a no-op in that case.
        stale = [pid for pid, jid in self._prompt_to_job.items() if jid == job_id]
        for pid in stale:
            del self._prompt_to_job[pid]
        event = self._job_done.get(job_id)
        if event is not None:
            event.set()
        # Wake the pump: a slot may have just freed up, or a queued job
        # was just dropped by cancel/cancel_all.
        self._pump_wake.set()

    async def wait_for_job(self, job_id: int, timeout: float = 10.0) -> Dict[str, Any]:
        """Block until a job leaves 'running'. Returns the final row.

        Registers the completion Event *before* reading current state:
        `_finish_job` can run in the gap between a plain DB read and a
        `setdefault` placed after it, in which case that later
        `setdefault` never observes the event it just missed and the
        caller blocks for the full `timeout` on a job that is already
        done. Registering first means any `_finish_job` that runs during
        or after the read is guaranteed to find and set this Event.
        """
        event = self._job_done.setdefault(job_id, asyncio.Event())
        try:
            current = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if current is None:
                raise ComfyError(f"No generation job with id {job_id}")
            if current["state"] not in ("queued", "running"):
                return dict(current)

            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ComfyError(
                    f"Job {job_id} did not finish within {timeout}s"
                ) from exc
            return dict(await asyncio.to_thread(self.db.get_generation_job, job_id))
        finally:
            self._job_done.pop(job_id, None)

    # ---- history -----------------------------------------------------

    async def _await_history(self, prompt_id: str) -> Dict[str, Any]:
        """`fetch_history`, retried briefly, and a hard error if empty.

        Two things this buys over a bare `fetch_history`:

        * It absorbs the genuine microsecond-scale race. ComfyUI emits
          `execution_success` from inside `PromptExecutor.execute()`, but
          the history entry is only recorded by `PromptQueue.task_done()`
          after `execute()` returns. Reacting to the earlier of the two
          end-of-prompt signals can land just before the write.

        * It refuses to treat a permanently-absent entry as success. The
          old code let `{}` flow through to `images = []`, `files: []`
          and `state="done"` — a job that silently produced nothing, with
          no retry, no error, and no signal to the user anywhere.
        """
        entry = await self.fetch_history(prompt_id)
        if entry:
            return entry
        for delay in _HISTORY_POLL_DELAYS:
            await asyncio.sleep(delay)
            entry = await self.fetch_history(prompt_id)
            if entry:
                return entry
        raise ComfyError(
            f"ComfyUI at {self.base_url} signalled that prompt {prompt_id} "
            "finished, but its /history entry never appeared"
        )

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


__all__ = ["ComfyClient", "ComfyError", "PresetNotFoundError"]
