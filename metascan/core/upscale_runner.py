"""Runs of a ComfyUI upscale workflow over one media item.

Mirrors ``I2vRunner``'s run: a server-owned task, a window of unfinished jobs,
one terminal ``batch_*`` frame. Nothing is written by a VLM: a prompt, negative,
LoRAs, model and denoise come from the dialog as they are, so a run is one
upload and ``count`` submissions.

**The source is always the original.** A file that has been upscaled is read
from the Original folder, so upscaling again never stacks on a result.

**Outputs are candidates, not media.** ``ComfyClient`` writes them under the
staging folder and does not ingest them; this module records each one in
``upscale_candidates``. Everything a candidate row needs is read from the job
row, so a job that finishes across a restart still gives a candidate: the
post-subscribe sweep (:meth:`UpscaleRunner.recover_finished`) picks up what
landed while nobody was listening.

Pause is the generation gate: ``wait_unpaused`` before the upload and before
each submit, and ``is_writing`` is true during an upload so a pause waits out
a large video instead of reporting paused mid-transfer."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set, Tuple

from metascan.core.comfy_bindings import GenerationParams, resolve_bindings
from metascan.core.i2v_runner import I2vRequestError, plan_i2v_seeds, render_seconds
from metascan.utils.ffmpeg_utils import probe_with_timeout
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]

CHANNEL = "upscale"
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}
TERMINAL_STATES = ("done", "failed", "cancelled")
FINISHED_KEEP = 64


class UpscaleRequestError(ValueError):
    """A request that cannot run: the route answers 400."""


class UpscaleNotFoundError(LookupError):
    """A run id this runner never saw."""


async def _nothing_to_wait_for() -> None:
    return None


def _no_prompting() -> bool:
    return False


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def kind_for(path: Path) -> str:
    return "upscale_video" if is_video(path) else "upscale_image"


def _has_audio(path: Path) -> Optional[bool]:
    """None when the file cannot be probed: unknown is not "lost"."""
    probe = probe_with_timeout(str(path))
    if not probe:
        return None
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


@dataclass
class UpscaleRunRequest:
    source_path: str  # stored POSIX
    preset_id: int
    count: int = 1
    seed: Optional[int] = None
    seed_policy: str = "random"
    prompt: Optional[str] = None
    scale: Optional[float] = None
    negative: Optional[str] = None
    # Metascan owns an MS_LORA_STACK node: None and [] both leave it empty.
    loras: Optional[List[Dict[str, Any]]] = None
    denoise: Optional[float] = None
    model: Optional[str] = None


@dataclass
class UpscaleStarted:
    batch_id: str
    total: int
    next_seed: Optional[int]


@dataclass
class _Run:
    id: str
    req: UpscaleRunRequest
    source_file: Path
    kind: str
    seeds: List[Optional[int]]
    window: "asyncio.Semaphore"
    phase: str = "uploading"
    submitted: int = 0
    pending: Set[int] = field(default_factory=set)
    all_submitted: bool = False
    all_jobs_ended: "asyncio.Event" = field(default_factory=asyncio.Event)
    task: "Optional[asyncio.Task[None]]" = None
    finished: Optional[str] = None


class UpscaleRunner:
    def __init__(
        self,
        db: Any,
        comfy: Any,
        staging_root: Callable[[], Path],
        technical: Callable[[Path], Dict[str, Any]],
        supported: Iterable[str],
        get_all_vlms: Optional[Callable[[], Iterable[Any]]] = None,
        unload_vlm_during_generation: bool = True,
        purge_stale: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.staging_root = staging_root
        self.technical = technical
        self.supported = {s.lower() for s in supported}
        self.get_all_vlms = get_all_vlms
        self.unload_vlm_during_generation = unload_vlm_during_generation
        self.purge_stale = purge_stale
        # Set by UpscaleProcess, and in the lifespan.
        self.wait_unpaused: Callable[[], Awaitable[None]] = _nothing_to_wait_for
        self.prompting_elsewhere: Callable[[], bool] = _no_prompting
        self._on_event: List[EventCb] = []
        self._runs: Dict[str, _Run] = {}
        self._finished: Dict[str, None] = {}
        self._job_run: Dict[int, str] = {}
        self._early_ended: Dict[int, str] = {}
        self._uploading = 0
        self._record_tasks: Set["asyncio.Task[None]"] = set()

    def on_event(self, cb: EventCb) -> None:
        self._on_event.append(cb)

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        for cb in self._on_event:
            try:
                cb(CHANNEL, event, data)
            except Exception:
                logger.warning("upscale on_event callback failed", exc_info=True)

    # ---- what UpscaleProcess reads --------------------------------------
    def is_writing(self) -> bool:
        """True while a source is on its way to ComfyUI (a pause waits)."""
        return self._uploading > 0

    def active_batches(self) -> List[Dict[str, Any]]:
        return [self._row(r) for r in self._runs.values()]

    def progress_counts(self) -> Tuple[int, int, str]:
        """(results ended, results planned, phase word) over the active runs."""
        runs = list(self._runs.values())
        ended = sum(r.submitted - len(r.pending) for r in runs)
        total = sum(len(r.seeds) for r in runs)
        phase = "uploading" if self.is_writing() else "rendering"
        return ended, total, phase

    def _row(self, r: _Run) -> Dict[str, Any]:
        return {
            "batch_id": r.id,
            "source_path": to_native_path(r.req.source_path),
            "phase": r.phase,
            "queued": r.submitted,
            "ended": r.submitted - len(r.pending),
            "total": len(r.seeds),
        }

    # ---- starting a run ---------------------------------------------------
    async def start_run(
        self, req: UpscaleRunRequest, window: int = 2
    ) -> UpscaleStarted:
        preset = await asyncio.to_thread(self.db.get_workflow_preset, req.preset_id)
        if preset is None:
            raise UpscaleRequestError(f"Workflow {req.preset_id} does not exist")
        media_file = Path(to_native_path(req.source_path))
        record = await asyncio.to_thread(self.db.upscale_record, req.source_path)
        source = Path(to_native_path(record["original_path"])) if record else media_file
        if not source.is_file():
            raise UpscaleRequestError("The file to upscale is missing")
        kind = kind_for(media_file)
        if preset.get("kind") != kind:
            raise UpscaleRequestError(
                f"Workflow '{preset.get('name')}' is not a "
                f"{'video' if kind == 'upscale_video' else 'image'} upscale workflow"
            )
        bindings = resolve_bindings(json.loads(preset["workflow_json"]), kind)
        if req.prompt and bindings.positive is None:
            raise UpscaleRequestError("This workflow takes no prompt")
        if req.scale is not None and bindings.scale is None:
            raise UpscaleRequestError("This workflow takes no scale")
        if req.negative and bindings.negative is None:
            raise UpscaleRequestError("This workflow takes no negative prompt")
        if req.loras and bindings.lora_stack is None:
            raise UpscaleRequestError("This workflow takes no LoRAs")
        if req.denoise is not None:
            if bindings.denoise is None:
                raise UpscaleRequestError("This workflow takes no denoise strength")
            if not 0.0 <= req.denoise <= 1.0:
                raise UpscaleRequestError("Denoise must be between 0 and 1")
        if req.model and bindings.model is None:
            raise UpscaleRequestError("This workflow takes no model")
        seeds, next_seed = self._plan_seeds(req, bindings.seed is not None)
        if self.purge_stale is not None:
            await asyncio.to_thread(self.purge_stale, req.source_path)
        run = _Run(
            id=uuid.uuid4().hex,
            req=req,
            source_file=source,
            kind=kind,
            seeds=seeds,
            window=asyncio.Semaphore(max(1, window)),
        )
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._run(run))
        self._progress(run)
        return UpscaleStarted(run.id, len(seeds), next_seed)

    @staticmethod
    def _plan_seeds(
        req: UpscaleRunRequest, has_seed: bool
    ) -> Tuple[List[Optional[int]], Optional[int]]:
        if req.count < 1:
            raise UpscaleRequestError("Count must be at least 1")
        if not has_seed:
            # Repeats of a seedless graph would all come out the same.
            return [None], None
        if req.seed is None:
            raise UpscaleRequestError("This workflow needs a seed")
        try:
            planned, next_seed = plan_i2v_seeds(req.seed, req.seed_policy, 1, req.count)
        except I2vRequestError as exc:
            raise UpscaleRequestError(str(exc)) from exc
        return list(planned[0]), next_seed

    async def _unload_vlms(self) -> None:
        """Free the GPU for ComfyUI; failing to never stops the run. A T2I
        batch still writing prompts keeps its VLMs."""
        if self.get_all_vlms is None or self.prompting_elsewhere():
            return
        for vlm in list(self.get_all_vlms()):
            if vlm is None or not vlm.model_id:
                continue
            try:
                await vlm.shutdown()
            except Exception as exc:
                logger.warning("could not unload the VLM before rendering: %s", exc)

    async def unload_vlms(self) -> None:
        if self.unload_vlm_during_generation:
            await self._unload_vlms()

    async def _run(self, r: _Run) -> None:
        try:
            await self.unload_vlms()
            await self.wait_unpaused()
            self._uploading += 1
            self._progress(r)
            try:
                uploaded = await self.comfy.upload_file(r.source_file)
            finally:
                self._uploading -= 1
            r.phase = "rendering"
            self._progress(r)
            for seed in r.seeds:
                await self.wait_unpaused()
                await r.window.acquire()
                await self._submit(r, uploaded, seed)
                self._progress(r)
            r.all_submitted = True
            if not r.pending:
                r.all_jobs_ended.set()
            await r.all_jobs_ended.wait()
            if r.finished is None:
                self._retire(r, "done")
        except asyncio.CancelledError:
            raise  # cancel_batch / aclose own what happens next
        except Exception as exc:
            logger.warning("upscale run failed: %s", type(exc).__name__)
            if r.finished is None:
                self._retire(r, "failed", str(exc))

    async def _submit(self, r: _Run, uploaded: str, seed: Optional[int]) -> None:
        """Called holding a window slot: on success it belongs to the job (its
        terminal update frees it), on failure it is released here."""
        req = r.req
        video = r.kind == "upscale_video"
        params = GenerationParams(
            positive=req.prompt or None,
            seed=seed,
            width=0,
            height=0,
            batch_size=1,
            source_image=None if video else uploaded,
            source_video=uploaded if video else None,
            scale=req.scale,
            negative=req.negative or None,
            loras=list(req.loras or []),
            denoise=req.denoise,
            model=req.model or None,
        )
        output_dir = self.staging_root() / uuid.uuid4().hex
        # Shielded: submit writes the job row and only then queues it, so a
        # cancel landing in between must not lose the id.
        inflight: "asyncio.Future[int]" = asyncio.ensure_future(
            self.comfy.submit(
                req.preset_id,
                params,
                output_dir=output_dir,
                output_name="candidate",
                upscale_source_path=req.source_path,
            )
        )
        try:
            job_id = await asyncio.shield(inflight)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                try:
                    job_id = await inflight  # let a submit in flight land
                except BaseException:
                    r.window.release()
                    raise
                self._own(r, job_id)
            else:
                r.window.release()
            raise
        self._own(r, job_id)

    def _own(self, r: _Run, job_id: int) -> None:
        r.submitted += 1
        if self._early_ended.pop(job_id, None) is not None:
            r.window.release()  # it ended while submit() was still returning
            return
        r.pending.add(job_id)
        self._job_run[job_id] = r.id

    def _release(self, r: _Run, job_id: int) -> None:
        if job_id in r.pending:
            r.pending.discard(job_id)
            r.window.release()
        if r.all_submitted and not r.pending:
            r.all_jobs_ended.set()

    def _progress(self, r: _Run, event: str = "batch_progress") -> None:
        self._emit(event, self._row(r))

    def _retire(self, r: _Run, kind: str, error: Optional[str] = None) -> None:
        r.finished = kind
        event = {
            "done": "batch_done",
            "failed": "batch_failed",
            "cancelled": "batch_cancelled",
        }[kind]
        self._runs.pop(r.id, None)
        self._finished[r.id] = None
        while len(self._finished) > FINISHED_KEEP:
            del self._finished[next(iter(self._finished))]
        if error is None:
            self._progress(r, event)
        else:
            self._emit(event, {**self._row(r), "error": error})

    # ---- cancel -----------------------------------------------------------
    async def cancel_batch(self, batch_id: str) -> bool:
        """Stop the run and cancel its unfinished jobs. Candidates already
        recorded stay. False when the run is over."""
        r = self._runs.get(batch_id)
        if r is None:
            if batch_id in self._finished:
                return False
            raise UpscaleNotFoundError(batch_id)
        if r.finished is not None:
            return False
        return await asyncio.shield(self._abort(r))

    async def _abort(self, r: _Run) -> bool:
        r.finished = "cancelled"  # first: from here nothing else retires it
        task = r.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.wait({task})
        targets = sorted(r.pending)
        if targets:
            results = await asyncio.gather(
                *(self.comfy.cancel(job_id) for job_id in targets),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(
                        "could not cancel an upscale job: %s", type(result).__name__
                    )
        for job_id in targets:
            self._release(r, job_id)
            self._job_run.pop(job_id, None)
        self._retire(r, "cancelled")
        return True

    async def aclose(self) -> None:
        for r in list(self._runs.values()):
            r.finished = r.finished or "cancelled"
            if r.task is not None and not r.task.done():
                r.task.cancel()
        tasks = [r.task for r in self._runs.values() if r.task is not None]
        tasks.extend(self._record_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- job events -------------------------------------------------------
    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Registered via comfy_client.on_job_event. Sync; never raises."""
        try:
            if event == "job_update":
                self._on_job_update(payload)
            elif event == "job_outputs":
                task = asyncio.get_running_loop().create_task(self._record(payload))
                self._record_tasks.add(task)
                task.add_done_callback(self._record_tasks.discard)
        except Exception:
            logger.warning("upscale job event handling failed", exc_info=True)

    def _on_job_update(self, payload: Dict[str, Any]) -> None:
        if payload.get("state") not in TERMINAL_STATES:
            return
        job_id = payload.get("job_id")
        if not isinstance(job_id, int):
            return
        run_id = self._job_run.pop(job_id, None)
        if run_id is None:
            if self._runs:
                # Maybe a job of ours whose submit() has not returned yet.
                self._early_ended[job_id] = str(payload.get("state"))
                while len(self._early_ended) > 256:
                    self._early_ended.pop(next(iter(self._early_ended)))
            return
        r = self._runs.get(run_id)
        if r is not None:
            self._release(r, job_id)

    async def _record(self, payload: Dict[str, Any]) -> None:
        job_id = payload.get("job_id")
        if job_id is None:
            return
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None or not job.get("upscale_source_path"):
            return
        files = [Path(f) for f in payload.get("files") or []]
        added = await asyncio.to_thread(self._record_files, job, files)
        self._announce(job, added)

    def _announce(self, job: Dict[str, Any], added: int) -> None:
        source = to_native_path(job["upscale_source_path"])
        if added:
            self._emit("candidates_changed", {"source_path": source})
        else:
            self._emit("no_output", {"source_path": source})

    def _record_files(self, job: Dict[str, Any], files: List[Path]) -> int:
        """Thread. One candidate per readable output."""
        try:
            params = json.loads(job["params"])
        except (TypeError, ValueError):
            params = {}
        preset = self.db.get_workflow_preset(job["preset_id"]) or {}
        source_path = job["upscale_source_path"]
        added = 0
        for path in files:
            try:
                facts = self.technical(path)
            except (OSError, ValueError) as exc:
                logger.warning("unreadable upscale output: %s", type(exc).__name__)
                path.unlink(missing_ok=True)
                continue
            added += 1
            self.db.add_upscale_candidate(
                source_path=source_path,
                file_path=to_posix_path(path),
                kind=preset.get("kind") or kind_for(path),
                preset_id=job["preset_id"],
                preset_name=preset.get("name"),
                seed=params.get("seed"),
                prompt=params.get("positive"),
                scale=params.get("scale"),
                denoise=params.get("denoise"),
                width=facts["width"],
                height=facts["height"],
                duration_s=facts.get("duration"),
                file_size=facts["file_size"],
                render_s=render_seconds(job.get("started_at"), job.get("finished_at")),
                audio_lost=int(self._audio_lost(source_path, path)),
                job_id=job["id"],
            )
        return added

    def _audio_lost(self, source_path: str, output: Path) -> bool:
        if not is_video(output):
            return False
        record = self.db.upscale_record(source_path)
        source = record["original_path"] if record else source_path
        before, after = _has_audio(Path(to_native_path(source))), _has_audio(output)
        return before is True and after is False

    def recover_finished(self) -> int:
        """Record the outputs of jobs that finished with no runner listening.
        ``ComfyClient.start`` runs before runners subscribe, so this runs after
        they do. Thread."""
        added = 0
        for job in self.db.upscale_jobs_without_candidate():
            folder = job.get("output_dir")
            if not folder or not Path(folder).is_dir():
                continue
            files = [
                f
                for f in Path(folder).iterdir()
                if f.is_file() and f.suffix.lower() in self.supported
            ]
            added += self._record_files(job, sorted(files))
        return added
