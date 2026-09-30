"""The pure helpers behind the Text-to-Image dialog: seed stepping, output
size, and the editable per-image form state. No I/O, no i2v imports.

A ``t2i_images`` row carries two sets of values:

* the **as-rendered facts** -- ``caption``, ``prompt_used``,
  ``negative_used``, ``model``, ``seed``, ``megapixels``,
  ``aspect_ratio``, ``loras`` ... Written once at ingest and never
  changed: they are the record of what produced the image and agree with
  the metadata embedded in the file.
* ``form_state`` -- the editable copy. Seeded from the request at ingest,
  loaded into the dialog when a tile is selected, and autosaved into as
  the user edits.

Keeping them apart is the point: letting edits overwrite the facts would
leave a row claiming a seed or prompt that never rendered its picture.

Seed policy, Batch Size and Count per Batch are *not* form state. They
are actions, not properties of an image, so selecting a tile can never
trigger a large re-run.
"""

from __future__ import annotations

import copy
import math
import random
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

SEED_MAX: int = 2**31 - 1
SEED_POLICIES: Tuple[str, ...] = ("fixed", "increment", "decrement", "random")
ASPECT_RATIOS: Tuple[str, ...] = (
    "1:1",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "16:9",
    "9:16",
    "4:5",
    "5:4",
    "21:9",
    "9:21",
)
T2I_MODES: Tuple[str, ...] = ("manual", "random")
T2I_FORM_FIELDS: Tuple[str, ...] = (
    "mode",
    "filter",
    "caption",
    "model",
    "preset_id",
    "megapixels",
    "aspect_ratio",
    "seed",
    "prompt",
    "negative",
    "loras",
)

# t2i_dims limits. No model renders a 1000 MP image or a 33:1 strip; past
# these the input is a typo, and a clean 400 beats a silly size (or an
# OverflowError on the way to one).
MAX_MEGAPIXELS: float = 1000.0
_MAX_ASPECT_SKEW: int = 32
_RATIO_RX = re.compile(r"([0-9]{1,6}):([0-9]{1,6})")


class T2iFormError(ValueError):
    """A form value is unusable. The message names the field."""


def _is_int(value: Any) -> bool:
    # bool is an int subclass; True must not pass as a seed or a preset id.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---- seeds --------------------------------------------------------------


def _seed(value: Any, field: str = "seed") -> int:
    if not _is_int(value) or not 0 <= value <= SEED_MAX:
        raise T2iFormError(f"{field} must be a whole number from 0 to {SEED_MAX}")
    return int(value)


def next_seed(policy: str, seed: int, rng: random.Random) -> Optional[int]:
    """The seed after ``seed`` under ``policy``.

    ``fixed`` repeats it; ``increment`` / ``decrement`` step by one and
    return None rather than leave ``0..SEED_MAX`` (the caller stops the run
    instead of repeating or wrapping a seed); ``random`` draws from the
    whole range through ``rng`` and ignores the current seed.
    """
    if policy not in SEED_POLICIES:
        raise T2iFormError(
            f"unknown seed policy {policy!r}; use one of {', '.join(SEED_POLICIES)}"
        )
    current = _seed(seed)
    if policy == "fixed":
        return current
    if policy == "random":
        return rng.randint(0, SEED_MAX)
    stepped = current + 1 if policy == "increment" else current - 1
    return stepped if 0 <= stepped <= SEED_MAX else None


# ---- output size --------------------------------------------------------

# How much a ratio error costs relative to a budget error in t2i_dims, both
# taken as absolute log ratios. i2v_dims weighs them ten to one because its
# output must line up with a source frame. Here the ratio only steers the
# composition, and on SDXL's 64-grid ten to one lets the area drift by up to
# 47.5% of the budget (0.5 MP at 4:5) to buy an exact ratio. At four to one the
# whole grid the dialog offers (11 ratios x 0.5-2 MP x 16/64) stays within
# 11.5% of the budget and 4.2% of the ratio.
_ASPECT_WEIGHT: float = 4.0
# Candidate edges tried either side of the ideal one, on each axis.
_SEARCH_RADIUS: int = 3


def _ratio_parts(value: Any, label: str) -> Tuple[int, int]:
    match = _RATIO_RX.fullmatch(value) if isinstance(value, str) else None
    if match is None or int(match.group(1)) == 0 or int(match.group(2)) == 0:
        raise T2iFormError(
            f"{label} must look like W:H with whole numbers above zero, got {value!r}"
        )
    wide, high = int(match.group(1)), int(match.group(2))
    if max(wide, high) > _MAX_ASPECT_SKEW * min(wide, high):
        raise T2iFormError(
            f"{label} {value!r} is more extreme than {_MAX_ASPECT_SKEW}:1"
        )
    return wide, high


def _megapixels(value: Any, label: str = "megapixels") -> float:
    if _is_number(value):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if 0 < number <= MAX_MEGAPIXELS:  # False for nan and inf
            return number
    raise T2iFormError(
        f"{label} must be a number above 0 and at most {MAX_MEGAPIXELS:g}"
    )


def t2i_dims(aspect_ratio: str, megapixels: float, multiple: int) -> Tuple[int, int]:
    """Output ``(width, height)`` for a ratio and a megapixel budget.

    Both edges land on the ``multiple`` grid (16 for Krea2 / Qwen / Z-Image,
    64 for SDXL) and neither is ever less than one multiple, so an extreme
    ratio on a coarse grid still yields a usable short edge. Rounding each
    edge on its own compounds the ratio error at that coarseness, so a
    window of candidate edges around the ideal one is scored on both errors
    -- the ratio's counting ``_ASPECT_WEIGHT`` times what the budget's does
    -- and the best pair wins. Ties go to the smaller width, then height, so
    the answer is deterministic.

    Candidates are generated from both axes: each nearby width with its best
    partner heights, and each nearby height with its best partner widths.
    The second family is what keeps the search honest when the floor binds
    (the budget's short edge is under one multiple): there the best width is
    far from the ideal one, but it is the best partner of the floor height.
    """
    wide, high = _ratio_parts(aspect_ratio, "aspect ratio")
    budget = _megapixels(megapixels) * 1_000_000
    if not _is_int(multiple) or multiple < 1:
        raise T2iFormError(
            f"grid multiple must be a whole number of at least 1, got {multiple!r}"
        )

    aspect = wide / high
    ideal_wide = math.sqrt(budget * aspect)
    ideal_high = math.sqrt(budget / aspect)

    def cost(width: int, height: int) -> float:
        return _ASPECT_WEIGHT * abs(math.log(width / height / aspect)) + abs(
            math.log(width * height / budget)
        )

    candidates: Set[Tuple[int, int]] = set()
    centre = round(ideal_wide / multiple)
    for cells in range(centre - _SEARCH_RADIUS, centre + _SEARCH_RADIUS + 1):
        width = max(1, cells) * multiple
        exact = width / aspect / multiple
        for partner in (math.floor(exact), math.ceil(exact)):
            candidates.add((width, max(1, partner) * multiple))
    centre = round(ideal_high / multiple)
    for cells in range(centre - _SEARCH_RADIUS, centre + _SEARCH_RADIUS + 1):
        height = max(1, cells) * multiple
        exact = height * aspect / multiple
        for partner in (math.floor(exact), math.ceil(exact)):
            candidates.add((max(1, partner) * multiple, height))
    return min(sorted(candidates), key=lambda pair: (cost(*pair), pair))


# ---- form state ---------------------------------------------------------


def _lora_strength(value: Any, index: int) -> float:
    number: Optional[float] = None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            number = None
    elif _is_number(value):
        try:
            number = float(value)
        except OverflowError:
            number = None
    if number is None or not math.isfinite(number):
        raise T2iFormError(f"loras[{index}] strength must be a finite number")
    return number


def normalize_loras(loras: Any) -> List[Dict[str, Any]]:
    """``[{"name": str, "strength": float}]`` from user input, or raise.

    A LoRA needs a non-blank text name (kept verbatim: it is a path the
    picker offered). A missing strength means full strength; an explicit
    null does not -- a cleared field should say so rather than silently
    become 1.0. Negative strengths are legal. Anything else on an entry is
    dropped, and the input is never mutated.
    """
    if not isinstance(loras, (list, tuple)):
        raise T2iFormError("loras must be a list of {name, strength} objects")
    cleaned: List[Dict[str, Any]] = []
    for index, entry in enumerate(loras):
        if not isinstance(entry, Mapping):
            raise T2iFormError(
                f"loras[{index}] must be an object with a text name and a strength"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise T2iFormError(f"loras[{index}] needs a non-empty text name")
        cleaned.append(
            {
                "name": name,
                "strength": _lora_strength(entry.get("strength", 1.0), index),
            }
        )
    return cleaned


def _mode(value: Any) -> str:
    if value not in T2I_MODES:
        raise T2iFormError(f"mode must be one of {', '.join(T2I_MODES)}")
    return str(value)


def _filter(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise T2iFormError("filter must be an object or null")
    return copy.deepcopy(dict(value))


def _optional_text(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise T2iFormError(f"{field} must be text or null")
    return value


def _model(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise T2iFormError("model must be a non-empty string")
    return value


def _preset_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if not _is_int(value):
        raise T2iFormError("preset_id must be a whole number or null")
    return int(value)


def _optional_megapixels(value: Any) -> Optional[float]:
    return None if value is None else _megapixels(value)


def _aspect_ratio(value: Any) -> Optional[str]:
    if value is None:
        return None
    _ratio_parts(value, "aspect_ratio")
    return str(value)


def _optional_seed(value: Any) -> Optional[int]:
    return None if value is None else _seed(value)


_FIELD_RULES: Dict[str, Callable[[Any], Any]] = {
    "mode": _mode,
    "filter": _filter,
    "caption": lambda value: _optional_text(value, "caption"),
    "model": _model,
    "preset_id": _preset_id,
    "megapixels": _optional_megapixels,
    "aspect_ratio": _aspect_ratio,
    "seed": _optional_seed,
    "prompt": lambda value: _optional_text(value, "prompt"),
    "negative": lambda value: _optional_text(value, "negative"),
    "loras": normalize_loras,
}


def validate_form_patch(patch: Mapping[str, Any]) -> Dict[str, Any]:
    """Clean a partial form-state update, or raise ``T2iFormError``.

    Partial on purpose: the dialog may send one changed field or the whole
    form. Unknown keys are an error rather than ignored, so a typo in a
    caller cannot silently save nothing -- and so is an empty patch, and a
    session-only setting such as ``seed_policy``. Returns only the fields
    supplied, as fresh objects.
    """
    if not isinstance(patch, Mapping):
        raise T2iFormError("form patch must be a mapping of field names to values")
    if not patch:
        raise T2iFormError("No form fields supplied")
    unknown = sorted(str(key) for key in patch if key not in T2I_FORM_FIELDS)
    if unknown:
        raise T2iFormError(f"Unknown form field(s): {', '.join(unknown)}")
    return {name: _FIELD_RULES[name](value) for name, value in patch.items()}


def _blank_form() -> Dict[str, Any]:
    return {
        "mode": "manual",
        "filter": None,
        "caption": None,
        "model": None,
        "preset_id": None,
        "megapixels": None,
        "aspect_ratio": None,
        "seed": None,
        "prompt": None,
        "negative": None,
        "loras": [],
    }


def build_form_state(**fields: Any) -> Dict[str, Any]:
    """The form exactly as it stood when Generate was clicked.

    Takes any of ``T2I_FORM_FIELDS`` as keywords, checks them by the same
    rules ``validate_form_patch`` applies (so ingest can never store what a
    later PATCH would refuse), and returns the COMPLETE shape: fields not
    given are ``mode="manual"``, ``loras=[]`` and null for the rest.
    ``loras=None`` counts as not given.
    """
    if fields.get("loras", []) is None:
        del fields["loras"]
    cleaned = validate_form_patch(fields) if fields else {}
    state = _blank_form()
    state.update(cleaned)
    return state


def form_state_for_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The form state to hand the dialog for one ``t2i_images`` row.

    A stored ``form_state`` wins field by field -- including a stored null,
    which is the user clearing a box. Anything it lacks (the whole thing for
    an image ingested after a server restart, or a single field added to the
    form later) is filled from the as-rendered facts, so the dialog always
    receives one complete shape. Never mutates ``row``.
    """
    rendered = row.get("loras")
    facts_loras = list(rendered) if isinstance(rendered, list) else []
    state: Dict[str, Any] = {
        "mode": "manual",
        "filter": None,
        "caption": row.get("caption"),
        "model": row.get("model"),
        "preset_id": row.get("preset_id"),
        "megapixels": row.get("megapixels"),
        "aspect_ratio": row.get("aspect_ratio"),
        "seed": row.get("seed"),
        "prompt": row.get("prompt_used"),
        "negative": row.get("negative_used"),
        "loras": facts_loras,
    }
    stored = row.get("form_state")
    if isinstance(stored, Mapping):
        state.update({k: v for k, v in stored.items() if k in T2I_FORM_FIELDS})
    # The dialog binds these two to non-null controls.
    if not isinstance(state["loras"], list):
        state["loras"] = facts_loras
    if state["mode"] not in T2I_MODES:
        state["mode"] = "manual"
    return copy.deepcopy(state)


# ---- render time --------------------------------------------------------


def render_seconds(
    started_at: Optional[str], finished_at: Optional[str]
) -> Optional[float]:
    """Wall-clock seconds a job spent rendering, from the job row's ISO
    stamps. ``job_outputs`` fires before the job is marked done, so a
    missing ``finished_at`` means "now". ``started_at`` is stamped when
    metascan hands the prompt to ComfyUI, so time spent behind another
    in-flight prompt inside ComfyUI is included. Naive stamps are read as
    UTC. None when the start is absent/unparseable or the span is
    negative."""
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


__all__ = [
    "ASPECT_RATIOS",
    "MAX_MEGAPIXELS",
    "SEED_MAX",
    "SEED_POLICIES",
    "T2I_FORM_FIELDS",
    "T2I_MODES",
    "T2iFormError",
    "build_form_state",
    "form_state_for_row",
    "next_seed",
    "normalize_loras",
    "render_seconds",
    "t2i_dims",
    "validate_form_patch",
]
