"""i2v découpage templates: the shot structure of an Image-to-Video clip.

A template fixes timing, shot size, angle, camera motion, how each shot
begins (``transition``), who is on screen and who speaks; the VLM fills
only prose. Shots are DERIVED from beats: a ``cut`` or ``j_cut`` beat
opens a new H3 ``[Shot n]``, a ``continuous`` beat stays inside the
current shot with an intra-shot timestamp -- the base guide's "prefer
camera motion when only the distance changes" rule, as data.

Vocabularies are the storyboard's, imported, never redeclared. ``pov`` is
excluded: i2v has no POV mode.

``lint_against_template`` is advisory only and runs on generated AND
hand-edited text. It is text-only -- the fill is not available to it --
so §5.6's "every role's tag appears in the span of every beat that casts
it" cannot be checked from text alone: assembly guarantees it for
generated text, but a hand edit is not checked for it.

Pure module: reads the JSON files and nothing else. See
docs/superpowers/specs/2026-09-22-i2v-decoupage-templates-design.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.h3_compiler import _CAMERA_PHRASES
from metascan.core.i2v_compiler import (
    _COMMON,
    _DESCRIPTION_RE,
    _OPENING,
    ALIGNMENT_LINE,
    I2vError,
    _sentence,
)
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


class I2vTemplateNotFound(I2vTemplateError):
    """The requested template id does not exist. The library itself loaded
    fine -- this is a lookup miss, not a broken file -- so callers that
    want to distinguish "pick a different id" from "the library is
    corrupt" should catch this subclass first."""


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
    if transition == "j_cut" and data.get("speaker") is None:
        raise I2vTemplateError(
            f"{where}.transition: j_cut requires a speaker -- the line is "
            "what carries over the cut"
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
        raise I2vTemplateNotFound(
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


# ---- rendering vocabulary --------------------------------------------

SHOT_SIZE_PHRASES: Dict[str, str] = {
    "ECU": "an extreme close-up",
    "CU": "a close-up",
    "MCU": "a medium close-up",
    "MS": "a medium shot",
    "MLS": "a medium long shot",
    "WS": "a wide shot",
    "EWS": "an extreme wide shot",
}
ANGLE_PHRASES: Dict[str, str] = {
    "eye": "",
    "low": "from a low angle",
    "high": "from a high angle",
    "overhead": "from directly overhead",
    "dutch": "with a dutch tilt",
    "ots": "over the shoulder",
}
LENS_PHRASES: Dict[str, str] = {
    "wide": "on a wide lens",
    "normal": "",
    "tele": "on a telephoto lens",
    "macro": "on a macro lens",
}


def camera_sentence(cam: I2vCamera, *, capital: bool = True) -> str:
    """``The camera pushes in with small amplitude at slow speed.`` --
    the base guide's motion + amplitude + speed order, the same shape
    ``i2v_fixes`` rewrites toward so Apply fixes never fights it."""
    phrase = _CAMERA_PHRASES[cam.camera_motion]
    if cam.camera_motion == "static":
        text = "the camera holds a static shot"
    else:
        text = f"the camera {phrase}"
        if cam.camera_amplitude:
            text += f" with {cam.camera_amplitude} amplitude"
        if cam.camera_speed:
            text += f" at {cam.camera_speed} speed"
    return (text[0].upper() + text[1:] if capital else text) + "."


def framing_phrase(cam: I2vCamera) -> str:
    """``a medium shot from a low angle on a macro lens`` (no cast)."""
    bits = [SHOT_SIZE_PHRASES[cam.shot_size]]
    if cam.angle != "ots" and ANGLE_PHRASES[cam.angle]:
        bits.append(ANGLE_PHRASES[cam.angle])
    if cam.lens and LENS_PHRASES[cam.lens]:
        bits.append(LENS_PHRASES[cam.lens])
    return " ".join(bits)


def _timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


# ---- VLM fill: grammar, prompt, validation ---------------------------


def describe_beat(t: I2vTemplate, index: int) -> str:
    """One-line brief for beat ``index`` -- the VLM's instructions for
    it, and the wording lint messages reuse."""
    b = t.beats[index]
    cam = b.camera
    how = {"continuous": "", "cut": "CUT to ", "j_cut": "J-CUT to "}[b.transition]
    size = SHOT_SIZE_PHRASES[cam.shot_size]
    # "CUT to a close-up" keeps the article; a continuing beat reads
    # "medium shot" bare.
    framing = f"{how}{size}" if how else size.split(" ", 1)[1]
    angle = (
        "over the shoulder"
        if cam.angle == "ots"
        else (ANGLE_PHRASES[cam.angle] or "eye level")
    )
    motion = camera_sentence(cam, capital=False).rstrip(".").removeprefix("the ")
    cast = ", ".join(b.cast) if b.cast else "nobody (a detail shot)"
    who = f"{b.speaker} speaks" if b.speaker else "no line"
    return (
        f"Beat {index + 1} ({b.start_s:.1f}-{b.end_s:.1f} s, {framing}, {angle}, "
        f"{motion}; on screen: {cast}; {who}): {b.note or 'no further direction'}"
    )


def i2v_template_grammar(t: I2vTemplate) -> str:
    """GBNF with the template baked in: one rule per role (id fixed as a
    literal) and per beat; a beat with a ``speaker`` gets a required
    ``line`` string, one without has no ``line`` key at all -- so a
    missing or stray line is structurally impossible."""
    role_rules = [
        f'role{i} ::= "{{" ws "\\"id\\"" ws ":" ws "\\"{r.id}\\"" ws "," ws '
        f'"\\"bound\\"" ws ":" ws boolean ws "," ws '
        f'"\\"description\\"" ws ":" ws string ws "," ws '
        f'"\\"tag\\"" ws ":" ws string ws "}}"'
        for i, r in enumerate(t.roles)
    ]
    beat_rules = []
    for i, b in enumerate(t.beats):
        line = ' ws "," ws "\\"line\\"" ws ":" ws string' if b.speaker else ""
        beat_rules.append(
            f'beat{i} ::= "{{" ws "\\"action\\"" ws ":" ws string{line} ws "}}"'
        )
    roles_seq = ' ws "," ws '.join(f"role{i}" for i in range(len(t.roles)))
    beats_seq = ' ws "," ws '.join(f"beat{i}" for i in range(len(t.beats)))
    root = (
        'root ::= "{" ws "\\"roles\\"" ws ":" ws "[" ws '
        + roles_seq
        + ' ws "]" ws "," ws '
        '"\\"beats\\"" ws ":" ws "[" ws ' + beats_seq + ' ws "]" ws "," ws '
        '"\\"overall_soundscape\\"" ws ":" ws string ws "," ws '
        '"\\"non_diegetic_music\\"" ws ":" ws string ws "}"\n'
    )
    return (
        root
        + "\n".join(role_rules + beat_rules)
        + '\nboolean ::= "true" | "false"\n'
        + _COMMON
    )


def i2v_template_max_tokens(t: I2vTemplate) -> int:
    """Roles cost a description each; beats an action (+ a line)."""
    return 260 + 90 * len(t.beats) + 70 * len(t.roles)


def build_i2v_template_user_prompt(t: I2vTemplate, idea: str) -> str:
    idea_line = idea.strip() or "(none -- infer a natural continuation)"
    roles = "\n".join(f"Role {r.id}: {r.note or 'no note'}" for r in t.roles) or (
        "This template casts no one; the beats are detail shots."
    )
    beats = "\n".join(describe_beat(t, i) for i in range(len(t.beats)))
    hint = f"Soundscape guidance: {t.soundscape_hint}\n" if t.soundscape_hint else ""
    return (
        f"The attached image is the exact first frame of a "
        f"{t.duration_s:.0f}-second video with a fixed shot plan.\n"
        f"User's idea for the video: {idea_line}\n\n"
        f"Roles:\n{roles}\n\n"
        "For each role, set 'bound' to true if that person is visible in the "
        "image and describe THAT person; set it false if nobody in the image "
        "fits and invent a fitting person. 'description' is one sentence of "
        "stable identity (age, build, hair, clothing) used on first mention; "
        "'tag' is a 3-6 word handle used afterwards (\"the woman in the red "
        'coat").\n\n'
        f"Shot plan:\n{beats}\n\n"
        "Write each beat's 'action' as 1-2 present-tense sentences of "
        "concrete visible action matching its brief and framing; do not "
        "restate the camera move. Beat 1 must begin from exactly what the "
        "image depicts. Where a beat lists a speaker, write that person's "
        "spoken words in 'line' (words only, no quotes, no attribution). "
        f"{hint}"
        "Then summarize ambient and action sound in 'overall_soundscape' "
        "and the score in 'non_diegetic_music'."
    )


@dataclass
class I2vRoleFill:
    id: str
    bound: bool
    description: str
    tag: str


@dataclass
class I2vBeatFill:
    action: str
    line: Optional[str]


@dataclass
class I2vTemplateFill:
    roles: Dict[str, I2vRoleFill]
    beats: List[I2vBeatFill]
    overall_soundscape: str
    non_diegetic_music: str


_DEFAULT_SOUNDSCAPE = "Natural ambient sound consistent with the scene."
_DEFAULT_MUSIC = "No non-diegetic music."


def _squash(value: Any) -> str:
    return " ".join(str(value or "").split())


def validate_i2v_template_fill(raw: str, t: I2vTemplate) -> I2vTemplateFill:
    """Strict parse of the VLM's fill against ``t``. The grammar is the
    real enforcement; this catches a model that ignored it."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise I2vError(f"VLM response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise I2vError("VLM response is not a JSON object")

    roles: Dict[str, I2vRoleFill] = {}
    raw_roles = data.get("roles")
    for r in raw_roles if isinstance(raw_roles, list) else []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        desc, tag = _squash(r.get("description")), _squash(r.get("tag"))
        if not desc or not tag:
            raise I2vError(f"VLM gave role {rid or '?'} no description or tag")
        roles[rid] = I2vRoleFill(
            id=rid, bound=bool(r.get("bound")), description=desc, tag=tag
        )
    expected_roles = [r.id for r in t.roles]
    if sorted(roles) != sorted(expected_roles):
        raise I2vError(
            f"VLM returned roles {sorted(roles)}; template needs {expected_roles}"
        )

    raw_beats = data.get("beats")
    beats_in = raw_beats if isinstance(raw_beats, list) else []
    if len(beats_in) != len(t.beats):
        raise I2vError(
            f"VLM returned {len(beats_in)} beats; template has {len(t.beats)} beats"
        )
    beats: List[I2vBeatFill] = []
    for i, (spec, b) in enumerate(zip(t.beats, beats_in)):
        action = _squash(b.get("action")) if isinstance(b, dict) else ""
        if not action:
            raise I2vError(f"VLM gave beat {i + 1} no action")
        line = _squash(b.get("line")) if isinstance(b, dict) else ""
        if spec.speaker and not line:
            raise I2vError(f"VLM gave beat {i + 1} no line, but {spec.speaker} speaks")
        beats.append(I2vBeatFill(action=action, line=line if spec.speaker else None))

    return I2vTemplateFill(
        roles=roles,
        beats=beats,
        overall_soundscape=_squash(data.get("overall_soundscape"))
        or _DEFAULT_SOUNDSCAPE,
        non_diegetic_music=_squash(data.get("non_diegetic_music")) or _DEFAULT_MUSIC,
    )


# ---- assembly ----------------------------------------------------------


def _join_names(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _capital(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


class _Names:
    """First mention -> description, afterwards -> tag."""

    def __init__(self, fill: I2vTemplateFill) -> None:
        self._fill = fill
        self._seen: set[str] = set()

    def __call__(self, role_id: str) -> str:
        r = self._fill.roles[role_id]
        if role_id in self._seen:
            return r.tag
        self._seen.add(role_id)
        return r.description

    def seen(self, role_id: str) -> bool:
        return role_id in self._seen


class _Speakers:
    """(S1), (S2) ... in order of first line."""

    def __init__(self) -> None:
        self._ids: Dict[str, int] = {}

    def __call__(self, role_id: str) -> str:
        if role_id not in self._ids:
            self._ids[role_id] = len(self._ids) + 1
        return f"(S{self._ids[role_id]})"


def _line_sentence(
    name: str, sid: str, line: str, *, off_screen: bool = False, capital: bool = True
) -> str:
    """``The man in the leather jacket (S1) says: <d>[English] ...</d>``.
    ``capital`` is False when the sentence continues an ``At MM:SS.mmm,``
    stamp (the j_cut form); the "Off-screen, " prefix takes the capital
    itself."""
    prefix = "Off-screen, " if off_screen else ""
    shown = name if (off_screen or not capital) else _capital(name)
    return f"{prefix}{shown} {sid} says: <d>[English] {_sentence(line)}</d>"


def _framing(cam: I2vCamera, names: Sequence[str], *, opening: bool) -> str:
    """The sentence that establishes a shot's framing and who is in it."""
    if cam.angle == "ots" and len(names) >= 2:
        size = SHOT_SIZE_PHRASES[cam.shot_size].split(" ", 1)[1]  # drop article
        if opening:
            return (
                f"An over-the-shoulder {size} looks past {names[0]} onto "
                f"{_join_names(names[1:])}."
            )
        return (
            f"the shot cuts to an over-the-shoulder {size} looking past "
            f"{names[0]} onto {_join_names(names[1:])}."
        )
    phrase = framing_phrase(cam)
    if opening:
        who = _join_names(names) or "the scene"
        return f"{_capital(phrase)} frames {who}."
    return f"the shot cuts to {phrase}" + (
        f" of {_join_names(names)}." if names else "."
    )


def assemble_i2v_template_prompt(
    t: I2vTemplate, fill: I2vTemplateFill
) -> Tuple[str, List[str]]:
    """Deterministic H3 document from template + fill. Returns the text
    and advisory notes (the off-screen rule). Code owns 100% of the
    document structure; the VLM's words appear only as actions, lines,
    role descriptions/tags and the two sound fields.

    Name lookups are ordered to match the TEXT's reading order, not the
    beat's data order -- ``_Names`` hands out a role's full description
    on its first call and a short tag on every call after, so whichever
    mention actually reads first in the document is the one that gets
    the description. A j_cut's line reads before its framing clause, so
    the speaker is resolved before the shot's cast names; every other
    beat shape reads framing (or "is now in frame") before its line.
    """
    names = _Names(fill)
    speakers = _Speakers()
    notes: List[str] = []
    parts: List[str] = []
    shot_no = 0

    for i, b in enumerate(t.beats):
        cast = list(b.cast)
        off_screen_speaker = False
        if i == 0:
            for rid in b.cast:
                if not fill.roles[rid].bound:
                    cast.remove(rid)
                    later = next(
                        (
                            j + 1
                            for j in range(1, len(t.beats))
                            if rid in t.beats[j].cast
                        ),
                        None,
                    )
                    if later is None:
                        notes.append(
                            f"role {rid} is not in the picture; the template "
                            "casts it only in the first shot, so it is left out"
                        )
                    else:
                        notes.append(
                            f"role {rid} is not in the picture; the template "
                            f"casts it in the first shot, so it enters at beat "
                            f"{later} instead"
                        )
                    if b.speaker == rid:
                        off_screen_speaker = True

        opens_shot = i == 0 or b.transition != "continuous"
        segment: List[str] = []

        if i == 0:
            shot_no = 1
            opening_rest = _OPENING.removeprefix("[Shot 1] ")
            if t.look:
                head = f"[Shot 1] {_sentence(_capital(t.look))} {opening_rest}"
            else:
                head = _OPENING
            segment.append(head)
            cast_names = [names(rid) for rid in cast]
            segment.append(_framing(b.camera, cast_names, opening=True))
            segment.append(camera_sentence(b.camera))
            segment.append(_sentence(fill.beats[i].action))
            if b.speaker:
                segment.append(
                    _line_sentence(
                        names(b.speaker),
                        speakers(b.speaker),
                        fill.beats[i].line or "",
                        off_screen=off_screen_speaker,
                    )
                )
        elif opens_shot:
            shot_no += 1
            stamp = f"[Shot {shot_no}] At {_timestamp(b.start_s)},"
            if b.transition == "j_cut" and b.speaker:
                # The line reads before the framing clause -- resolve the
                # speaker's name first so its first mention lands here.
                line = _line_sentence(
                    names(b.speaker),
                    speakers(b.speaker),
                    fill.beats[i].line or "",
                    capital=False,
                )
                cast_names = [names(rid) for rid in cast]
                segment.append(
                    f"{stamp} {line}, the words carrying over from the previous "
                    f"shot, as {_framing(b.camera, cast_names, opening=False)}"
                )
                segment.append(camera_sentence(b.camera))
                segment.append(_sentence(fill.beats[i].action))
            else:
                cast_names = [names(rid) for rid in cast]
                segment.append(
                    f"{stamp} {_framing(b.camera, cast_names, opening=False)}"
                )
                segment.append(camera_sentence(b.camera))
                segment.append(_sentence(fill.beats[i].action))
                if b.speaker:
                    segment.append(
                        _line_sentence(
                            names(b.speaker),
                            speakers(b.speaker),
                            fill.beats[i].line or "",
                        )
                    )
        else:
            newly = [rid for rid in cast if not names.seen(rid)]
            segment.append(
                f"At {_timestamp(b.start_s)}, {camera_sentence(b.camera, capital=False)}"
            )
            for rid in newly:
                segment.append(f"{_capital(names(rid))} is now in frame.")
            segment.append(_sentence(fill.beats[i].action))
            if b.speaker:
                segment.append(
                    _line_sentence(
                        names(b.speaker), speakers(b.speaker), fill.beats[i].line or ""
                    )
                )
        parts.append(" ".join(segment))

    description = " ".join(parts)
    text = (
        f"{ALIGNMENT_LINE}\n\n"
        f"integrated_multimodal_description: {description}\n\n"
        f"overall_soundscape: {_sentence(_capital(fill.overall_soundscape))}\n\n"
        f"non_diegetic_music: {_sentence(_capital(fill.non_diegetic_music))}"
    )
    return text, notes


# ---- lint --------------------------------------------------------------
#
# See the module docstring for the §5.6 text-only limitation.

_SHOT_MARK = re.compile(r"\[Shot (\d+)\](?: At (\d\d):(\d\d\.\d{3}),)?")


def _shot_spans(text: str) -> List[Tuple[int, Optional[float], str]]:
    """[(shot_no, cut_time_s or None, span_text), ...] in document order."""
    marks = list(_SHOT_MARK.finditer(text))
    out: List[Tuple[int, Optional[float], str]] = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        stamp = None
        if m.group(2) is not None:
            stamp = int(m.group(2)) * 60 + float(m.group(3))
        out.append((int(m.group(1)), stamp, text[m.end() : end]))
    return out


def _beat_label(t: I2vTemplate, index: int) -> str:
    """Short, well-formed identifier for a beat in a lint message --
    ``describe_beat`` is prose for the VLM prompt, not a label, and
    truncating it at its first colon (which falls mid-sentence, at
    "; on screen:") produced unbalanced-parenthesis garbage."""
    b = t.beats[index]
    return f"Beat {index + 1}, {b.start_s:.1f}-{b.end_s:.1f} s"


def lint_against_template(text: str, t: I2vTemplate) -> List[str]:
    issues: List[str] = []
    # Scope the scan to the integrated_multimodal_description field so
    # ALIGNMENT_LINE's own "(from [Shot 1])" citation in the preamble is
    # excluded structurally, never by guessing at a marker's punctuation
    # (a heuristic like "not followed by ')'" also drops real markers a
    # hand edit wraps in a parenthetical aside). Absent the field, the
    # base lint (_REQUIRED_FIELDS) already reports it -- scan whole text.
    m = _DESCRIPTION_RE.search(text)
    body = m.group(1) if m else text
    spans = _shot_spans(body)
    expected = shots_of(t)

    if len(spans) != len(expected):
        issues.append(
            f"text has {len(spans)} shots; template '{t.id}' has {len(expected)}"
        )
    if [s[0] for s in spans] != list(range(1, len(spans) + 1)):
        issues.append("[Shot n] numbers must be contiguous from 1")
    if spans and spans[0][1] is not None:
        issues.append("Shot 1 must not carry a timestamp (base guide 4.2)")

    last = 0.0
    for shot_no, stamp, _ in spans[1:]:
        if stamp is None:
            issues.append(f"Shot {shot_no} has no 'At MM:SS.mmm,' cut time")
            continue
        if stamp <= last:
            issues.append(
                f"Shot {shot_no} cut time must increase (after {_timestamp(last)})"
            )
        if stamp >= t.duration_s:
            issues.append(
                f"Shot {shot_no} cut time {_timestamp(stamp)} is past the "
                f"{t.duration_s:.0f}s duration"
            )
        last = stamp

    # zip stops at the shorter sequence, so a shot-count mismatch (already
    # reported above) simply limits per-shot checks to the overlap rather
    # than raising -- intentional, not a silent truncation bug.
    for (shot_no, stamp, span), beat_ids in zip(spans, expected):
        first = t.beats[beat_ids[0]]
        if shot_no != 1 and stamp is not None and abs(stamp - first.start_s) > 1e-6:
            issues.append(
                f"Shot {shot_no} cuts at {_timestamp(stamp)}; template has "
                f"{_timestamp(first.start_s)}"
            )
        for bi in beat_ids:
            b = t.beats[bi]
            phrase = (
                "static shot"
                if b.camera.camera_motion == "static"
                else _CAMERA_PHRASES[b.camera.camera_motion]
            )
            if phrase not in span:
                issues.append(
                    f"Shot {shot_no} lacks its camera move '{phrase}' "
                    f"({_beat_label(t, bi)})"
                )
        need = sum(1 for bi in beat_ids if t.beats[bi].speaker)
        have = span.count("<d>")
        if have < need:
            who = ", ".join(
                f"{t.beats[bi].speaker} speaks"
                for bi in beat_ids
                if t.beats[bi].speaker
            )
            issues.append(
                f"Shot {shot_no} has {have} spoken line(s) but the template has "
                f"{need} ({who})"
            )
    return issues


__all__ = [
    "I2V_ANGLE_VALUES",
    "I2V_MOTION_VALUES",
    "I2V_TEMPLATES_DIR",
    "I2vBeatFill",
    "I2vBeatSpec",
    "I2vCamera",
    "I2vRole",
    "I2vRoleFill",
    "I2vTemplate",
    "I2vTemplateError",
    "I2vTemplateFill",
    "I2vTemplateNotFound",
    "MIN_BEAT_S",
    "TRANSITION_VALUES",
    "assemble_i2v_template_prompt",
    "build_i2v_template_user_prompt",
    "camera_sentence",
    "describe_beat",
    "framing_phrase",
    "get_i2v_template",
    "i2v_template_grammar",
    "i2v_template_max_tokens",
    "lint_against_template",
    "load_i2v_templates",
    "parse_i2v_template",
    "reload_i2v_templates",
    "shots_of",
    "summarize_i2v_template",
    "validate_i2v_template_fill",
]
