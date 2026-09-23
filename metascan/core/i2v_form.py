"""The editable, per-clip form state of the Image-to-Video dialog. Pure.

An ``i2v_videos`` row carries two sets of values:

* the **as-rendered facts** -- ``prompt_used``, ``seed``, ``duration_s``,
  ``quality``, ``steps``, ``width``/``height``, ``megapixels``, ``loras``.
  Written once at ingest and never changed: they drive the tile's details
  label and are the record of what actually produced the clip (and agree
  with the metadata embedded in the file).
* ``form_state`` -- the editable copy. Seeded from the request at ingest,
  loaded into the dialog when the clip is clicked, and autosaved into as
  the user edits.

Keeping them apart is the point. Letting edits overwrite the facts would
leave a row claiming a seed or prompt that never rendered its video.

Nothing here is persisted before ingest: a cancelled or failed render
leaves no form state behind, by decision.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

FORM_FIELDS = (
    "idea",
    "prompt",
    "duration_s",
    "quality",
    "megapixels",
    "steps",
    "seed",
    "loras",
)

QUALITIES = ("fast", "quality")


class FormStateError(ValueError):
    """A form-state value is unusable. The message names the field."""


def _number(value: Any, field: str) -> float:
    # bool is an int subclass; True must not pass as a seed or a duration.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormStateError(f"{field} must be a number")
    return float(value)


def _positive(value: Any, field: str) -> float:
    number = _number(value, field)
    if number <= 0:
        raise FormStateError(f"{field} must be positive")
    return number


def _whole(value: Any, field: str) -> int:
    number = _number(value, field)
    if number != int(number):
        raise FormStateError(f"{field} must be a whole number")
    return int(number)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise FormStateError(f"{field} must be text")
    return value


def _loras(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise FormStateError("loras must be a list")
    cleaned: List[Dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
            raise FormStateError("loras entries need a text name")
        strength = entry.get("strength", 1.0)
        if isinstance(strength, str):
            try:
                strength = float(strength)
            except ValueError:
                raise FormStateError("loras strength must be a number") from None
        cleaned.append(
            {"name": entry["name"], "strength": _number(strength, "loras strength")}
        )
    return cleaned


def _steps(value: Any) -> Optional[int]:
    if value is None:
        return None  # Fast renders carry no step count
    steps = _whole(value, "steps")
    if steps <= 0:
        raise FormStateError("steps must be positive")
    return steps


def _quality(value: Any) -> str:
    if value not in QUALITIES:
        raise FormStateError(f"quality must be one of {', '.join(QUALITIES)}")
    return str(value)


_VALIDATORS = {
    "idea": lambda v: _text(v, "idea"),
    "prompt": lambda v: _text(v, "prompt"),
    "duration_s": lambda v: _positive(v, "duration_s"),
    "quality": _quality,
    "megapixels": lambda v: _positive(v, "megapixels"),
    "steps": _steps,
    "seed": lambda v: _whole(v, "seed"),
    "loras": _loras,
}


def validate_form_patch(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Clean a partial form-state update, or raise ``FormStateError``.

    Partial on purpose: the dialog may send one changed field or the whole
    form. Unknown keys are an error rather than ignored, so a typo in a
    caller cannot silently save nothing.
    """
    if not body:
        raise FormStateError("No form fields supplied")
    unknown = sorted(set(body) - set(FORM_FIELDS))
    if unknown:
        raise FormStateError(f"Unknown form field(s): {', '.join(unknown)}")
    return {field: _VALIDATORS[field](value) for field, value in body.items()}


def build_form_state(
    *,
    idea: Optional[str],
    prompt: str,
    duration_s: float,
    quality: str,
    megapixels: float,
    steps: Optional[int],
    seed: int,
    loras: Optional[Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """The form exactly as it stood when Generate was clicked."""
    return {
        "idea": idea or "",
        "prompt": prompt,
        "duration_s": float(duration_s),
        "quality": quality,
        "megapixels": float(megapixels),
        "steps": int(steps) if steps is not None else None,
        "seed": int(seed),
        "loras": [dict(entry) for entry in (loras or [])],
    }


def _snap_megapixels(
    width: Any, height: Any, options: Sequence[float]
) -> Optional[float]:
    """The megapixel setting a clip's pixel count most plausibly came from.

    ``i2v_dims`` snaps each edge to a multiple of 16, so the rendered area
    is never exactly the budget (640x1184 is 0.758 MP for a 0.75 setting);
    the nearest configured option recovers the setting.
    """
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    if width <= 0 or height <= 0:
        return None
    area = (width * height) / 1_000_000
    if not options:
        return round(area, 2)
    return float(min(options, key=lambda option: abs(option - area)))


def form_state_for_row(
    row: Mapping[str, Any], megapixel_options: Sequence[float]
) -> Dict[str, Any]:
    """The form state to hand the dialog for one ``i2v_videos`` row.

    A stored ``form_state`` wins field by field. Anything it lacks -- the
    whole thing for a clip ingested before the column existed, or a single
    field added to the form later -- is filled from the as-rendered facts,
    so the dialog always receives one complete shape.
    """
    megapixels = row.get("megapixels")
    if megapixels is None:
        megapixels = _snap_megapixels(
            row.get("width"), row.get("height"), megapixel_options
        )
    rendered_loras = row.get("loras")
    fallback: Dict[str, Any] = {
        "idea": row.get("idea") or "",
        "prompt": row.get("prompt_used") or "",
        "duration_s": row.get("duration_s"),
        "quality": row.get("quality"),
        "megapixels": megapixels,
        "steps": row.get("steps"),
        "seed": row.get("seed"),
        "loras": list(rendered_loras) if isinstance(rendered_loras, list) else [],
    }
    stored = row.get("form_state")
    if isinstance(stored, Mapping):
        fallback.update({k: v for k, v in stored.items() if k in FORM_FIELDS})
    return fallback


__all__ = [
    "FORM_FIELDS",
    "QUALITIES",
    "FormStateError",
    "build_form_state",
    "form_state_for_row",
    "validate_form_patch",
]
