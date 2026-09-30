"""Text-to-image runner: caption resolution, prompt writing, batches.

Layering mirrors I2vRunner: this is the only module that knows about
``t2i_images``; ``ComfyClient`` stays a generic job driver. Correlation
flows one way -- a batch stamps ``t2i_batch_id`` on every job it submits,
and ``handle_job_event`` (registered with ``ComfyClient.on_job_event``)
turns the finished jobs back into ``t2i_images`` rows and counters.

A batch is planned completely before anything happens (``start_batch``):
every static problem is a named error, the whole seed sequence is known,
and so is the seed the dialog should show afterwards (``next_seed``). Only
then is state created, events are emitted and a background task takes over.
That task is a producer (it writes the prompts) and a consumer (it submits
the jobs, at most ``window`` unfinished at a time) over a small queue;
whether they take turns or overlap follows ``unload_vlm_during_generation``
(spec 6.3). Every batch ends with exactly one terminal frame:
``batch_complete``, ``batch_cancelled`` or ``batch_error``.

State is in memory, event-loop only (no locks): a restart drops unsubmitted
steps, and jobs ComfyClient re-enqueues still ingest, without a form state.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import os
import random
import sqlite3
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from metascan.core.comfy_bindings import Bindings, GenerationParams, resolve_bindings
from metascan.core.i2v_output import I2vOutputError, resolve_output_target
from metascan.core.t2i_captions import CaptionPicker, CaptionStore
from metascan.core.t2i_characters import ResolvedCaption, resolve_caption
from metascan.core.t2i_form import (
    ASPECT_RATIOS,
    SEED_MAX,
    SEED_POLICIES,
    T2I_MODES,
    T2iFormError,
    build_form_state,
    next_seed,
    normalize_loras,
    render_seconds,
    t2i_dims,
)
from metascan.core.t2i_models import (
    MODEL_PROFILES,
    T2iModelError,
    T2iModelProfile,
    effective_identity,
    get_profile,
)
from metascan.core.t2i_prompt import (
    compose_t2i_prompts,
    fallback_prompt,
    parse_t2i_output,
)
from metascan.core.t2i_wildcards import LibraryCache
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError, pick_vlm_model
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]

# Warning texts the dialog shows verbatim.
WARN_VLM_UNAVAILABLE = "VLM unavailable - used the resolved caption"
WARN_NEGATIVE_IGNORED = "negative prompt ignored: the workflow has no MS_NEGATIVE node"

_VLM_TEMPERATURE = 0.6
_VLM_TIMEOUT_S = 240.0
_TERMINAL_JOB_STATES = ("done", "failed", "cancelled")
# Finished batches remembered (so a cancel of one is "already over", not
# "unknown") and per-job snapshots kept before the oldest are evicted.
_FINISHED_KEEP = 50
_JOB_META_LIMIT = 2000


class T2iRequestError(RuntimeError):
    """Caller error -- the route answers 400 with the message."""


class T2iNotFoundError(RuntimeError):
    """An id the runner has never seen -- the route answers 404."""


class T2iConflictError(RuntimeError):
    """Another Random batch is running -- the route answers 409. The
    message starts with ``random_batch_active``."""


class T2iUnavailableError(RuntimeError):
    """The t2i runner cannot serve the request -- the route answers 503."""


@dataclass
class BatchRequest:
    mode: str
    model: str
    preset_id: int
    megapixels: float
    seed: int
    seed_policy: str
    batch_size: int = 1
    count_per_batch: int = 1
    loras: List[Dict[str, Any]] = field(default_factory=list)
    # Manual fields. A Random request may carry them too: one with a prompt
    # renders it as its first step (see ``_supplies_first_step``).
    caption: Optional[str] = None
    prompt: Optional[str] = None
    negative: Optional[str] = None
    aspect_ratio: Optional[str] = None
    # Random field
    filter: Optional[Dict[str, Any]] = None


@dataclass
class BatchStarted:
    batch_id: str
    total_images: int
    warnings: List[str]


@dataclass
class PromptResult:
    prompt: str
    negative: Optional[str]
    resolved_caption: str
    warnings: List[str]


@dataclass
class _Batch:
    """One planned batch: every input resolved, nothing rendered yet."""

    id: str
    req: BatchRequest  # normalised copy (loras cleaned); the caller's is untouched
    profile: T2iModelProfile
    bindings: Bindings
    loras: List[Dict[str, Any]]
    seeds: List[int]  # one per image, in render order
    per_step: int  # images per step
    total_steps: int
    next_seed: Optional[int]  # the first unused seed after the run, if there is one
    root: str  # output root directory (absolute)
    prefix: str  # output prefix template, relative to the root
    content_mode: str
    identity: str
    window: asyncio.Semaphore
    picker: Optional[CaptionPicker]
    warnings: List[str]
    started_at: str
    phase: str  # "prompting" | "rendering"
    prompting: bool  # still writing prompts (Random batches only)
    step: Optional[Dict[str, Any]] = None  # snapshot of the latest step
    images_done: int = 0
    images_failed: int = 0
    last_error: Optional[str] = None
    task: Optional["asyncio.Task[None]"] = None
    # Jobs submitted whose terminal job_update has not arrived. Each holds
    # one slot of ``window``; membership IS ownership of the slot, so a slot
    # is released exactly once however many events arrive.
    pending: Set[int] = field(default_factory=set)
    # "complete" | "cancelled" | "error" once the terminal frame is decided.
    finished: Optional[str] = None
    # Submitted jobs not yet accounted for: waiting for their image to be
    # ingested, or for a failed / cancelled update. A job leaves this set
    # exactly once, which is what makes every counter exact.
    unresolved: Set[int] = field(default_factory=set)
    # Jobs whose outputs have been announced and whose ingest is running.
    ingesting: Set[int] = field(default_factory=set)
    resolved: int = 0  # jobs accounted for; the batch ends when it reaches the total

    @property
    def mode(self) -> str:
        return self.req.mode

    @property
    def total_images(self) -> int:
        return len(self.seeds)

    def step_seeds(self, index: int) -> List[int]:
        """The seeds of step ``index`` (0-based); the first is the step's
        character seed."""
        return self.seeds[index * self.per_step : (index + 1) * self.per_step]


@dataclass
class _Step:
    """One caption's worth of images: a prompt and the seeds it renders with."""

    index: int  # 0-based
    # Provenance: the given caption for a supplied step (Manual, or a Random
    # batch's first), the raw row for a drawn one.
    caption: str
    aspect_ratio: str
    seeds: List[int]
    prompt: str
    negative: Optional[str]
    warnings: List[str]
    width: int
    height: int

    @property
    def prompt_seed(self) -> int:
        """The seed the step's characters were drawn with: its first image's."""
        return self.seeds[0]


def default_output_root(comfy_output_root: Any) -> Path:
    """``<comfy.output_root>/t2i``, as an absolute path.

    Absolute because ``resolve_output_target`` refuses a relative root and
    ``comfy.output_root`` defaults to the relative ``data/storyboards``.
    """
    base = Path(to_native_path(str(comfy_output_root)))
    return Path(os.path.abspath(base / "t2i"))


def plan_seeds(
    policy: str, first: int, requested: int, rng: random.Random
) -> List[int]:
    """The seed of every image in a run, ``first`` first.

    Increment and decrement stop early -- the list is shorter than
    ``requested`` -- rather than leave ``0..SEED_MAX``, wrap or repeat a
    seed. Fixed repeats ``first``; random draws the rest from ``rng``.
    """
    seeds = [first]
    while len(seeds) < requested:
        following = next_seed(policy, seeds[-1], rng)
        if following is None:
            break
        seeds.append(following)
    return seeds


def _check_seed(seed: Any) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise T2iRequestError(f"Seed must be a whole number from 0 to {SEED_MAX}")
    return int(seed)


def _check_count(value: Any, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise T2iRequestError(f"{label} must be a whole number from 1 to {maximum}")
    return int(value)


def _check_aspect(aspect: Any) -> None:
    if aspect not in ASPECT_RATIOS:
        raise T2iRequestError(
            f"Aspect ratio {aspect!r} is not supported; use one of "
            f"{', '.join(ASPECT_RATIOS)}"
        )


def _supplies_first_step(req: BatchRequest) -> bool:
    """A Random request that carries a prompt renders it as its first step
    (the dialog's Generate Prompt, then Generate): nothing is drawn and no
    prompt is written for that step, the rest are drawn as usual. A blank
    prompt supplies nothing, and neither does a caption or ratio without one."""
    return (
        req.mode == "random"
        and isinstance(req.prompt, str)
        and req.prompt.strip() != ""
    )


def _profile(model: Any) -> T2iModelProfile:
    try:
        return get_profile(model)
    except (T2iModelError, TypeError):
        raise T2iRequestError(
            f"Unknown model {model!r}; known models: {', '.join(MODEL_PROFILES)}"
        ) from None


class T2iRunner:
    def __init__(
        self,
        db: Any,
        comfy: Any,
        get_vlm: Callable[[], Any],
        captions: CaptionStore,
        library: LibraryCache,
        output_root: Path,
        get_config: Callable[[], Dict[str, Any]],
        unload_vlm_during_generation: bool = True,
        vlm_installed: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.get_vlm = get_vlm
        # Says whether a model's files are on disk (None: trust the picker).
        # A model that is not installed is "no VLM" -- the resolved caption
        # is the prompt -- not a failure to retry.
        self.vlm_installed = vlm_installed
        self.captions = captions  # public: the routes read them too
        self.library = library
        self.output_root = Path(output_root)
        self.unload_vlm_during_generation = unload_vlm_during_generation
        # Called afresh for every prompt and every batch, so an edit to the
        # config file applies to the next request without a restart.
        self._get_config = get_config
        self._on_event: List[EventCb] = []
        self._batches: Dict[str, _Batch] = {}
        self._finished: "OrderedDict[str, _Batch]" = OrderedDict()
        # job id -> batch id, for every submitted job whose terminal update
        # has not arrived; and the per-job snapshot the ingest will need.
        self._job_batch: Dict[int, str] = {}
        self._job_meta: Dict[int, Dict[str, Any]] = {}
        # Ingest tasks spawned by handle_job_event; held so they are not
        # garbage-collected mid-flight and can be drained by aclose().
        self._ingest_tasks: Set["asyncio.Task[None]"] = set()
        # Last filename number handed out (epoch seconds). Two images in the
        # same second must not share one -- see _next_output_number.
        self._last_output_number = 0

    def on_event(self, cb: EventCb) -> None:
        self._on_event.append(cb)

    def _emit(self, channel: str, event: str, data: Dict[str, Any]) -> None:
        for cb in self._on_event:
            try:
                cb(channel, event, data)
            except Exception:
                logger.warning("t2i on_event callback failed", exc_info=True)

    def _next_output_number(self) -> int:
        """Epoch seconds, bumped past the previous value so numbers are
        strictly increasing within this process. (Across a restart the
        collector's no-clobber tail is the backstop.)"""
        number = max(int(time.time()), self._last_output_number + 1)
        self._last_output_number = number
        return number

    # ---- caption resolution and prompt writing (review-only) --------------

    async def _resolve(
        self, profile: T2iModelProfile, caption: str, seed: int, cfg: Dict[str, Any]
    ) -> ResolvedCaption:
        identity = effective_identity(profile, cfg["identity"])
        library, _ = await asyncio.to_thread(self.library.get)
        return resolve_caption(caption, seed, identity, library)

    async def resolve(self, *, caption: str, seed: int, model: str) -> ResolvedCaption:
        """Expand a caption's tokens for ``seed`` in ``model``'s identity
        style. Pure of the VLM; an empty caption is allowed (the dialog
        resolves as the user types)."""
        profile = _profile(model)
        _check_seed(seed)
        return await self._resolve(profile, caption, seed, self._get_config())

    async def _usable_model(self, vlm: Any) -> Optional[str]:
        """The model to ask, or None when there is none: the picker found no
        loadable model, or the one it found is not installed."""
        try:
            model_id = pick_vlm_model(vlm)
        except VlmSelectError:
            return None
        check = self.vlm_installed
        if check is not None and not await asyncio.to_thread(check, model_id):
            return None
        return model_id

    async def _vlm_prompt(
        self,
        vlm: Any,
        model_id: str,
        profile: T2iModelProfile,
        system_prompt: str,
        user_prompt: str,
    ) -> Tuple[str, Optional[str]]:
        """One VLM round trip: start the model, ask, split off the negative.
        Raises VlmError, TimeoutError or RuntimeError; an OSError (the spawn
        of a missing or non-executable llama-server, a dropped connection)
        becomes a VlmError so callers handle it like any other VLM failure."""
        try:
            await vlm.ensure_started(model_id)
            raw = await vlm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=_VLM_TEMPERATURE,
                max_tokens=profile.max_tokens,
                timeout=_VLM_TIMEOUT_S,
            )
        except TimeoutError:
            raise  # a TimeoutError is an OSError: callers already know it
        except OSError as exc:
            raise VlmError(f"the VLM could not be used: {exc}") from exc
        prompt, negative = parse_t2i_output(profile, raw)
        if not prompt.strip():
            raise VlmError("the VLM returned an empty prompt")
        return prompt, negative

    async def generate_prompt(
        self, *, caption: str, seed: int, model: str
    ) -> PromptResult:
        """Resolve ``caption`` and have the VLM write ``model``'s prompt for
        it. Writes nothing.

        No VLM, or none loadable on this hardware, is not an error: the
        resolved caption is the prompt (see ``fallback_prompt``) and a
        warning says so. A VLM that is there but fails (VlmError, timeout)
        propagates -- the user retries; only a running batch retries once by
        itself and then falls back, so an unattended run never dies on one
        bad call."""
        profile = _profile(model)
        _check_seed(seed)
        if not isinstance(caption, str) or not caption.strip():
            raise T2iRequestError("Caption is empty")
        cfg = self._get_config()
        resolved = await self._resolve(profile, caption, seed, cfg)
        warnings = list(resolved.warnings)

        vlm = self.get_vlm()
        model_id = await self._usable_model(vlm) if vlm is not None else None
        if vlm is None or model_id is None:
            prompt, negative = fallback_prompt(profile, resolved.text)
            warnings.append(WARN_VLM_UNAVAILABLE)
            return PromptResult(prompt, negative, resolved.text, warnings)

        system_prompt, user_prompt = compose_t2i_prompts(
            profile, resolved.text, cfg["content_mode"]
        )
        prompt, negative = await self._vlm_prompt(
            vlm, model_id, profile, system_prompt, user_prompt
        )
        return PromptResult(prompt, negative, resolved.text, warnings)

    # ---- batch planning ----------------------------------------------------

    async def _placement(self, cfg: Dict[str, Any]) -> Tuple[str, str]:
        """``(root, prefix)`` for this run, validated once upfront.

        A blank ``output_root`` means ``<comfy.output_root>/t2i``, created
        on demand. A configured one must already be a directory: a typo must
        not silently grow a new tree.
        """
        configured = str(cfg["output_root"]).strip()
        prefix = str(cfg["output_prefix"])
        if configured:
            root = Path(to_native_path(configured))
            if not await asyncio.to_thread(root.is_dir):
                raise T2iRequestError(
                    f"Image output directory does not exist: {root} -- fix it "
                    "in the Text to Image configuration"
                )
        else:
            root = default_output_root(self.output_root)
            try:
                await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            except OSError as exc:
                raise T2iRequestError(
                    f"Cannot create the default image output directory {root}: {exc}"
                ) from exc
        try:
            # A dry run: the same call renders every image's path later.
            resolve_output_target(str(root), prefix, datetime.now(), 0)
        except I2vOutputError as exc:
            raise T2iRequestError(str(exc)) from exc
        return str(root), prefix

    async def _plan_batch(self, req: BatchRequest, cfg: Dict[str, Any]) -> _Batch:
        """Validate ``req`` against everything that can be checked without
        rendering and build the batch. Raises before any state exists; the
        only side effects are the default output folder and the caption
        picker."""
        if req.mode not in T2I_MODES:
            raise T2iRequestError(
                f"Unknown mode {req.mode!r}; use one of {', '.join(T2I_MODES)}"
            )
        profile = _profile(req.model)
        if req.seed_policy not in SEED_POLICIES:
            raise T2iRequestError(
                f"Unknown seed policy {req.seed_policy!r}; use one of "
                f"{', '.join(SEED_POLICIES)}"
            )
        seed = _check_seed(req.seed)
        try:
            # Any ratio will do: this only proves the budget is usable, so a
            # Random batch cannot fail on it mid-run.
            t2i_dims("1:1", req.megapixels, profile.dim_multiple)
        except T2iFormError as exc:
            raise T2iRequestError(str(exc)) from exc
        batch_size = _check_count(req.batch_size, "Batch size", cfg["max_batch_size"])
        per_step = _check_count(
            req.count_per_batch, "Count per batch", cfg["max_count_per_batch"]
        )
        if req.mode == "manual" and batch_size != 1:
            raise T2iRequestError(
                f"A Manual batch has a batch size of 1 (got {batch_size}); "
                "use Count per Batch for more images"
            )
        if req.seed_policy == "fixed" and per_step != 1:
            raise T2iRequestError(
                "A Fixed seed would render the same image "
                f"{per_step} times per step: count per batch must be 1 "
                "(pick Increment, Decrement or Randomize for more)"
            )

        if isinstance(req.preset_id, bool) or not isinstance(req.preset_id, int):
            raise T2iRequestError("preset_id must be a whole number")
        preset = await asyncio.to_thread(self.db.get_workflow_preset, req.preset_id)
        if preset is None:
            raise T2iRequestError(f"Preset {req.preset_id} does not exist")
        if preset.get("kind") != "t2i":
            raise T2iRequestError(
                f"Preset '{preset.get('name')}' is kind {preset.get('kind')!r}, "
                "not a text-to-image (t2i) workflow"
            )
        # BindingError propagates: the route answers 400 with its message.
        bindings = resolve_bindings(json.loads(preset["workflow_json"]), "t2i")
        loras = normalize_loras(req.loras)  # T2iFormError propagates the same way
        if loras and bindings.lora_stack is None:
            raise T2iRequestError(
                "This preset has no MS_LORA_STACK node; clear the lora "
                "list or register a workflow that has one"
            )

        picker: Optional[CaptionPicker] = None
        supplied = _supplies_first_step(req)
        if req.mode == "manual":
            if not isinstance(req.prompt, str) or not req.prompt.strip():
                raise T2iRequestError("Prompt is empty")
            _check_aspect(req.aspect_ratio)
        else:
            if supplied:
                _check_aspect(req.aspect_ratio)  # no CSV row to take one from
            if not await asyncio.to_thread(self.captions.available):
                reason = await asyncio.to_thread(self.captions.error)
                raise T2iRequestError(
                    f"caption CSV unavailable: {reason or 'unknown error'}"
                )
            # CaptionFilterError propagates (a bad filter, or no match).
            picker = await asyncio.to_thread(
                self.captions.picker, req.filter, random.Random()
            )

        root, prefix = await self._placement(cfg)

        requested = per_step if req.mode == "manual" else batch_size * per_step
        seeds = plan_seeds(req.seed_policy, seed, requested, random.Random())
        warnings: List[str] = []
        if len(seeds) < requested:
            warnings.append(
                f"seed range exhausted: {req.seed_policy} from {seed} allows only "
                f"{len(seeds)} image(s), so the run was shortened from "
                f"{requested} to {len(seeds)}"
            )
        # Increment/decrement know the seed after the run; fixed repeats its
        # own; random has none. None also means "the range ran out".
        following: Optional[int] = None
        if req.seed_policy != "random":
            following = next_seed(req.seed_policy, seeds[-1], random.Random())
        # A negative the workflow has no node for is dropped, once, loudly:
        # the user typed one (Manual, or a Random first step) or the model
        # writes one (each step a Random batch draws).
        typed_negative = isinstance(req.negative, str) and req.negative.strip() != ""
        if req.mode == "manual":
            has_negative = typed_negative
        else:
            drawn_steps = batch_size - (1 if supplied else 0)
            has_negative = (drawn_steps > 0 and profile.has_negative) or (
                supplied and typed_negative
            )
        if has_negative and bindings.negative is None:
            warnings.append(WARN_NEGATIVE_IGNORED)

        return _Batch(
            id=uuid.uuid4().hex,
            req=dataclasses.replace(req, loras=loras),
            profile=profile,
            bindings=bindings,
            loras=loras,
            seeds=seeds,
            per_step=per_step,
            total_steps=-(-len(seeds) // per_step) if req.mode == "random" else 1,
            next_seed=following,
            root=root,
            prefix=prefix,
            content_mode=cfg["content_mode"],
            identity=effective_identity(profile, cfg["identity"]),
            window=asyncio.Semaphore(cfg["window"]),
            picker=picker,
            warnings=warnings,
            started_at=datetime.now(timezone.utc).isoformat(),
            phase="prompting" if req.mode == "random" else "rendering",
            prompting=req.mode == "random",
        )

    async def start_batch(self, req: BatchRequest) -> BatchStarted:
        """Validate and plan a batch, register it and announce it.

        Every static problem raises before anything is created (a bad
        request leaves no batch and emits no event). A Random batch is
        refused while another one is active; Manual batches are unlimited.
        """
        cfg = self._get_config()
        batch = await self._plan_batch(req, cfg)
        # No await between this check and the registration below, so two
        # concurrent Random requests cannot both pass it.
        if batch.mode == "random":
            for other in self._batches.values():
                if other.mode == "random":
                    raise T2iConflictError(
                        f"random_batch_active: Random batch {other.id} is still "
                        "running; wait for it to finish or cancel it"
                    )
        self._batches[batch.id] = batch
        self._emit(
            "t2i",
            "batch_started",
            {
                "batch_id": batch.id,
                "mode": batch.mode,
                "total_steps": batch.total_steps,
                "total_images": batch.total_images,
            },
        )
        self._emit_progress(batch)
        batch.task = asyncio.create_task(
            self._run(batch), name=f"t2i-batch-{batch.id[:8]}"
        )
        return BatchStarted(
            batch_id=batch.id,
            total_images=batch.total_images,
            warnings=list(batch.warnings),
        )

    # ---- batch state as data ----------------------------------------------

    def _emit_progress(self, batch: _Batch) -> None:
        if batch.finished is not None:
            return  # the terminal frame has the final counters
        data: Dict[str, Any] = {
            "batch_id": batch.id,
            "phase": batch.phase,
            "images_done": batch.images_done,
            "images_failed": batch.images_failed,
            "images_total": batch.total_images,
            "next_seed": batch.next_seed,
        }
        if batch.last_error is not None:
            data["last_error"] = batch.last_error
        self._emit("t2i", "batch_progress", data)

    def active_batches(self) -> List[Dict[str, Any]]:
        """Every registered batch, oldest first, as JSON-ready rows."""
        return [
            {
                "batch_id": b.id,
                "mode": b.mode,
                "state": b.phase,
                "total_steps": b.total_steps,
                "step": copy.deepcopy(b.step),
                "images_total": b.total_images,
                "images_done": b.images_done,
                "images_failed": b.images_failed,
                "next_seed": b.next_seed,
                "started_at": b.started_at,
            }
            for b in self._batches.values()
        ]

    # ---- the run: prompts, then jobs ------------------------------------------

    @staticmethod
    def _reason(exc: BaseException) -> str:
        """One short line about ``exc``, for a warning."""
        return " ".join(str(exc).split())[:160] or type(exc).__name__

    async def _write_prompt(
        self, batch: _Batch, resolved_text: str
    ) -> Tuple[str, Optional[str], List[str]]:
        """``(prompt, negative, warnings)`` for one step of a running batch.

        An unattended run never dies on one bad VLM call: a call that fails
        is tried once more (starting the model again first), then the
        resolved caption is used and a warning says so. Cancellation is
        never swallowed, and neither is anything that is not a VLM failure.
        """
        profile = batch.profile

        def fall_back(warning: str) -> Tuple[str, Optional[str], List[str]]:
            prompt, negative = fallback_prompt(profile, resolved_text)
            return prompt, negative, [warning]

        vlm = self.get_vlm()
        if vlm is None:
            return fall_back(WARN_VLM_UNAVAILABLE)
        model_id = await self._usable_model(vlm)
        if model_id is None:
            return fall_back(WARN_VLM_UNAVAILABLE)
        system_prompt, user_prompt = compose_t2i_prompts(
            profile, resolved_text, batch.content_mode
        )
        failure: BaseException = VlmError("no attempt was made")
        for attempt in (1, 2):
            try:
                prompt, negative = await self._vlm_prompt(
                    vlm, model_id, profile, system_prompt, user_prompt
                )
                return prompt, negative, []
            except asyncio.CancelledError:
                raise
            except (VlmError, TimeoutError, RuntimeError) as exc:
                failure = exc
                logger.warning(
                    "t2i batch %s: VLM attempt %d failed: %s", batch.id, attempt, exc
                )
        return fall_back(
            f"VLM failed ({self._reason(failure)}) - used the resolved caption"
        )

    def _supplied_step(self, batch: _Batch) -> _Step:
        """The step the request itself carries -- a Manual batch's only step,
        or a Random batch's first: its prompt, ratio, caption and negative
        exactly as given. Nothing is drawn and the model is not asked."""
        req = batch.req
        aspect = str(req.aspect_ratio)
        width, height = t2i_dims(aspect, req.megapixels, batch.profile.dim_multiple)
        blank = not isinstance(req.negative, str) or not req.negative.strip()
        return _Step(
            index=0,
            caption=req.caption or "",
            aspect_ratio=aspect,
            seeds=batch.step_seeds(0),
            prompt=str(req.prompt),
            negative=None if blank else req.negative,
            warnings=[],
            width=width,
            height=height,
        )

    async def _random_step(self, batch: _Batch, index: int) -> _Step:
        if batch.picker is None:
            raise RuntimeError("a Random batch has no caption picker")
        row = await asyncio.to_thread(batch.picker.next)
        seeds = batch.step_seeds(index)
        warnings: List[str] = []
        aspect = row.aspect_ratio
        megapixels = batch.req.megapixels
        multiple = batch.profile.dim_multiple
        try:
            width, height = t2i_dims(aspect, megapixels, multiple)
        except T2iFormError as exc:
            # One odd row must not end an unattended run.
            warnings.append(
                f"aspect ratio {aspect!r} of this caption is unusable ({exc}); "
                "used 1:1"
            )
            aspect = "1:1"
            width, height = t2i_dims(aspect, megapixels, multiple)
        library, _ = await asyncio.to_thread(self.library.get)
        resolved = resolve_caption(row.caption, seeds[0], batch.identity, library)
        warnings.extend(resolved.warnings)
        prompt, negative, notes = await self._write_prompt(batch, resolved.text)
        warnings.extend(notes)
        return _Step(
            index=index,
            caption=row.caption,
            aspect_ratio=aspect,
            seeds=seeds,
            prompt=prompt,
            negative=negative,
            warnings=warnings,
            width=width,
            height=height,
        )

    def _announce_step(self, batch: _Batch, step: _Step) -> None:
        snapshot: Dict[str, Any] = {
            "step": step.index + 1,
            "total_steps": batch.total_steps,
            "caption": step.caption,
            "aspect_ratio": step.aspect_ratio,
            "seed": step.prompt_seed,
            "prompt": step.prompt,
            "negative": step.negative,
            "warnings": list(step.warnings),
        }
        batch.step = snapshot
        self._emit(
            "t2i", "batch_step", {"batch_id": batch.id, **copy.deepcopy(snapshot)}
        )

    def _set_phase(self, batch: _Batch, phase: str) -> None:
        if batch.phase != phase and batch.finished is None:
            batch.phase = phase
            self._emit_progress(batch)

    async def _produce(self, batch: _Batch, queue: "asyncio.Queue[_Step]") -> None:
        """Write the prompts, one step at a time, and hand each to the
        consumer. A Manual batch has one step and never asks the VLM; neither
        does the first step of a Random batch whose request supplies it."""
        try:
            for index in range(batch.total_steps):
                if batch.mode == "manual" or (
                    index == 0 and _supplies_first_step(batch.req)
                ):
                    step = self._supplied_step(batch)
                else:
                    step = await self._random_step(batch, index)
                self._announce_step(batch, step)
                await queue.put(step)
        finally:
            batch.prompting = False

    async def _unload_vlm(self, batch: _Batch) -> None:
        """Free the GPU for ComfyUI: shut the VLM down if a model is loaded
        -- unless another Random batch is still writing prompts with it.
        Only called when unloading is on. Failing to unload never stops the
        run."""
        vlm = self.get_vlm()
        if vlm is None or not vlm.model_id:
            return
        for other in self._batches.values():
            if other is not batch and other.mode == "random" and other.prompting:
                return
        try:
            await vlm.shutdown()
        except Exception as exc:
            logger.warning("could not unload the VLM before rendering: %s", exc)

    def _negative_for(self, batch: _Batch, step: _Step) -> Optional[str]:
        """The negative to send: only a workflow that binds MS_NEGATIVE can
        take one (the start warning already said so), and only a real one."""
        if batch.bindings.negative is None:
            return None
        if step.negative is None or not step.negative.strip():
            return None
        return step.negative

    def _register_job(self, batch: _Batch, job_id: int, step: _Step, seed: int) -> None:
        """Remember a submitted job: it owns a window slot until its terminal
        job_update, and the ingest will need what the dialog showed."""
        batch.pending.add(job_id)
        batch.unresolved.add(job_id)
        self._job_batch[job_id] = batch.id
        manual = batch.mode == "manual"
        self._job_meta[job_id] = {
            "mode": batch.mode,
            "filter": None if manual else copy.deepcopy(batch.req.filter),
            "model": batch.req.model,
            "caption": step.caption or None,
            "prompt_seed": step.prompt_seed,
            "aspect_ratio": step.aspect_ratio,
            "megapixels": batch.req.megapixels,
            "loras": [dict(entry) for entry in batch.loras],
            "prompt": step.prompt,
            # The Negative box as the dialog had it: Manual keeps what was
            # typed even if the workflow could not take it.
            "negative": batch.req.negative if manual else step.negative,
        }
        while len(self._job_meta) > _JOB_META_LIMIT:
            self._job_meta.pop(next(iter(self._job_meta)))

    def _release_slot(self, batch: _Batch, job_id: int) -> None:
        if job_id in batch.pending:
            batch.pending.discard(job_id)
            batch.window.release()

    @staticmethod
    async def _landed(submit: "Optional[asyncio.Future[int]]") -> Optional[int]:
        """The job id of a submit that was in flight, once it has landed;
        None if it failed (or never started)."""
        if submit is None:
            return None
        try:
            return await submit
        except BaseException:
            return None

    async def _submit_registered(
        self,
        batch: _Batch,
        step: _Step,
        seed: int,
        params: GenerationParams,
        target: Any,
    ) -> int:
        """comfy.submit, then remember the job in the same step of the loop:
        ComfyClient may announce the job's end a loop iteration after submit
        returns, and by then the runner has to know the job."""
        job_id = int(
            await self.comfy.submit(
                batch.req.preset_id,
                params,
                output_dir=target.directory,
                output_name=target.stem,
                t2i_batch_id=batch.id,
            )
        )
        self._register_job(batch, job_id, step, seed)
        return job_id

    async def _submit_one(self, batch: _Batch, step: _Step, seed: int) -> int:
        """Submit one image. Called holding a window slot: on success the
        slot belongs to the job (its terminal update frees it), on failure it
        is released here."""
        inflight: "Optional[asyncio.Future[int]]" = None
        try:
            params = GenerationParams(
                positive=step.prompt,
                seed=seed,
                width=step.width,
                height=step.height,
                batch_size=1,
                negative=self._negative_for(batch, step),
                loras=[dict(entry) for entry in batch.loras],
            )
            target = resolve_output_target(
                batch.root, batch.prefix, datetime.now(), self._next_output_number()
            )
            # Shielded: ComfyClient.submit writes the job row and only then
            # queues it, so a cancel landing in between must not lose the id
            # -- the row would sit queued and run after the next restart.
            submitting = asyncio.ensure_future(
                self._submit_registered(batch, step, seed, params, target)
            )
            inflight = submitting
            return await asyncio.shield(submitting)
        except asyncio.CancelledError:
            # A submit that landed registered its job (the cancel will reach
            # it, and the job owns the slot); one that failed never did.
            if await self._landed(inflight) is None:
                batch.window.release()
            raise
        except BaseException:
            batch.window.release()
            raise

    async def _consume(self, batch: _Batch, queue: "asyncio.Queue[_Step]") -> None:
        """Submit every image of every step, keeping at most ``window`` jobs
        unfinished."""
        for _ in range(batch.total_steps):
            step = await queue.get()
            self._set_phase(batch, "rendering")
            for seed in step.seeds:
                await batch.window.acquire()
                await self._submit_one(batch, step, seed)

    @staticmethod
    async def _await_all(*tasks: "asyncio.Task[None]") -> None:
        """Wait for every task. If one fails, or this coroutine is cancelled,
        the rest are cancelled and awaited before the error is re-raised, so
        nothing is left running."""
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _run(self, batch: _Batch) -> None:
        """The batch's background task: write the prompts, then submit the
        jobs -- in that order when unloading is on, overlapped when it is off
        (spec 6.3). Anything that goes wrong ends the batch with one
        ``batch_error``."""
        tag = batch.id[:8]
        try:
            # With unloading on every prompt is written before the first job
            # goes out, so the queue must hold every step; with it off the
            # writer may only run a step or two ahead of the renderer.
            queue: "asyncio.Queue[_Step]" = asyncio.Queue(
                maxsize=batch.total_steps if self.unload_vlm_during_generation else 1
            )
            producer = asyncio.create_task(
                self._produce(batch, queue), name=f"t2i-produce-{tag}"
            )
            if self.unload_vlm_during_generation:
                await self._await_all(producer)
                self._set_phase(batch, "rendering")
                await self._unload_vlm(batch)
                await self._consume(batch, queue)
            else:
                consumer = asyncio.create_task(
                    self._consume(batch, queue), name=f"t2i-consume-{tag}"
                )
                await self._await_all(producer, consumer)
        except asyncio.CancelledError:
            raise  # cancel_batch / aclose own what happens next
        except Exception as exc:
            logger.warning("t2i batch %s failed: %s", batch.id, exc, exc_info=True)
            await self._abort_batch(batch, "error", self._reason(exc))

    # ---- ending a batch -----------------------------------------------------------

    def _retire(self, batch: _Batch, kind: str, error: Optional[str] = None) -> None:
        """Emit the batch's one terminal frame and move it out of the active
        list (the last few finished batches stay known)."""
        event = {
            "complete": "batch_complete",
            "cancelled": "batch_cancelled",
            "error": "batch_error",
        }[kind]
        data: Dict[str, Any] = {
            "batch_id": batch.id,
            "images_done": batch.images_done,
            "images_failed": batch.images_failed,
            "images_total": batch.total_images,
            # Whatever neither rendered nor failed: cancelled, or never submitted.
            "images_cancelled": max(
                0, batch.total_images - batch.images_done - batch.images_failed
            ),
            "next_seed": batch.next_seed,
        }
        if error is not None:
            data["error"] = error
        if batch.last_error is not None:
            data["last_error"] = batch.last_error
        self._emit("t2i", event, data)
        for job_id in sorted(batch.pending | batch.unresolved):
            self._release_slot(batch, job_id)
            self._job_batch.pop(job_id, None)
            self._job_meta.pop(job_id, None)
        # Nothing is owed once the batch is over: a straggler's event finds
        # nothing to account for (its image is still kept).
        batch.unresolved.clear()
        batch.ingesting.clear()
        self._batches.pop(batch.id, None)
        self._finished[batch.id] = batch
        while len(self._finished) > _FINISHED_KEEP:
            self._finished.popitem(last=False)
        batch.picker = None  # let the caption index go

    async def _abort_batch(
        self, batch: _Batch, kind: str, error: Optional[str] = None
    ) -> bool:
        """Stop a batch for good: cancel its run, cancel every job still
        unfinished in ComfyUI, give back the window and emit the terminal
        frame. False if the batch is already over."""
        if batch.finished is not None:
            return False
        # First, before any await: from here nothing else may emit a
        # terminal frame for this batch.
        batch.finished = kind
        task = batch.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.wait({task})
        targets = sorted(batch.pending)
        if targets:
            results = await asyncio.gather(
                *(self.comfy.cancel(job_id) for job_id in targets),
                return_exceptions=True,
            )
            for job_id, result in zip(targets, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "could not cancel t2i job %s of batch %s: %s",
                        job_id,
                        batch.id,
                        result,
                    )
        # The cancelled jobs' own updates have usually done this already;
        # a cancel that failed has not, and must leave nothing behind either.
        for job_id in targets:
            self._release_slot(batch, job_id)
            self._job_batch.pop(job_id, None)
            self._job_meta.pop(job_id, None)
        self._retire(batch, kind, error)
        return True

    async def cancel_batch(self, batch_id: str) -> bool:
        """Cancel a batch: stop its run (even mid VLM call) and cancel every
        unfinished job. False if it is known but already over; unknown ids
        raise ``T2iNotFoundError``."""
        batch = self._batches.get(batch_id)
        if batch is None:
            if batch_id in self._finished:
                return False
            raise T2iNotFoundError(f"No t2i batch {batch_id}")
        # Shielded: a caller that goes away mid-cancel must not leave the
        # batch half-cancelled.
        return await asyncio.shield(self._abort_batch(batch, "cancelled"))

    async def wait_batch(self, batch_id: str) -> None:
        """Wait for a batch's run to end: every prompt written and every job
        submitted -- not for the jobs to finish. Returns at once for a batch
        that is already over."""
        batch = self._batches.get(batch_id) or self._finished.get(batch_id)
        if batch is None:
            raise T2iNotFoundError(f"No t2i batch {batch_id}")
        task = batch.task
        if task is not None and not task.done():
            await asyncio.wait({task})

    # ---- job events ---------------------------------------------------------------

    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Registered via comfy_client.on_job_event. Sync; never raises."""
        try:
            if event == "job_update":
                self._on_job_update(payload)
            elif event == "job_outputs":
                self._on_job_outputs(payload)
        except Exception:
            logger.warning("t2i job event handling failed", exc_info=True)

    def _on_job_update(self, payload: Dict[str, Any]) -> None:
        """A job ended: free its window slot and account for it."""
        state = payload.get("state")
        if state not in _TERMINAL_JOB_STATES:
            return
        job_id = payload.get("job_id")
        if not isinstance(job_id, int):
            return
        batch_id = self._job_batch.pop(job_id, None)
        if batch_id is None:
            return  # not ours, or already handled
        if state != "done":
            # A done job's snapshot belongs to its ingest (job_outputs comes
            # first and the ingest may still be running); the others end here.
            self._job_meta.pop(job_id, None)
        batch = self._batches.get(batch_id)
        if batch is None:
            return
        self._release_slot(batch, job_id)
        if batch.finished is not None:
            return  # winding down: the abort owns the counters
        if state == "failed":
            self._settle(
                batch, job_id, failure=str(payload.get("error") or "the job failed")
            )
        elif state == "cancelled":
            self._settle(batch, job_id, cancelled=True)
        elif job_id not in batch.ingesting:
            # ComfyClient announces a job's outputs before it marks it done,
            # so a done job that is neither being ingested nor already
            # accounted for has produced nothing to wait for.
            self._settle(batch, job_id, failure="the job finished without an image")

    def _on_job_outputs(self, payload: Dict[str, Any]) -> None:
        job_id = payload.get("job_id")
        if not isinstance(job_id, int):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("t2i handle_job_event: no running loop", exc_info=True)
            return
        # Read now, hand to the task: by the time it runs the job's done
        # update may already have dropped the link, and the snapshot is the
        # ingest's to take (spec 6.4).
        batch_id = self._job_batch.get(job_id)
        meta = self._job_meta.pop(job_id, None)
        batch = self._batches.get(batch_id) if batch_id is not None else None
        if batch is not None:
            batch.ingesting.add(job_id)
        task = loop.create_task(
            self._ingest_outputs(payload, batch_id, meta), name=f"t2i-ingest-{job_id}"
        )
        self._ingest_tasks.add(task)
        task.add_done_callback(self._ingest_tasks.discard)

    def _form_state(
        self,
        job: Dict[str, Any],
        meta: Optional[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """The dialog exactly as submitted, for this image; None when the
        runner has no snapshot (after a restart) or it does not validate --
        an image is never lost over its form."""
        if meta is None:
            return None
        try:
            return build_form_state(
                mode=meta.get("mode"),
                filter=meta.get("filter"),
                caption=meta.get("caption"),
                model=meta.get("model"),
                preset_id=job.get("preset_id"),
                megapixels=meta.get("megapixels"),
                aspect_ratio=meta.get("aspect_ratio"),
                seed=params.get("seed"),
                prompt=meta.get("prompt"),
                negative=meta.get("negative"),
                loras=meta.get("loras"),
            )
        except T2iFormError as exc:
            logger.warning(
                "t2i ingest: the form state of job %s is unusable (%s); "
                "stored without one",
                job.get("id"),
                exc,
            )
            return None

    async def _insert_images(
        self,
        job: Dict[str, Any],
        meta: Optional[Dict[str, Any]],
        batch_id: str,
        files: List[Any],
    ) -> List[str]:
        """One ``t2i_images`` row per file: the as-rendered facts from the
        job's stored params and the snapshot. Returns the POSIX paths that
        got a row; a file that cannot be recorded is logged and skipped."""
        try:
            params = json.loads(job["params"])
        except (TypeError, ValueError):
            params = {}
        if not isinstance(params, dict):
            params = {}
        known = meta if meta is not None else {}
        form = self._form_state(job, meta, params)
        render_s = render_seconds(job.get("started_at"), job.get("finished_at"))
        inserted: List[str] = []
        for entry in files:
            posix = to_posix_path(entry)
            try:
                await asyncio.to_thread(
                    self.db.create_t2i_image,
                    file_path=posix,
                    batch_id=batch_id,
                    model=known.get("model"),
                    preset_id=job.get("preset_id"),
                    caption=known.get("caption"),
                    prompt_used=params.get("positive"),
                    negative_used=params.get("negative"),
                    seed=params.get("seed"),
                    prompt_seed=known.get("prompt_seed"),
                    width=params.get("width"),
                    height=params.get("height"),
                    megapixels=known.get("megapixels"),
                    aspect_ratio=known.get("aspect_ratio"),
                    loras=(
                        known.get("loras") if meta is not None else params.get("loras")
                    ),
                    render_s=render_s,
                    comfy_prompt_id=job.get("comfy_prompt_id"),
                    form_state=form,
                )
            except sqlite3.IntegrityError:
                logger.warning("t2i ingest: %s already has a row; skipped", posix)
                continue
            except Exception:
                logger.warning(
                    "Could not ingest t2i image %s for job %s",
                    entry,
                    job.get("id"),
                    exc_info=True,
                )
                continue
            inserted.append(posix)
        return inserted

    async def _ingest_outputs(
        self,
        payload: Dict[str, Any],
        batch_id: Optional[str],
        meta: Optional[Dict[str, Any]],
    ) -> None:
        """Record the images a job produced and account for the job.
        ``batch_id`` is what the runner remembered when the outputs were
        announced (None after a restart); it is what lets a failed database
        read still fail the right job. The row's own ``t2i_batch_id`` wins."""
        job_id = payload["job_id"]
        files = list(payload.get("files") or [])
        inserted: List[str] = []
        try:
            job = await asyncio.to_thread(self.db.get_generation_job, job_id)
            if job is None or not job.get("t2i_batch_id"):
                return  # a job of another feature: not ours to account for
            batch_id = str(job["t2i_batch_id"])
            inserted = await self._insert_images(job, meta, batch_id, files)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Could not ingest the outputs of t2i job %s: %s",
                job_id,
                exc,
                exc_info=True,
            )
        self._account_ingest(batch_id, job_id, inserted, bool(files))

    def _account_ingest(
        self, batch_id: Optional[str], job_id: int, inserted: List[str], had_files: bool
    ) -> None:
        """Announce the new images, then account for the job -- unless its
        batch is over (a restart, a cancel): the images are kept and shown,
        but no counter moves and no second terminal frame can happen."""
        if inserted and batch_id is not None:
            self._emit(
                "t2i",
                "t2i_images_changed",
                {"batch_id": batch_id, "files": [to_native_path(p) for p in inserted]},
            )
        batch = self._batches.get(batch_id) if batch_id is not None else None
        if batch is None or batch.finished is not None:
            return
        if inserted:
            self._settle(batch, job_id, images=len(inserted))
        elif had_files:
            self._settle(batch, job_id, failure="the job's image could not be recorded")
        else:
            self._settle(batch, job_id, failure="the job produced no image")

    def _settle(
        self,
        batch: _Batch,
        job_id: int,
        *,
        images: int = 0,
        failure: Optional[str] = None,
        cancelled: bool = False,
    ) -> None:
        """Account for one job, once; the last one ends the batch."""
        if job_id not in batch.unresolved:
            return
        batch.unresolved.discard(job_id)
        batch.ingesting.discard(job_id)
        batch.resolved += 1
        if not cancelled:
            if failure is not None:
                batch.images_failed += 1
                batch.last_error = failure
            else:
                batch.images_done += images
            self._emit_progress(batch)
        if batch.resolved >= batch.total_images:
            batch.finished = "complete"
            self._retire(batch, "complete")

    async def aclose(self) -> None:
        """Stop every batch's run and let the ingests in flight finish, so no
        image that ComfyUI already produced is lost. The jobs already in
        ComfyUI are left to ComfyClient."""
        tasks = [b.task for b in self._batches.values() if b.task and not b.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        ingests = [t for t in self._ingest_tasks if not t.done()]
        if ingests:
            await asyncio.gather(*ingests, return_exceptions=True)
