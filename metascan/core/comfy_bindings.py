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
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Titles metascan looks for. Values are the widget keys each node must
# expose in its ``inputs`` block for the binding to be usable.
_REQUIRED_WIDGETS: Dict[str, Tuple[str, ...]] = {
    "MS_POSITIVE": ("text",),
    "MS_NEGATIVE": ("text",),
    "MS_LATENT": ("width", "height", "batch_size"),
    "MS_LORA": ("lora_name", "strength_model", "strength_clip"),
    "MS_REF_IMAGE": ("image",),
    "MS_REF_IMAGE_2": ("image",),
    "MS_REF_IMAGE_3": ("image",),
    "MS_FIRST_FRAME": ("image",),
    "MS_LAST_FRAME": ("image",),
    "MS_AUDIO": ("audio",),
    "MS_AUDIO_2": ("audio",),
    "MS_DURATION": ("value",),
    # MS_SEED is special-cased: either "seed" or "noise_seed".
    # MS_SAVE is an output node; metascan only needs its id.
}

_REQUIRED_TITLES: Dict[str, Tuple[str, ...]] = {
    "t2i": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE"),
    "ref": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE", "MS_REF_IMAGE"),
    "ref2v": ("MS_POSITIVE", "MS_SEED", "MS_SAVE"),
}

KINDS: Tuple[str, ...] = tuple(_REQUIRED_TITLES)


class BindingError(ValueError):
    """A workflow does not satisfy the MS_* contract.

    Raised at preset-registration time, never mid-run — a bad setup must
    surface before dozens of jobs are queued against it.
    """


@dataclass(frozen=True)
class Bindings:
    """Resolved node ids (and the seed widget name) for one preset."""

    positive: str
    seed: str
    seed_widget: str
    latent: Optional[str]
    save: str
    negative: Optional[str] = None
    lora: Optional[str] = None
    ref_image: Optional[str] = None
    ref_image_2: Optional[str] = None
    ref_image_3: Optional[str] = None
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    audio: Optional[str] = None
    audio_2: Optional[str] = None
    duration: Optional[str] = None

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

    return Bindings(
        positive=found["MS_POSITIVE"],
        seed=found["MS_SEED"],
        seed_widget=_seed_widget(workflow, found["MS_SEED"]),
        latent=found.get("MS_LATENT"),
        save=found["MS_SAVE"],
        negative=found.get("MS_NEGATIVE"),
        lora=found.get("MS_LORA"),
        ref_image=found.get("MS_REF_IMAGE"),
        ref_image_2=found.get("MS_REF_IMAGE_2"),
        ref_image_3=found.get("MS_REF_IMAGE_3"),
        first_frame=found.get("MS_FIRST_FRAME"),
        last_frame=found.get("MS_LAST_FRAME"),
        audio=found.get("MS_AUDIO"),
        audio_2=found.get("MS_AUDIO_2"),
        duration=found.get("MS_DURATION"),
    )


@dataclass(frozen=True)
class GenerationParams:
    """One generation request's override values.

    ``ref_image`` is a ComfyUI-side filename as returned by
    ``POST /upload/image`` — not a local path.
    """

    positive: str
    seed: int
    width: int
    height: int
    batch_size: int
    negative: Optional[str] = None
    lora_name: Optional[str] = None
    lora_strength: Optional[float] = None
    ref_image: Optional[str] = None
    ref_images: List[str] = field(default_factory=list)
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    audio_refs: List[str] = field(default_factory=list)
    duration_s: Optional[float] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "GenerationParams":
        return cls(**json.loads(raw))


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

    write(bindings.positive, "text", params.positive)
    write(bindings.seed, bindings.seed_widget, params.seed)
    if bindings.latent is not None:
        write(bindings.latent, "width", params.width)
        write(bindings.latent, "height", params.height)
        write(bindings.latent, "batch_size", params.batch_size)

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

    return graph


__all__ = [
    "BindingError",
    "Bindings",
    "GenerationParams",
    "KINDS",
    "apply_overrides",
    "resolve_bindings",
]
