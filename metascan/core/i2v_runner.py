"""Image-to-video runner: VLM prompt expansion + ComfyUI submission +
job-output ingest for the i2v flow.

Layering mirrors StoryboardRunner: this is the only module that knows
about i2v_videos; comfy_client stays a generic job driver. Correlation
flows one way -- generate() passes i2v_source_path into
ComfyClient.submit, and handle_job_event ingests job_outputs for jobs
whose row carries it (storyboard jobs carry panel_id/beat_id instead
and are ignored here, and vice versa).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from metascan.core.comfy_bindings import GenerationParams, resolve_bindings
from metascan.core.generation_categories import tag_generated_media
from metascan.core.i2v_compiler import (
    assemble_i2v_prompt,
    build_i2v_user_prompt,
    i2v_dims,
    i2v_grammar,
    i2v_max_tokens,
    lint_i2v_prompt,
    validate_i2v_beats,
)
from metascan.core.i2v_form import build_form_state
from metascan.core.i2v_output import I2vOutputError, resolve_output_target
from metascan.core.i2v_templates import (
    I2vTemplateError,
    assemble_i2v_template_prompt,
    build_i2v_template_user_prompt,
    get_i2v_template,
    i2v_template_grammar,
    i2v_template_max_tokens,
    validate_i2v_template_fill,
)
from metascan.core.prompt_store import get_prompt_store
from metascan.core.judgments import (
    DRAFT_WRITERS,
    WRITER_MANUAL,
    WriterModelError,
    check_writer_model,
    writer_id_allowed,
    normalize_prompt,
)
from metascan.core.vlm_client import AdapterLoadingError, AdapterNotLoadedError
from metascan.core.vlm_models import writer_missing_hint
from metascan.core.writer_adapters import (
    ADAPTER_LOADING,
    WriterAdapterUnavailable,
    parse_adapter_writer_id,
)
from metascan.core.vlm_select import pick_vlm_model
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]


def render_seconds(
    started_at: Optional[str], finished_at: Optional[str]
) -> Optional[float]:
    """Wall-clock seconds a job spent rendering, from the job row's ISO
    stamps. ``job_outputs`` fires before the job is marked done, so a
    missing ``finished_at`` means "now". ``started_at`` is stamped when
    metascan hands the prompt to ComfyUI, so time spent behind another
    in-flight prompt inside ComfyUI is included. None when the start is
    absent/unparseable or the span is negative."""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = (
            datetime.fromisoformat(finished_at)
            if finished_at
            else datetime.now(timezone.utc)
        )
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    span = (end - start).total_seconds()
    return round(span, 1) if span >= 0 else None


def i2v_output_root(configured: Optional[str], comfy_output_root: Any) -> Path:
    """The folder every I2V clip is written under: the configured
    ``i2v.output_root`` when it is set (each clip in the dated folder its
    ``i2v.output_prefix`` names below it), else ``<comfy.output_root>/i2v``
    (one folder per source image below it). One place for both readers:
    the runner places each clip here (``_output_placement``), and a full
    clean's re-ingest checks it for a drive that is not mounted."""
    text = str(configured or "").strip()
    if text:
        return Path(to_native_path(text))
    return Path(comfy_output_root) / "i2v"


class I2vRequestError(RuntimeError):
    """Caller error -- the route answers 400 with the message."""


class I2vNotFoundError(RuntimeError):
    """No such batch run -- the route answers 404."""


SEED_MAX = 2**31 - 1

# How many ended runs stay known, so a late cancel answers "over", not 404.
_FINISHED_KEEP = 50


def _random_seed() -> int:
    return random.randint(0, SEED_MAX)


def plan_i2v_seeds(
    seed: int,
    policy: str,
    batch_size: int,
    count: int,
    rng: Callable[[], int] = _random_seed,
) -> Tuple[List[List[int]], Optional[int]]:
    """One seed list per batch, in render order, and the next unused seed.

    ``fixed`` gives each batch the one seed (``count`` is ignored: the same
    seed would render the same clip again). ``increment`` / ``decrement``
    run on across batches; ``random`` starts at ``seed`` like a single
    submit and draws the rest from ``rng``. A seed outside ``0..SEED_MAX``
    is refused; the next seed is None when it would fall outside.
    """
    if policy not in ("fixed", "increment", "decrement", "random"):
        raise I2vRequestError(f"Unknown seed policy: {policy}")
    per_batch = 1 if policy == "fixed" else count
    total = batch_size * per_batch
    step = {"increment": 1, "decrement": -1}.get(policy, 0)
    seeds: List[int] = []
    current = seed
    for i in range(total):
        if not 0 <= current <= SEED_MAX:
            raise I2vRequestError(f"Seed {current} is outside 0..{SEED_MAX}")
        seeds.append(current)
        current = rng() if policy == "random" else current + step
    next_seed: Optional[int] = current if 0 <= current <= SEED_MAX else None
    return [seeds[i : i + per_batch] for i in range(0, total, per_batch)], next_seed


class I2vUnavailableError(RuntimeError):
    """The VLM subsystem is not running -- the route answers 503."""


_TERMINAL_JOB_STATES = ("done", "failed", "cancelled")


@dataclass
class I2vBatchRequest:
    source_path: str
    prompt: str  # "" = batch 1 is written too
    idea: str
    duration_s: float
    quality: str
    seed: int
    seed_policy: str  # fixed | increment | decrement | random
    batch_size: int
    count_per_batch: int  # ignored (1) when seed_policy is fixed
    megapixels: float
    loras: List[Dict[str, Any]]
    preset_id: int
    writer: str = "base"
    steps: Optional[int] = None
    template_id: Optional[str] = None
    output_root: Optional[str] = None
    output_prefix: Optional[str] = None
    category_ids: List[int] = field(default_factory=list)
    writer_id: Optional[str] = None  # lineage of the box's draft
    draft_prompt: Optional[str] = None
    writer_model: Optional[str] = None
    window: int = 4


@dataclass
class I2vBatchStarted:
    batch_id: str
    total_videos: int
    next_seed: Optional[int]


@dataclass
class _I2vBatch:
    id: str
    req: I2vBatchRequest
    seeds: List[List[int]]
    window: "asyncio.Semaphore"
    phase: str = "writing"  # "writing" | "rendering"
    batch: int = 0  # 1-based batch being written / rendered
    prompting: bool = True  # still writing prompts with the VLM
    submitted: int = 0
    # Submitted jobs without a terminal update; each owns a window slot.
    pending: Set[int] = field(default_factory=set)
    warnings: List[str] = field(default_factory=list)
    all_submitted: bool = False
    all_jobs_ended: "asyncio.Event" = field(default_factory=asyncio.Event)
    task: "Optional[asyncio.Task[None]]" = None
    finished: Optional[str] = None  # done | failed | cancelled

    @property
    def total(self) -> int:
        return len(self.seeds)

    @property
    def total_videos(self) -> int:
        return sum(len(s) for s in self.seeds)


class I2vRunner:
    def __init__(
        self,
        db: Any,
        comfy: Any,
        get_vlm: Callable[[], Any],
        output_root: Path,
        vlm_installed: Optional[Callable[[str], bool]] = None,
        writer_adapter: Optional[Callable[[int], Optional[Path]]] = None,
        get_all_vlms: Optional[Callable[[], Iterable[Any]]] = None,
        unload_vlm_during_generation: bool = True,
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.get_vlm = get_vlm  # the prompt-writer client
        # Adapter id -> its GGUF when the writer's llama-server serves it;
        # None: no adapter can be used.
        self.writer_adapter = writer_adapter
        # Says whether a model's files are on disk (None: trust the picker).
        # A missing writer model is refused with a "download it" hint rather
        # than spawning a llama-server that cannot load it.
        self.vlm_installed = vlm_installed
        self.output_root = Path(output_root)
        self._on_event: List[EventCb] = []
        self._ingest_tasks: Set["asyncio.Task[None]"] = set()
        # quality/idea are dialog metadata, not GenerationParams fields;
        # remembered per job until ingest. In-memory only: after a server
        # restart an ingested row simply has NULL quality/idea.
        self._job_meta: Dict[int, Dict[str, Any]] = {}
        # Last filename number handed out (epoch seconds). Two submits in
        # the same second must not share one -- see _next_output_number.
        self._last_output_number = 0
        self.get_all_vlms = get_all_vlms
        self.unload_vlm_during_generation = unload_vlm_during_generation
        # True while the other runner (T2I) is writing prompts with the
        # shared VLM; wired in lifespan. Keeps this run from unloading it.
        self.prompting_elsewhere: Callable[[], bool] = lambda: False
        self._batches: Dict[str, _I2vBatch] = {}
        # Ids of runs that ended (oldest first, bounded).
        self._finished: Dict[str, None] = {}
        # job id -> batch id, for submitted jobs without a terminal update.
        self._job_batch: Dict[int, str] = {}
        # Terminal states of jobs that ended before submit() returned (so
        # before the run knew them): job id -> state. Bounded.
        self._early_ended: Dict[int, str] = {}

    def on_event(self, cb: EventCb) -> None:
        self._on_event.append(cb)

    def _emit(self, channel: str, event: str, data: Dict[str, Any]) -> None:
        for cb in self._on_event:
            try:
                cb(channel, event, data)
            except Exception:
                logger.warning("i2v on_event callback failed", exc_info=True)

    # ---- prompt expansion (review-only, writes nothing) ----------------

    async def generate_prompt(
        self,
        source_path: str,
        idea: str,
        duration_s: float,
        template_id: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """``(text, warnings)`` of :meth:`write_prompt`."""
        text, warnings, _ = await self.write_prompt(
            source_path, idea, duration_s, template_id
        )
        return text, warnings

    async def write_prompt(
        self,
        source_path: str,
        idea: str,
        duration_s: float,
        template_id: Optional[str] = None,
        writer: str = "base",
    ) -> Tuple[str, List[str], str]:
        """Expand an idea into the H3 prompt: ``(text, warnings,
        writer_model)``, the last being the VLM model that wrote it (lineage).
        With ``template_id`` the découpage template owns the shot structure
        and the VLM fills only prose; without it, this is the single-take
        path, unchanged. ``writer`` is ``base`` or ``adapter:<id>`` (an I2V
        prompt-writer adapter, applied to the writer model; 409 when it
        cannot be served)."""
        lora_path = await self._adapter_lora(writer)
        template = None
        if template_id:
            try:
                template = get_i2v_template(template_id)
            except I2vTemplateError as exc:
                raise I2vRequestError(str(exc)) from exc
            if abs(float(duration_s) - template.duration_s) > 1e-6:
                raise I2vRequestError(
                    f"Template '{template.id}' is a {template.duration_s:.0f}s "
                    f"cadence; duration_s was {float(duration_s):.0f}"
                )

        vlm = self.get_vlm()
        if vlm is None:
            raise I2vUnavailableError("VLM subsystem is not running")
        path = Path(to_native_path(source_path))
        if not vlm.is_image_path(path):
            raise I2vRequestError(f"Not an image: {source_path}")
        if not path.exists():
            raise I2vRequestError(f"File not found: {source_path}")
        model_id = pick_vlm_model(vlm, role="writer")
        check = self.vlm_installed
        if check is not None and not await asyncio.to_thread(check, model_id):
            raise I2vUnavailableError(writer_missing_hint(model_id))
        if lora_path is not None:
            row = await asyncio.to_thread(
                self.db.get_writer_adapter, parse_adapter_writer_id(writer)
            )
            base = row["base_model"] if row else None
            if base != model_id:
                raise WriterAdapterUnavailable(
                    writer, f"it is built on {base}, the writer runs {model_id}"
                )
        extra: Dict[str, Any] = {}
        if lora_path is not None:
            extra["lora_path"] = lora_path
        await vlm.ensure_started(model_id)
        try:
            return await self._write(
                vlm, path, idea, duration_s, template, model_id, extra
            )
        except AdapterLoadingError as exc:
            raise WriterAdapterUnavailable(
                writer, str(exc), code=ADAPTER_LOADING
            ) from exc
        except AdapterNotLoadedError as exc:
            raise WriterAdapterUnavailable(writer, str(exc)) from exc

    async def _adapter_lora(self, writer: str) -> Optional[Path]:
        """None for ``base``; the GGUF of a servable I2V adapter for
        ``adapter:<id>``; ``WriterAdapterUnavailable`` otherwise."""
        if writer == "base":
            return None
        adapter_id = parse_adapter_writer_id(writer)
        if adapter_id is None:
            raise I2vRequestError(
                f"Unknown writer {writer!r}; use base or adapter:<id>"
            )
        if not await asyncio.to_thread(writer_id_allowed, writer, self.db, "i2v"):
            raise WriterAdapterUnavailable(writer, "not a ready image-to-video adapter")
        path = self.writer_adapter(adapter_id) if self.writer_adapter else None
        if path is None:
            raise WriterAdapterUnavailable(
                writer, "the prompt writer does not serve it"
            )
        return path

    async def _write(
        self,
        vlm: Any,
        path: Path,
        idea: str,
        duration_s: float,
        template: Any,
        model_id: str,
        extra: Dict[str, Any],
    ) -> Tuple[str, List[str], str]:
        if template is None:
            raw = await vlm.generate_text(
                system_prompt=get_prompt_store().get("I2V_BEATS_SYSTEM"),
                user_prompt=build_i2v_user_prompt(idea, duration_s),
                image_path=path,
                grammar=i2v_grammar(duration_s),
                temperature=0.6,
                max_tokens=i2v_max_tokens(duration_s),
                timeout=240.0,
                **extra,
            )
            result = validate_i2v_beats(raw)
            text = assemble_i2v_prompt(result)
            return text, lint_i2v_prompt(text, duration_s), model_id

        raw = await vlm.generate_text(
            system_prompt=get_prompt_store().get("I2V_TEMPLATE_SYSTEM"),
            user_prompt=build_i2v_template_user_prompt(template, idea),
            image_path=path,
            grammar=i2v_template_grammar(template),
            temperature=0.6,
            max_tokens=i2v_template_max_tokens(template),
            timeout=240.0,
            **extra,
        )
        fill = validate_i2v_template_fill(raw, template)
        text, notes = assemble_i2v_template_prompt(template, fill)
        warnings = lint_i2v_prompt(text, duration_s, template=template) + notes
        return text, warnings, model_id

    async def _source_dims(self, src: Path) -> Tuple[int, int]:
        """Source pixel dimensions: the media row first (already scanned,
        and what the dialog shows), falling back to reading the file's
        header for an image that has not been ingested yet."""
        media = await asyncio.to_thread(self.db.get_media, src)
        if media is not None and media.width and media.height:
            return int(media.width), int(media.height)
        try:
            from PIL import Image

            def _probe() -> Tuple[int, int]:
                with Image.open(src) as im:
                    return (int(im.width), int(im.height))

            return await asyncio.to_thread(_probe)
        except Exception as exc:
            raise I2vRequestError(
                f"Cannot determine the dimensions of {src.name}; "
                "rescan the library so its size is known."
            ) from exc

    def _next_output_number(self) -> int:
        """Epoch seconds, bumped past the previous value so numbers are
        strictly increasing within this process. (Across a restart the
        collector's no-clobber tail is the backstop.)"""
        number = max(int(time.time()), self._last_output_number + 1)
        self._last_output_number = number
        return number

    async def _output_placement(
        self, src: Path, output_root: Optional[str], output_prefix: Optional[str]
    ) -> Dict[str, Any]:
        """submit() kwargs placing the clip. No configured root keeps the
        original layout under comfy.output_root; a configured one must
        already exist (a typo must not silently grow a new tree)."""
        root = i2v_output_root(output_root, self.output_root)
        if not (output_root or "").strip():
            return {
                "output_dir": root / src.stem,
                "output_prefix": f"i2v_{src.stem}",
            }
        if not await asyncio.to_thread(root.is_dir):
            raise I2vRequestError(
                f"Video output directory does not exist: {root} -- fix it in "
                "Configuration → Video"
            )
        try:
            target = resolve_output_target(
                str(root), output_prefix, datetime.now(), self._next_output_number()
            )
        except I2vOutputError as exc:
            raise I2vRequestError(str(exc)) from exc
        return {"output_dir": target.directory, "output_name": target.stem}

    # ---- generation ------------------------------------------------------

    async def generate(
        self,
        *,
        source_path: str,
        prompt: str,
        duration_s: float,
        quality: str,
        seed: int,
        megapixels: float,
        loras: List[Dict[str, Any]],
        preset_id: int,
        idea: Optional[str] = None,
        steps: Optional[int] = None,
        template_id: Optional[str] = None,
        output_root: Optional[str] = None,
        output_prefix: Optional[str] = None,
        category_ids: Optional[List[int]] = None,
        writer_id: Optional[str] = None,
        draft_prompt: Optional[str] = None,
        writer_model: Optional[str] = None,
        on_submitted: Optional[Callable[[int], None]] = None,
    ) -> int:
        # Lineage: a prompt with a draft names the writer that drafted it; one
        # typed from scratch is `manual` whatever the caller says. Only a VLM
        # writer has a model, and it must be one the writer role can use.
        if draft_prompt is None:
            writer_id = WRITER_MANUAL
        elif not await asyncio.to_thread(writer_id_allowed, writer_id, self.db, "i2v"):
            if parse_adapter_writer_id(writer_id) is not None:
                # A draft by an adapter that has gone (deleted, failed, the
                # other kind): 409, so a stale client falls back to Base.
                raise WriterAdapterUnavailable(
                    str(writer_id), "not a ready image-to-video adapter"
                )
            raise I2vRequestError(
                f"writer_id {writer_id!r} is not a writer that drafts prompts "
                f"({', '.join(DRAFT_WRITERS)} or a ready adapter)"
            )
        try:
            writer_model = await asyncio.to_thread(
                check_writer_model, writer_id, writer_model, self.db, "i2v"
            )
        except WriterModelError as exc:
            raise I2vRequestError(str(exc)) from exc
        preset = await asyncio.to_thread(self.db.get_workflow_preset, preset_id)
        if preset is None:
            raise I2vRequestError(f"Preset {preset_id} does not exist")
        if preset.get("kind") != "ref2v":
            raise I2vRequestError(
                f"Preset '{preset.get('name')}' is kind "
                f"{preset.get('kind')!r}, not a video (ref2v) workflow"
            )
        tgt = preset.get("video_target")
        mode = preset.get("video_mode")
        if (tgt and tgt != "minimax") or (mode and mode != "i2va"):
            raise I2vRequestError(
                f"Preset '{preset.get('name')}' is tagged {tgt}/{mode}; "
                "the i2v flow needs minimax/i2va (or an untagged preset)"
            )
        if not prompt.strip():
            raise I2vRequestError("Prompt is empty")
        if megapixels <= 0:
            raise I2vRequestError(
                f"Megapixel budget must be positive, got {megapixels}"
            )
        if steps is not None and int(steps) <= 0:
            raise I2vRequestError(f"Steps must be positive, got {steps}")
        src = Path(to_native_path(source_path))
        if not src.exists():
            raise I2vRequestError(f"File not found: {source_path}")

        # Output size tracks the source aspect ratio: the image IS the
        # first frame, so any other ratio letterboxes or crops it.
        src_w, src_h = await self._source_dims(src)
        width, height = i2v_dims(src_w, src_h, float(megapixels))

        workflow = json.loads(preset["workflow_json"])
        bindings = resolve_bindings(workflow, preset["kind"])
        if loras and bindings.lora_stack is None:
            raise I2vRequestError(
                "This preset has no MS_LORA_STACK node; clear the lora "
                "list or register a workflow that has one"
            )

        # Steps drive the High quality preset only: the Fast slot is a
        # step-distilled build whose count must stay baked in. A quality
        # preset with no MS_STEPS node keeps its own count rather than
        # failing the submit -- the MS_DURATION precedent.
        apply_steps = (
            steps is not None and quality == "quality" and bindings.steps is not None
        )

        # Resolved before the upload: a bad output config is a caller
        # error and should cost nothing.
        placement = await self._output_placement(src, output_root, output_prefix)

        first_frame = await self.comfy.upload_file(src)
        params = GenerationParams(
            positive=prompt,
            seed=int(seed),
            width=int(width),
            height=int(height),
            batch_size=1,
            first_frame=first_frame,
            loras=list(loras),
            duration_s=(float(duration_s) if bindings.duration is not None else None),
            steps=(int(steps) if apply_steps and steps is not None else None),
        )
        job_id = int(
            await self.comfy.submit(
                preset_id,
                params,
                i2v_source_path=to_posix_path(source_path),
                **placement,
            )
        )
        # Same step of the loop as the submit: a batch run must know the job
        # before ComfyUI can announce its end or a cancel can land.
        if on_submitted is not None:
            on_submitted(job_id)
        # Held in memory until ingest, never written before it: a cancelled
        # or failed render must leave nothing behind (by decision). `form`
        # is the dialog exactly as submitted -- including a step selection a
        # Fast render does not apply -- and seeds the clip's editable
        # form_state; the rest are as-rendered facts.
        self._job_meta[job_id] = {
            "quality": quality,
            "idea": idea,
            "megapixels": float(megapixels),
            "loras": [dict(entry) for entry in loras],
            # Tagged onto the clip at ingest; session-local in the dialog,
            # so not part of the form.
            "category_ids": list(category_ids or []),
            # Prompt lineage; NULL on the row if a restart loses it first.
            "writer_id": writer_id,
            "writer_model": writer_model,
            "draft_prompt": draft_prompt,
            "form": build_form_state(
                idea=idea,
                prompt=prompt,
                duration_s=duration_s,
                quality=quality,
                megapixels=megapixels,
                steps=steps,
                seed=seed,
                loras=loras,
                template_id=template_id,
            ),
        }
        return job_id

    # ---- batch runs ------------------------------------------------------

    def is_prompting(self) -> bool:
        """True while any batch run is still writing prompts with the VLM."""
        return any(b.prompting for b in self._batches.values())

    def active_batches(self) -> List[Dict[str, Any]]:
        return [self._row(b) for b in self._batches.values()]

    def _row(self, b: _I2vBatch) -> Dict[str, Any]:
        return {
            "batch_id": b.id,
            "source_path": b.req.source_path,
            "phase": b.phase,
            "batch": b.batch,
            "total_batches": b.total,
            "queued": b.submitted,
            "total_videos": b.total_videos,
        }

    async def start_batch(self, req: I2vBatchRequest) -> I2vBatchStarted:
        """Plan the seeds, check what the run will need, and start it in the
        background. Raises before anything runs when it cannot."""
        if req.batch_size < 1 or req.count_per_batch < 1 or req.window < 1:
            raise I2vRequestError("Batch, count and window must be at least 1")
        seeds, next_seed = plan_i2v_seeds(
            req.seed, req.seed_policy, req.batch_size, req.count_per_batch
        )
        # The writer is needed only when some prompt must be written: batch
        # 1 is the box text when there is any.
        if req.batch_size > 1 or not req.prompt.strip():
            await self._adapter_lora(req.writer)
            if self.get_vlm() is None:
                raise I2vUnavailableError("VLM subsystem is not running")
        if (
            req.draft_prompt is not None
            and parse_adapter_writer_id(req.writer_id) is not None
        ):
            if not await asyncio.to_thread(
                writer_id_allowed, req.writer_id, self.db, "i2v"
            ):
                raise WriterAdapterUnavailable(
                    str(req.writer_id), "not a ready image-to-video adapter"
                )
        batch = _I2vBatch(
            id=uuid.uuid4().hex,
            req=req,
            seeds=seeds,
            window=asyncio.Semaphore(req.window),
        )
        self._batches[batch.id] = batch
        batch.task = asyncio.create_task(self._run(batch), name=f"i2v-{batch.id[:8]}")
        return I2vBatchStarted(batch.id, batch.total_videos, next_seed)

    def _progress(self, b: _I2vBatch, event: str = "batch_progress") -> None:
        data = self._row(b)
        if event in ("batch_progress", "batch_done"):
            data["warnings"] = list(b.warnings)
        self._emit("i2v", event, data)

    async def _step_prompt(
        self, b: _I2vBatch, index: int
    ) -> Tuple[str, Dict[str, Optional[str]]]:
        """The text of batch ``index`` (0-based) and its lineage: the box
        text for batch 1, else a freshly written prompt."""
        req = b.req
        b.batch = index + 1
        if index == 0 and req.prompt.strip():
            return req.prompt, {
                "writer_id": req.writer_id,
                "draft_prompt": req.draft_prompt,
                "writer_model": req.writer_model,
            }
        text, warnings, model = await self.write_prompt(
            req.source_path, req.idea, req.duration_s, req.template_id, req.writer
        )
        b.warnings.extend(w for w in warnings if w not in b.warnings)
        self._progress(b)
        return text, {
            "writer_id": req.writer,
            "draft_prompt": text,
            "writer_model": model,
        }

    async def _submit_step(
        self, b: _I2vBatch, index: int, text: str, lineage: Dict[str, Optional[str]]
    ) -> None:
        b.batch = index + 1
        for seed in b.seeds[index]:
            await b.window.acquire()
            await self._submit_one(b, text, lineage, seed)
            self._progress(b)

    def _register(self, b: _I2vBatch, job_id: int) -> None:
        b.submitted += 1
        if job_id in self._early_ended:
            # It ended while submit() was still returning: its update found
            # no owner. It never holds a slot; _submit_one drops its meta.
            b.window.release()
            return
        b.pending.add(job_id)
        self._job_batch[job_id] = b.id

    async def _submit_one(
        self, b: _I2vBatch, text: str, lineage: Dict[str, Optional[str]], seed: int
    ) -> None:
        """Submit one video. Called holding a window slot: on success the
        slot belongs to the job (its terminal update frees it), on failure it
        is released here."""
        req = b.req
        # Shielded: ComfyClient.submit writes the job row and only then
        # queues it, so a cancel landing in between must not lose the id --
        # the row would sit queued and run after the next restart.
        # generate() registers the job through on_submitted the moment the
        # submit returns.
        inflight: "asyncio.Future[int]" = asyncio.ensure_future(
            self.generate(
                source_path=req.source_path,
                prompt=text,
                duration_s=req.duration_s,
                quality=req.quality,
                seed=seed,
                megapixels=req.megapixels,
                loras=req.loras,
                preset_id=req.preset_id,
                idea=req.idea,
                steps=req.steps,
                template_id=req.template_id,
                output_root=req.output_root,
                output_prefix=req.output_prefix,
                category_ids=req.category_ids,
                writer_id=lineage["writer_id"],
                draft_prompt=lineage["draft_prompt"],
                writer_model=lineage["writer_model"],
                on_submitted=lambda job_id: self._register(b, job_id),
            )
        )
        before = b.submitted
        try:
            job_id = await asyncio.shield(inflight)
            state = self._early_ended.pop(job_id, None)
            if state is not None and state != "done":
                self._job_meta.pop(job_id, None)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                try:
                    await inflight  # let a submit in flight land
                except BaseException:
                    pass
            if b.submitted == before:
                b.window.release()
            raise

    def _release(self, b: _I2vBatch, job_id: int) -> None:
        if job_id in b.pending:
            b.pending.discard(job_id)
            b.window.release()
        if b.all_submitted and not b.pending:
            b.all_jobs_ended.set()

    async def _unload_vlm(self, b: _I2vBatch) -> None:
        """Free the GPU for ComfyUI: shut down each distinct VLM client that
        has a model loaded, except the prompt writer while another run (or a
        T2I Random batch) is still writing prompts with it. Failing to unload
        never stops the run."""
        writer = self.get_vlm()
        clients: List[Any] = (
            list(self.get_all_vlms()) if self.get_all_vlms is not None else []
        )
        if writer is not None and not any(c is writer for c in clients):
            clients.insert(0, writer)
        writer_busy = self.prompting_elsewhere() or any(
            other is not b and other.prompting for other in self._batches.values()
        )
        for vlm in clients:
            if vlm is None or not vlm.model_id:
                continue
            if vlm is writer and writer_busy:
                continue
            try:
                await vlm.shutdown()
            except Exception as exc:
                logger.warning("could not unload the VLM before rendering: %s", exc)

    async def _run(self, b: _I2vBatch) -> None:
        """Write the prompts, then submit the jobs -- in that order when
        unloading is on, interleaved when it is off. Anything that goes wrong
        ends the run with one ``batch_failed``; what was queued stays."""
        try:
            try:
                if self.unload_vlm_during_generation:
                    steps = [await self._step_prompt(b, i) for i in range(b.total)]
                    b.prompting = False
                    b.phase = "rendering"
                    self._progress(b)
                    await self._unload_vlm(b)
                    for i, (text, lineage) in enumerate(steps):
                        await self._submit_step(b, i, text, lineage)
                else:
                    for i in range(b.total):
                        text, lineage = await self._step_prompt(b, i)
                        b.phase = "rendering"
                        await self._submit_step(b, i, text, lineage)
            finally:
                b.prompting = False
            b.all_submitted = True
            if not b.pending:
                b.all_jobs_ended.set()
            await b.all_jobs_ended.wait()
            if b.finished is None:
                self._retire(b, "done")
        except asyncio.CancelledError:
            raise  # cancel_batch / aclose own what happens next
        except Exception as exc:
            logger.warning("i2v batch %s failed: %s", b.id, exc, exc_info=True)
            if b.finished is None:
                reason = f"{exc} ({b.submitted} of {b.total_videos} videos queued)"
                self._retire(b, "failed", reason)

    def _retire(self, b: _I2vBatch, kind: str, error: Optional[str] = None) -> None:
        """Emit the run's one terminal frame and drop it from the active
        list. Callers check ``finished`` first; the abort sets it before its
        first await, so nothing else retires a run being cancelled."""
        b.finished = kind
        event = {
            "done": "batch_done",
            "failed": "batch_failed",
            "cancelled": "batch_cancelled",
        }[kind]
        self._batches.pop(b.id, None)
        self._finished[b.id] = None
        while len(self._finished) > _FINISHED_KEEP:
            del self._finished[next(iter(self._finished))]
        (
            self._progress(b, event)
            if error is None
            else self._emit("i2v", event, {**self._row(b), "error": error})
        )

    async def cancel_batch(self, batch_id: str) -> bool:
        """Stop writing and cancel every unfinished job of the run. False
        when it is already over; an id never seen raises
        ``I2vNotFoundError``."""
        b = self._batches.get(batch_id)
        if b is None:
            if batch_id in self._finished:
                return False
            raise I2vNotFoundError(f"No i2v batch {batch_id}")
        if b.finished is not None:
            return False
        # Shielded: a caller that goes away mid-cancel must not leave the
        # run half-cancelled.
        return await asyncio.shield(self._abort(b))

    async def _abort(self, b: _I2vBatch) -> bool:
        # First, before any await: from here nothing else retires the run.
        b.finished = "cancelled"
        task = b.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.wait({task})
        targets = sorted(b.pending)
        if targets:
            results = await asyncio.gather(
                *(self.comfy.cancel(job_id) for job_id in targets),
                return_exceptions=True,
            )
            for job_id, result in zip(targets, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "could not cancel i2v job %s of batch %s: %s",
                        job_id,
                        b.id,
                        result,
                    )
        for job_id in targets:
            self._release(b, job_id)
            self._job_batch.pop(job_id, None)
            self._job_meta.pop(job_id, None)
        self._retire(b, "cancelled")
        return True

    def _on_job_update(self, payload: Dict[str, Any]) -> None:
        """A job ended: free its window slot. A failed job does not end the
        run."""
        if payload.get("state") not in _TERMINAL_JOB_STATES:
            return
        job_id = payload.get("job_id")
        if not isinstance(job_id, int):
            return
        batch_id = self._job_batch.pop(job_id, None)
        if batch_id is None:
            if self._batches:
                # Maybe a job of ours whose submit() has not returned yet.
                self._early_ended[job_id] = str(payload.get("state"))
                while len(self._early_ended) > 256:
                    self._early_ended.pop(next(iter(self._early_ended)))
            return
        if payload.get("state") != "done":
            # A done job's snapshot belongs to its ingest.
            self._job_meta.pop(job_id, None)
        b = self._batches.get(batch_id)
        if b is not None:
            self._release(b, job_id)

    # ---- ingest ------------------------------------------------------------

    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Registered via comfy_client.on_job_event. Sync; never raises."""
        if event == "job_update":
            try:
                self._on_job_update(payload)
            except Exception:
                logger.warning("i2v job update handling failed", exc_info=True)
            return
        if event != "job_outputs":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("i2v handle_job_event: no running loop", exc_info=True)
            return
        task = loop.create_task(self._ingest_outputs(payload))
        self._ingest_tasks.add(task)
        task.add_done_callback(self._ingest_tasks.discard)

    async def _ingest_outputs(self, payload: Dict[str, Any]) -> None:
        job_id = payload.get("job_id")
        if job_id is None:
            return
        job = await asyncio.to_thread(self.db.get_generation_job, job_id)
        if job is None or not job.get("i2v_source_path"):
            return
        try:
            params = json.loads(job["params"])
        except (TypeError, ValueError):
            params = {}
        meta = self._job_meta.pop(int(job_id), {})
        draft = meta.get("draft_prompt")
        edited: Optional[int] = None
        if meta.get("writer_id") is not None:
            edited = int(
                draft is not None
                and normalize_prompt(draft)
                != normalize_prompt(params.get("positive") or "")
            )
        inserted: List[str] = []
        for f in payload.get("files") or []:
            posix = to_posix_path(f)
            try:
                await asyncio.to_thread(
                    self.db.create_i2v_video,
                    source_path=job["i2v_source_path"],
                    file_path=posix,
                    prompt_used=params.get("positive"),
                    idea=meta.get("idea"),
                    seed=params.get("seed"),
                    duration_s=params.get("duration_s"),
                    quality=meta.get("quality"),
                    width=params.get("width"),
                    height=params.get("height"),
                    steps=params.get("steps"),
                    render_s=render_seconds(
                        job.get("started_at"), job.get("finished_at")
                    ),
                    preset_id=job.get("preset_id"),
                    comfy_prompt_id=job.get("comfy_prompt_id"),
                    megapixels=meta.get("megapixels"),
                    loras=meta.get("loras"),
                    form_state=meta.get("form"),
                    writer_id=meta.get("writer_id"),
                    writer_model=meta.get("writer_model"),
                    draft_prompt=draft,
                    edited=edited,
                )
                inserted.append(posix)
            except Exception:
                logger.warning(
                    "Could not ingest i2v video %s for job %s",
                    f,
                    job_id,
                    exc_info=True,
                )
        if not inserted:
            return
        # Before the clip is announced, so a refetch already sees its chips.
        await tag_generated_media(
            self.db, inserted, meta.get("category_ids") or [], self._emit
        )
        self._emit(
            "i2v",
            "i2v_videos_changed",
            {
                "source_path": to_native_path(job["i2v_source_path"]),
                "files": [to_native_path(p) for p in inserted],
            },
        )

    async def aclose(self) -> None:
        runs = [b.task for b in self._batches.values() if b.task is not None]
        for task in runs:
            task.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)
        tasks = [t for t in self._ingest_tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
