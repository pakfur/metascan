"""Resolve the MS_* node-title contract in a ComfyUI API-format workflow.

Metascan binds to nodes by their ``_meta.title`` rather than by node id
because ComfyUI renumbers node ids when a workflow is re-saved. Titles
survive that, need no mapping UI, and document the contract inside
ComfyUI itself.

This module is pure: no network, no filesystem, no database. Everything
here is exercised by dict literals in tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Titles metascan looks for. Values are the widget keys each node must
# expose in its ``inputs`` block for the binding to be usable.
_REQUIRED_WIDGETS: Dict[str, Tuple[str, ...]] = {
    "MS_POSITIVE": ("text",),
    "MS_NEGATIVE": ("text",),
    "MS_LATENT": ("width", "height", "batch_size"),
    "MS_LORA": ("lora_name", "strength_model", "strength_clip"),
    # MS_LORA_STACK has no required widgets: a stackable loader's lora_N
    # entries are dynamic (rgthree Power Lora Loader), so an empty stack
    # node is a valid binding target.
    "MS_REF_IMAGE": ("image",),
    "MS_REF_IMAGE_2": ("image",),
    "MS_REF_IMAGE_3": ("image",),
    "MS_FIRST_FRAME": ("image",),
    "MS_LAST_FRAME": ("image",),
    "MS_AUDIO": ("audio",),
    "MS_AUDIO_2": ("audio",),
    "MS_DURATION": ("value",),
    "MS_RESOLUTION": ("width", "height"),
    # Sampler step count. Only the i2v flow's High quality preset is ever
    # written; a step-distilled (turbo) workflow must NOT carry this title.
    "MS_STEPS": ("steps",),
    "MS_SOURCE_IMAGE": ("image",),
    "MS_SCALE": ("value",),
    # MS_DENOISE is special-cased: a sampler's "denoise" (KSampler, Ultimate SD
    # Upscale) or a primitive's "value" feeding one. A node has one title, so
    # a sampler already titled MS_SEED takes its denoise from a primitive.
    # MS_MODEL is special-cased like MS_SEED: the loader's "unet_name"
    # (UNETLoader) or "ckpt_name" (CheckpointLoaderSimple).
    # MS_SEED is special-cased: either "seed" or "noise_seed".
    # MS_SOURCE_VIDEO likewise: "video" (VHS_LoadVideo) or "file" (LoadVideo).
    # MS_SAVE is an output node; metascan only needs its id.
}

_REQUIRED_TITLES: Dict[str, Tuple[str, ...]] = {
    "t2i": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE"),
    "ref": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE", "MS_REF_IMAGE"),
    "ref2v": ("MS_POSITIVE", "MS_SEED", "MS_SAVE"),
    # An upscale needs no prompt or seed: most upscale graphs have neither.
    "upscale_image": ("MS_SOURCE_IMAGE", "MS_SAVE"),
    "upscale_video": ("MS_SOURCE_VIDEO", "MS_SAVE"),
}

KINDS: Tuple[str, ...] = tuple(_REQUIRED_TITLES)


class BindingError(ValueError):
    """A workflow does not satisfy the MS_* contract.

    Raised at preset-registration time, never mid-run — a bad setup must
    surface before dozens of jobs are queued against it.
    """


@dataclass(frozen=True)
class Bindings:
    """Resolved node ids (and the seed widget name) for one preset.

    ``positive`` and ``seed`` are None only for the upscale kinds.
    """

    positive: Optional[str]
    seed: Optional[str]
    seed_widget: Optional[str]
    latent: Optional[str]
    save: str
    negative: Optional[str] = None
    lora: Optional[str] = None
    lora_stack: Optional[str] = None
    ref_image: Optional[str] = None
    ref_image_2: Optional[str] = None
    ref_image_3: Optional[str] = None
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    audio: Optional[str] = None
    audio_2: Optional[str] = None
    duration: Optional[str] = None
    resolution: Optional[str] = None
    steps: Optional[str] = None
    source_image: Optional[str] = None
    source_video: Optional[str] = None
    source_video_widget: Optional[str] = None
    scale: Optional[str] = None
    denoise: Optional[str] = None
    denoise_widget: Optional[str] = None
    model: Optional[str] = None
    model_widget: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Bindings":
        return cls(**json.loads(raw))


def _titles(workflow: Dict[str, Any]) -> Dict[str, str]:
    """Map MS_* title -> node id, rejecting duplicates."""
    found: Dict[str, str] = {}
    duplicates: List[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        title = (node.get("_meta") or {}).get("title")
        if not isinstance(title, str) or not title.startswith("MS_"):
            continue
        if title in found:
            duplicates.append(title)
        else:
            found[title] = str(node_id)
    if duplicates:
        raise BindingError(
            "Duplicate MS_* node titles: "
            + ", ".join(sorted(set(duplicates)))
            + ". Each title may appear on at most one node."
        )
    return found


def _inputs(workflow: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    node = workflow.get(node_id) or {}
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _check_widgets(workflow: Dict[str, Any], title: str, node_id: str) -> None:
    required = _REQUIRED_WIDGETS.get(title)
    if not required:
        return
    inputs = _inputs(workflow, node_id)
    missing = [w for w in required if w not in inputs]
    if missing:
        raise BindingError(
            f"Node {node_id} titled {title} is missing required widget(s): "
            + ", ".join(missing)
        )


def _seed_widget(workflow: Dict[str, Any], node_id: str) -> str:
    inputs = _inputs(workflow, node_id)
    for candidate in ("seed", "noise_seed"):
        if candidate in inputs:
            return candidate
    raise BindingError(
        f"Node {node_id} titled MS_SEED exposes neither a 'seed' nor a "
        "'noise_seed' widget."
    )


def _source_video_widget(workflow: Dict[str, Any], node_id: str) -> str:
    inputs = _inputs(workflow, node_id)
    for candidate in ("video", "file"):
        if candidate in inputs:
            return candidate
    raise BindingError(
        f"Node {node_id} titled MS_SOURCE_VIDEO exposes neither a 'video' nor a "
        "'file' widget."
    )


MODEL_WIDGETS: Tuple[str, ...] = ("unet_name", "ckpt_name")
DENOISE_WIDGETS: Tuple[str, ...] = ("denoise", "value")


def _denoise_widget(workflow: Dict[str, Any], node_id: str) -> str:
    inputs = _inputs(workflow, node_id)
    for candidate in DENOISE_WIDGETS:
        if candidate in inputs and not isinstance(inputs[candidate], list):
            return candidate
    raise BindingError(
        f"Node {node_id} titled MS_DENOISE exposes neither a 'denoise' nor a "
        "'value' widget (a linked input is not a widget)."
    )


def _model_widget(workflow: Dict[str, Any], node_id: str) -> str:
    inputs = _inputs(workflow, node_id)
    for candidate in MODEL_WIDGETS:
        if candidate in inputs:
            return candidate
    raise BindingError(
        f"Node {node_id} titled MS_MODEL exposes neither a 'unet_name' nor a "
        "'ckpt_name' widget."
    )


def baked_model(workflow: Dict[str, Any], bindings: "Bindings") -> Optional[str]:
    """The model name the MS_MODEL node holds, or None."""
    if bindings.model is None or bindings.model_widget is None:
        return None
    value = _inputs(workflow, bindings.model).get(bindings.model_widget)
    return value if isinstance(value, str) else None


def resolve_bindings(workflow: Dict[str, Any], kind: str) -> Bindings:
    """Resolve MS_* titles in ``workflow`` into a Bindings record.

    Raises BindingError listing every problem found, so a user fixing
    their workflow sees the full list rather than one error per attempt.
    """
    if kind not in _REQUIRED_TITLES:
        raise BindingError(
            f"Unknown preset kind {kind!r}. Expected one of: " + ", ".join(KINDS)
        )

    found = _titles(workflow)

    missing = [t for t in _REQUIRED_TITLES[kind] if t not in found]
    if missing:
        raise BindingError(
            f"Workflow is missing required node title(s) for kind {kind!r}: "
            + ", ".join(missing)
            + ". Title the corresponding nodes in ComfyUI and re-export "
            "in API format."
        )

    for title, node_id in found.items():
        _check_widgets(workflow, title, node_id)

    seed = found.get("MS_SEED")
    source_video = found.get("MS_SOURCE_VIDEO")
    model = found.get("MS_MODEL")
    denoise = found.get("MS_DENOISE")
    return Bindings(
        positive=found.get("MS_POSITIVE"),
        seed=seed,
        seed_widget=_seed_widget(workflow, seed) if seed else None,
        latent=found.get("MS_LATENT"),
        save=found["MS_SAVE"],
        negative=found.get("MS_NEGATIVE"),
        lora=found.get("MS_LORA"),
        lora_stack=found.get("MS_LORA_STACK"),
        ref_image=found.get("MS_REF_IMAGE"),
        ref_image_2=found.get("MS_REF_IMAGE_2"),
        ref_image_3=found.get("MS_REF_IMAGE_3"),
        first_frame=found.get("MS_FIRST_FRAME"),
        last_frame=found.get("MS_LAST_FRAME"),
        audio=found.get("MS_AUDIO"),
        audio_2=found.get("MS_AUDIO_2"),
        duration=found.get("MS_DURATION"),
        resolution=found.get("MS_RESOLUTION"),
        steps=found.get("MS_STEPS"),
        source_image=found.get("MS_SOURCE_IMAGE"),
        source_video=source_video,
        source_video_widget=(
            _source_video_widget(workflow, source_video) if source_video else None
        ),
        scale=found.get("MS_SCALE"),
        denoise=denoise,
        denoise_widget=_denoise_widget(workflow, denoise) if denoise else None,
        model=model,
        model_widget=_model_widget(workflow, model) if model else None,
    )


@dataclass(frozen=True)
class GenerationParams:
    """One generation request's override values.

    ``ref_image`` is a ComfyUI-side filename as returned by
    ``POST /upload/image`` — not a local path.

    ``positive`` / ``seed`` None and ``width`` / ``height`` 0 keep what the
    graph has baked in (an upscale drives only what the user chose).
    """

    positive: Optional[str]
    seed: Optional[int]
    width: int
    height: int
    batch_size: int
    negative: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    loras: List[Dict[str, Any]] = field(default_factory=list)
    ref_image: Optional[str] = None
    ref_images: List[str] = field(default_factory=list)
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    audio_refs: List[str] = field(default_factory=list)
    duration_s: Optional[float] = None
    steps: Optional[int] = None
    source_image: Optional[str] = None
    source_video: Optional[str] = None
    scale: Optional[float] = None
    denoise: Optional[float] = None
    model: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "GenerationParams":
        return cls(**json.loads(raw))


_LORA_ENTRY_KEY = re.compile(r"^lora_\d+$")


def _is_lora_entry(key: str, value: Any) -> bool:
    """True for a stackable-loader lora entry input.

    Matches both the ``lora_N`` key convention and any dict value carrying
    a ``lora`` widget (covers stackers with other key spellings), while
    leaving link inputs and UI-only widgets (header, add-button) alone.
    """
    if _LORA_ENTRY_KEY.match(key):
        return True
    return isinstance(value, dict) and "lora" in value


def apply_overrides(
    workflow: Dict[str, Any],
    bindings: Bindings,
    params: GenerationParams,
) -> Dict[str, Any]:
    """Return a deep copy of ``workflow`` with bound widgets overwritten.

    A parameter supplied without a corresponding binding raises rather
    than being silently dropped — quietly discarding a negative prompt or
    a LoRA would produce a wrong image with no signal to the user.
    """
    graph: Dict[str, Any] = json.loads(json.dumps(workflow))

    def write(node_id: str, widget: str, value: Any) -> None:
        graph[node_id]["inputs"][widget] = value

    if params.positive is not None:
        if bindings.positive is None:
            raise BindingError(
                "A prompt was supplied but this workflow has no MS_POSITIVE node."
            )
        write(bindings.positive, "text", params.positive)
    if params.seed is not None:
        if bindings.seed is None or bindings.seed_widget is None:
            raise BindingError(
                "A seed was supplied but this workflow has no MS_SEED node."
            )
        write(bindings.seed, bindings.seed_widget, params.seed)
    sized = params.width > 0 and params.height > 0
    if bindings.latent is not None and sized:
        write(bindings.latent, "width", params.width)
        write(bindings.latent, "height", params.height)
        write(bindings.latent, "batch_size", params.batch_size)
    # ref2v graphs usually have no MS_LATENT; MS_RESOLUTION is the
    # video-side dimension carrier. A workflow with neither keeps its
    # baked-in resolution rather than raising -- the MS_DURATION
    # precedent, so existing presets need no retrofit.
    if bindings.resolution is not None and sized:
        write(bindings.resolution, "width", params.width)
        write(bindings.resolution, "height", params.height)

    if params.negative is not None:
        if bindings.negative is None:
            raise BindingError(
                "A negative prompt was supplied but this workflow has no "
                "MS_NEGATIVE node. Add one, or clear the negative prompt."
            )
        write(bindings.negative, "text", params.negative)

    if params.lora_name is not None:
        if bindings.lora is None:
            raise BindingError(
                "A LoRA was supplied but this workflow has no MS_LORA node. "
                "Add one, or clear the LoRA."
            )
        strength = 0.8 if params.lora_strength is None else params.lora_strength
        write(bindings.lora, "lora_name", params.lora_name)
        write(bindings.lora, "strength_model", strength)
        write(bindings.lora, "strength_clip", strength)

    if params.loras and bindings.lora_stack is None:
        raise BindingError(
            f"{len(params.loras)} stack lora(s) were supplied but this "
            "workflow has no MS_LORA_STACK node. Add a stackable lora "
            "loader (e.g. Power Lora Loader) titled MS_LORA_STACK, or "
            "clear the shot's loras."
        )
    if bindings.lora_stack is not None:
        # Metascan owns the stack node: baked-in entries are replaced with
        # exactly the supplied list (possibly none), never appended to.
        inputs = graph[bindings.lora_stack]["inputs"]
        for key in [k for k, v in inputs.items() if _is_lora_entry(k, v)]:
            del inputs[key]
        for i, entry in enumerate(params.loras, start=1):
            inputs[f"lora_{i}"] = {
                "on": True,
                "lora": entry["name"],
                "strength": entry["strength"],
            }

    if params.ref_image is not None and params.ref_images:
        raise BindingError("Both ref_image and ref_images were supplied; use only one.")

    if params.ref_image is not None:
        if bindings.ref_image is None:
            raise BindingError(
                "A reference image was supplied but this workflow has no "
                "MS_REF_IMAGE node. Register it with kind='ref'."
            )
        write(bindings.ref_image, "image", params.ref_image)

    if params.ref_images:
        slots = [
            b
            for b in (bindings.ref_image, bindings.ref_image_2, bindings.ref_image_3)
            if b is not None
        ]
        if len(params.ref_images) > len(slots):
            raise BindingError(
                f"{len(params.ref_images)} reference images supplied but the "
                f"workflow has only {len(slots)} MS_REF_IMAGE slot(s)"
            )
        for node_id, value in zip(slots, params.ref_images):
            write(node_id, "image", value)

    if params.first_frame is not None:
        if bindings.first_frame is None:
            raise BindingError(
                "A first frame was supplied but this workflow has no "
                "MS_FIRST_FRAME node."
            )
        write(bindings.first_frame, "image", params.first_frame)

    if params.last_frame is not None:
        if bindings.last_frame is None:
            raise BindingError(
                "A last frame was supplied but this workflow has no "
                "MS_LAST_FRAME node."
            )
        write(bindings.last_frame, "image", params.last_frame)

    if params.audio_refs:
        slots = [b for b in (bindings.audio, bindings.audio_2) if b is not None]
        if len(params.audio_refs) > len(slots):
            raise BindingError(
                f"{len(params.audio_refs)} audio references supplied but the "
                f"workflow has only {len(slots)} MS_AUDIO slot(s)"
            )
        for node_id, value in zip(slots, params.audio_refs):
            write(node_id, "audio", value)

    if params.duration_s is not None:
        if bindings.duration is None:
            raise BindingError(
                "A duration was supplied but this workflow has no " "MS_DURATION node."
            )
        write(bindings.duration, "value", params.duration_s)

    if params.steps is not None:
        if bindings.steps is None:
            raise BindingError(
                "A step count was supplied but this workflow has no MS_STEPS node."
            )
        write(bindings.steps, "steps", int(params.steps))

    if params.source_image is not None:
        if bindings.source_image is None:
            raise BindingError(
                "A source image was supplied but this workflow has no "
                "MS_SOURCE_IMAGE node."
            )
        write(bindings.source_image, "image", params.source_image)

    if params.source_video is not None:
        if bindings.source_video is None or bindings.source_video_widget is None:
            raise BindingError(
                "A source video was supplied but this workflow has no "
                "MS_SOURCE_VIDEO node."
            )
        write(bindings.source_video, bindings.source_video_widget, params.source_video)

    if params.scale is not None:
        if bindings.scale is None:
            raise BindingError(
                "A scale was supplied but this workflow has no MS_SCALE node."
            )
        write(bindings.scale, "value", float(params.scale))

    if params.denoise is not None:
        if bindings.denoise is None or bindings.denoise_widget is None:
            raise BindingError(
                "A denoise strength was supplied but this workflow has no "
                "MS_DENOISE node."
            )
        if not 0.0 <= float(params.denoise) <= 1.0:
            raise BindingError(f"Denoise {params.denoise} is outside 0..1")
        write(bindings.denoise, bindings.denoise_widget, float(params.denoise))

    if params.model is not None:
        if bindings.model is None or bindings.model_widget is None:
            raise BindingError(
                "A model was supplied but this workflow has no MS_MODEL node."
            )
        write(bindings.model, bindings.model_widget, params.model)

    return graph


__all__ = [
    "BindingError",
    "Bindings",
    "GenerationParams",
    "KINDS",
    "DENOISE_WIDGETS",
    "MODEL_WIDGETS",
    "apply_overrides",
    "baked_model",
    "resolve_bindings",
]
