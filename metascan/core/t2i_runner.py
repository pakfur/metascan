"""Text-to-image runner: caption resolution, prompt writing, batch planning.

Layering mirrors I2vRunner: this is the only module that knows about
``t2i_images``; ``ComfyClient`` stays a generic job driver. Correlation
flows one way -- a batch stamps ``t2i_batch_id`` on every job it submits.

A batch is planned completely before anything happens (``start_batch``):
every static problem is a named error, the whole seed sequence is known,
and so is the seed the dialog should show afterwards (``next_seed``). Only
then is state created and events are emitted.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from metascan.core.comfy_bindings import Bindings, resolve_bindings
from metascan.core.i2v_output import I2vOutputError, resolve_output_target
from metascan.core.t2i_captions import CaptionPicker, CaptionStore
from metascan.core.t2i_characters import ResolvedCaption, resolve_caption
from metascan.core.t2i_form import (
    ASPECT_RATIOS,
    SEED_MAX,
    SEED_POLICIES,
    T2I_MODES,
    T2iFormError,
    next_seed,
    normalize_loras,
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
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

EventCb = Callable[[str, str, Dict[str, Any]], None]

# Warning texts the dialog shows verbatim.
WARN_VLM_UNAVAILABLE = "VLM unavailable - used the resolved caption"
WARN_NEGATIVE_IGNORED = "negative prompt ignored: the workflow has no MS_NEGATIVE node"

_VLM_TEMPERATURE = 0.6
_VLM_TIMEOUT_S = 240.0


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
    # Manual fields
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
    ) -> None:
        self.db = db
        self.comfy = comfy
        self.get_vlm = get_vlm
        self.captions = captions  # public: the routes read them too
        self.library = library
        self.output_root = Path(output_root)
        self.unload_vlm_during_generation = unload_vlm_during_generation
        # Called afresh for every prompt and every batch, so an edit to the
        # config file applies to the next request without a restart.
        self._get_config = get_config
        self._on_event: List[EventCb] = []
        self._batches: Dict[str, _Batch] = {}
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

    async def _vlm_prompt(
        self,
        vlm: Any,
        model_id: str,
        profile: T2iModelProfile,
        system_prompt: str,
        user_prompt: str,
    ) -> Tuple[str, Optional[str]]:
        """One VLM round trip: start the model, ask, split off the negative.
        Raises whatever the client raises (VlmError, TimeoutError, ...)."""
        await vlm.ensure_started(model_id)
        raw = await vlm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=_VLM_TEMPERATURE,
            max_tokens=profile.max_tokens,
            timeout=_VLM_TIMEOUT_S,
        )
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
        model_id: Optional[str] = None
        if vlm is not None:
            try:
                model_id = pick_vlm_model(vlm)
            except VlmSelectError:
                model_id = None
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
        if req.mode == "manual":
            if not isinstance(req.prompt, str) or not req.prompt.strip():
                raise T2iRequestError("Prompt is empty")
            if req.aspect_ratio not in ASPECT_RATIOS:
                raise T2iRequestError(
                    f"Aspect ratio {req.aspect_ratio!r} is not supported; use one "
                    f"of {', '.join(ASPECT_RATIOS)}"
                )
        else:
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
        # the user typed one (Manual) or the model writes one (Random).
        if req.mode == "manual":
            has_negative = isinstance(req.negative, str) and req.negative.strip() != ""
        else:
            has_negative = profile.has_negative
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
        return BatchStarted(
            batch_id=batch.id,
            total_images=batch.total_images,
            warnings=list(batch.warnings),
        )

    # ---- batch state as data ----------------------------------------------

    def _emit_progress(self, batch: _Batch) -> None:
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
