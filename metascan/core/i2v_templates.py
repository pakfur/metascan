"""i2v découpage templates: the shot structure of an Image-to-Video clip.

A template fixes timing, shot size, angle, camera motion, how each shot
begins (``transition``), who is on screen and who speaks; the VLM fills
only prose. Shots are DERIVED from beats: a ``cut`` or ``j_cut`` beat
opens a new H3 ``[Shot n]``, a ``continuous`` beat stays inside the
current shot with an intra-shot timestamp -- the base guide's "prefer
camera motion when only the distance changes" rule, as data.

Vocabularies are the storyboard's, imported, never redeclared. ``pov`` is
excluded: i2v has no POV mode.

Pure module: reads the JSON files and nothing else. See
docs/superpowers/specs/2026-09-22-i2v-decoupage-templates-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.storyboard_parse import (
    ANGLE_VALUES,
    LENS_VALUES,
    SHOT_SIZE_VALUES,
)
from metascan.core.storyboard_story import (
    CAMERA_AMPLITUDE_VALUES,
    CAMERA_MOTION_VALUES,
    CAMERA_SPEED_VALUES,
)

I2V_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "data" / "i2v_templates"

TRANSITION_VALUES: Tuple[str, ...] = ("continuous", "cut", "j_cut")
I2V_ANGLE_VALUES: Tuple[str, ...] = tuple(v for v in ANGLE_VALUES if v != "pov")
I2V_MOTION_VALUES: Tuple[str, ...] = tuple(
    v for v in CAMERA_MOTION_VALUES if v != "pov"
)
MIN_BEAT_S = 0.5
_ROLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class I2vTemplateError(ValueError):
    """A template file is unusable. The message names the file and field."""


@dataclass(frozen=True)
class I2vRole:
    id: str
    note: str = ""


@dataclass(frozen=True)
class I2vCamera:
    shot_size: str
    angle: str
    camera_motion: str
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None
    lens: Optional[str] = None


@dataclass(frozen=True)
class I2vBeatSpec:
    start_s: float
    end_s: float
    transition: str
    cast: Tuple[str, ...]
    speaker: Optional[str]
    camera: I2vCamera
    note: str = ""


@dataclass(frozen=True)
class I2vTemplate:
    id: str
    name: str
    description: str
    duration_s: float
    look: str
    soundscape_hint: str
    roles: Tuple[I2vRole, ...]
    beats: Tuple[I2vBeatSpec, ...]


# ---- parse ----------------------------------------------------------


def _enum(
    where: str, value: Any, values: Sequence[str], *, nullable: bool = False
) -> Any:
    if value is None and nullable:
        return None
    if value not in values:
        raise I2vTemplateError(f"{where}: {value!r} is not one of {', '.join(values)}")
    return value


def _text(where: str, value: Any, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise I2vTemplateError(f"{where}: must be a non-empty string")
    return value


def _num(where: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise I2vTemplateError(f"{where}: {value!r} is not a number")
    return float(value)


def _parse_camera(where: str, data: Any) -> I2vCamera:
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: camera must be an object")
    return I2vCamera(
        shot_size=_enum(f"{where}.shot_size", data.get("shot_size"), SHOT_SIZE_VALUES),
        angle=_enum(f"{where}.angle", data.get("angle"), I2V_ANGLE_VALUES),
        camera_motion=_enum(
            f"{where}.camera_motion", data.get("camera_motion"), I2V_MOTION_VALUES
        ),
        camera_amplitude=_enum(
            f"{where}.camera_amplitude",
            data.get("camera_amplitude"),
            CAMERA_AMPLITUDE_VALUES,
            nullable=True,
        ),
        camera_speed=_enum(
            f"{where}.camera_speed",
            data.get("camera_speed"),
            CAMERA_SPEED_VALUES,
            nullable=True,
        ),
        lens=_enum(f"{where}.lens", data.get("lens"), LENS_VALUES, nullable=True),
    )


def _parse_beat(
    where: str, data: Any, role_ids: Sequence[str], index: int
) -> I2vBeatSpec:
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: must be an object")
    transition = _enum(f"{where}.transition", data.get("transition"), TRANSITION_VALUES)
    if index == 0 and transition != "continuous":
        raise I2vTemplateError(
            f"{where}.transition: the first beat must be 'continuous' -- "
            "it is anchored to the source picture, there is nothing to cut from"
        )
    raw_cast = data.get("cast", [])
    if not isinstance(raw_cast, list) or any(
        not isinstance(r, str) or r not in role_ids for r in raw_cast
    ):
        raise I2vTemplateError(
            f"{where}.cast: every entry must be a declared role id "
            f"({', '.join(role_ids) or 'none declared'})"
        )
    speaker = data.get("speaker")
    if speaker is not None and speaker not in raw_cast:
        raise I2vTemplateError(
            f"{where}.speaker: {speaker!r} must be one of the beat's cast"
        )
    start = _num(f"{where}.start_s", data.get("start_s"))
    end = _num(f"{where}.end_s", data.get("end_s"))
    if end - start < MIN_BEAT_S:
        raise I2vTemplateError(
            f"{where}.end_s: a beat must be at least {MIN_BEAT_S} s long"
        )
    return I2vBeatSpec(
        start_s=start,
        end_s=end,
        transition=transition,
        cast=tuple(raw_cast),
        speaker=speaker,
        camera=_parse_camera(f"{where}.camera", data.get("camera")),
        note=_text(f"{where}.note", data.get("note"), required=False),
    )


def parse_i2v_template(
    data: Mapping[str, Any], *, where: str = "template"
) -> I2vTemplate:
    """Validate one template document. Every failure names ``where`` (the
    file) and the offending field, so a bad file never loads silently."""
    if not isinstance(data, Mapping):
        raise I2vTemplateError(f"{where}: must be a JSON object")
    duration = _num(f"{where}: duration_s", data.get("duration_s"))
    if duration <= 0:
        raise I2vTemplateError(f"{where}: duration_s must be > 0")

    roles: List[I2vRole] = []
    raw_roles = data.get("roles", [])
    if not isinstance(raw_roles, list):
        raise I2vTemplateError(f"{where}: roles must be a list")
    for i, r in enumerate(raw_roles):
        if not isinstance(r, Mapping) or not isinstance(r.get("id"), str):
            raise I2vTemplateError(f"{where}: roles[{i}] needs a string id")
        rid = r["id"]
        if not _ROLE_ID.match(rid):
            raise I2vTemplateError(
                f"{where}: roles[{i}] role id {rid!r} must be a bare identifier"
            )
        if any(existing.id == rid for existing in roles):
            raise I2vTemplateError(f"{where}: duplicate role id {rid!r}")
        roles.append(
            I2vRole(
                id=rid,
                note=_text(f"{where}: roles[{i}].note", r.get("note"), required=False),
            )
        )
    role_ids = [r.id for r in roles]

    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats:
        raise I2vTemplateError(f"{where}: beats must be a non-empty list")
    beats: List[I2vBeatSpec] = []
    for i, b in enumerate(raw_beats):
        beats.append(_parse_beat(f"{where}: beats[{i}]", b, role_ids, i))
    if beats[0].start_s != 0:
        raise I2vTemplateError(f"{where}: beats[0].start_s must be 0")
    for i in range(1, len(beats)):
        if abs(beats[i].start_s - beats[i - 1].end_s) > 1e-6:
            raise I2vTemplateError(
                f"{where}: beats[{i}].start_s must equal beats[{i - 1}].end_s "
                f"({beats[i - 1].end_s}); beats must tile the clip with no gaps"
            )
    if abs(beats[-1].end_s - duration) > 1e-6:
        raise I2vTemplateError(
            f"{where}: beats[{len(beats) - 1}].end_s must equal duration_s "
            f"({duration})"
        )

    return I2vTemplate(
        id=_text(f"{where}: id", data.get("id"), required=True),
        name=_text(f"{where}: name", data.get("name"), required=True),
        description=_text(
            f"{where}: description", data.get("description"), required=False
        ),
        duration_s=duration,
        look=_text(f"{where}: look", data.get("look"), required=False),
        soundscape_hint=_text(
            f"{where}: soundscape_hint", data.get("soundscape_hint"), required=False
        ),
        roles=tuple(roles),
        beats=tuple(beats),
    )


# ---- load -----------------------------------------------------------

_cache: Dict[str, Dict[str, I2vTemplate]] = {}


def load_i2v_templates(directory: Optional[Path] = None) -> Dict[str, I2vTemplate]:
    """Load + validate every ``*.json`` under ``directory`` (default
    ``data/i2v_templates/``), cached per directory until
    ``reload_i2v_templates``. A malformed file raises, naming it."""
    d = Path(directory) if directory is not None else I2V_TEMPLATES_DIR
    key = str(d.resolve())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    out: Dict[str, I2vTemplate] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise I2vTemplateError(f"{path.name}: invalid JSON: {exc}") from exc
            t = parse_i2v_template(data, where=path.name)
            if t.id != path.stem:
                raise I2vTemplateError(
                    f"{path.name}: id {t.id!r} must equal the file stem {path.stem!r}"
                )
            out[t.id] = t
    _cache[key] = out
    return out


def reload_i2v_templates() -> None:
    _cache.clear()


def get_i2v_template(template_id: str, directory: Optional[Path] = None) -> I2vTemplate:
    templates = load_i2v_templates(directory)
    if template_id not in templates:
        raise I2vTemplateError(
            f"unknown i2v template {template_id!r}; available: "
            f"{', '.join(sorted(templates)) or '(none)'}"
        )
    return templates[template_id]


# ---- structure ------------------------------------------------------


def shots_of(t: I2vTemplate) -> List[List[int]]:
    """Beat indices grouped into H3 shots. ``cut``/``j_cut`` open a shot."""
    shots: List[List[int]] = []
    for i, b in enumerate(t.beats):
        if i == 0 or b.transition != "continuous":
            shots.append([i])
        else:
            shots[-1].append(i)
    return shots


def summarize_i2v_template(t: I2vTemplate) -> Dict[str, Any]:
    """JSON-safe dict of the whole template, for the API and the dialog's
    cadence strip."""
    d = asdict(t)
    d["roles"] = [asdict(r) for r in t.roles]
    d["beats"] = [
        {**asdict(b), "cast": list(b.cast), "camera": asdict(b.camera)} for b in t.beats
    ]
    return d


__all__ = [
    "I2V_ANGLE_VALUES",
    "I2V_MOTION_VALUES",
    "I2V_TEMPLATES_DIR",
    "I2vBeatSpec",
    "I2vCamera",
    "I2vRole",
    "I2vTemplate",
    "I2vTemplateError",
    "MIN_BEAT_S",
    "TRANSITION_VALUES",
    "get_i2v_template",
    "load_i2v_templates",
    "parse_i2v_template",
    "reload_i2v_templates",
    "shots_of",
    "summarize_i2v_template",
]
