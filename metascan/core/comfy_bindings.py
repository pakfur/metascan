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
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

# Titles metascan looks for. Values are the widget keys each node must
# expose in its ``inputs`` block for the binding to be usable.
_REQUIRED_WIDGETS: Dict[str, Tuple[str, ...]] = {
    "MS_POSITIVE": ("text",),
    "MS_NEGATIVE": ("text",),
    "MS_LATENT": ("width", "height", "batch_size"),
    "MS_LORA": ("lora_name", "strength_model", "strength_clip"),
    "MS_REF_IMAGE": ("image",),
    # MS_SEED is special-cased: either "seed" or "noise_seed".
    # MS_SAVE is an output node; metascan only needs its id.
}

_REQUIRED_TITLES: Dict[str, Tuple[str, ...]] = {
    "t2i": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE"),
    "ref": ("MS_POSITIVE", "MS_SEED", "MS_LATENT", "MS_SAVE", "MS_REF_IMAGE"),
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
    latent: str
    save: str
    negative: Optional[str] = None
    lora: Optional[str] = None
    ref_image: Optional[str] = None

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
        latent=found["MS_LATENT"],
        save=found["MS_SAVE"],
        negative=found.get("MS_NEGATIVE"),
        lora=found.get("MS_LORA"),
        ref_image=found.get("MS_REF_IMAGE"),
    )


__all__ = ["BindingError", "Bindings", "KINDS", "resolve_bindings"]
