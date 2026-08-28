"""Shot-list templates (spec refactor-spec-visual-story-quality, Phase E).

A template is scene-scoped and REPLACES the shots + beats compose stages
for that scene: its sections become panels (clip-sized, <= ``shot_cap``)
and its slots become beats. The template owns every structural field --
``duration_s``, ``kind``, ``is_cut``, ``cast`` (as roles), ``dialog_slot``
and the whole camera block -- and the VLM fills only prose (``action``,
``reveals``, ``emotional_intent``, ``sound``, ``dialog[].text``), one
section at a time under a grammar whose slot count and dialog
nullability are baked in.

Pure module: no I/O beyond reading the template JSON files under
``data/templates/`` and the prompt store. The VLM calls live in
``StoryboardRunner.apply_template``. Grammars are built here from the
same enum constants the beats validator uses, so a template whose camera
vocabulary drifts from the code fails LOUDLY at load time instead of
silently nulling out through ``x if x in VALUES else None``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    COMPOSITION_VALUES,
    SCENE_FUNCTION_VALUES,
    _COMMON_RULES,
    _loads_array,
    _clean,
)

SLOT_KIND_VALUES = ("establishing", "action", "reaction", "insert")
SCREEN_SIDE_VALUES = ("LEFT", "RIGHT", "CENTER")

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "data" / "templates"

_PROMPT_KEYS = frozenset({"TEMPLATE_BIND_SYSTEM", "TEMPLATE_FILL_SYSTEM"})


def __getattr__(name: str) -> str:
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class TemplateError(ValueError):
    """A template file is malformed, or a VLM response failed validation."""


class TemplateConformanceError(TemplateError):
    """The instantiated panels do not match the template exactly. Unlike
    the compose lints this is a HARD failure by design (spec §Phase E
    step 4): a conformance miss is a bug, not a style warning."""


# ---- Dataclasses -------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    id: str
    screen_side: str
    note: str


@dataclass(frozen=True)
class Camera:
    shot_size: str
    angle: str
    lens: str
    composition: str
    camera_motion: str
    camera_amplitude: Optional[str] = None
    camera_speed: Optional[str] = None


@dataclass(frozen=True)
class Slot:
    beat_index: int
    duration_s: float
    kind: str
    cast: Tuple[str, ...]
    dialog_slot: Optional[str]
    is_cut: bool
    camera: Camera
    note: Optional[str] = None


@dataclass(frozen=True)
class Section:
    panel_index: int
    duration_s: float
    label: str
    slots: Tuple[Slot, ...]
    note: Optional[str] = None


@dataclass(frozen=True)
class ShotTemplate:
    id: str
    function: str
    roles: Tuple[Role, ...]
    sections: Tuple[Section, ...]
    description: Optional[str] = None
    source: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def slot_count(self) -> int:
        return sum(len(s.slots) for s in self.sections)

    @property
    def duration_s(self) -> float:
        return round(sum(s.duration_s for s in self.sections), 3)

    def role(self, role_id: str) -> Role:
        for r in self.roles:
            if r.id == role_id:
                return r
        raise KeyError(role_id)


# ---- Loading + validation ------------------------------------------------


def _enum(where: str, value: Any, values: Sequence[str], nullable: bool = False) -> Any:
    if value is None and nullable:
        return None
    if value not in values:
        raise TemplateError(f"{where}: {value!r} is not one of {', '.join(values)}")
    return value


def _num(where: str, value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise TemplateError(f"{where}: {value!r} is not a number") from None
    if n <= 0:
        raise TemplateError(f"{where}: duration must be > 0 (got {n})")
    return n


def parse_template(data: Mapping[str, Any], *, where: str = "template") -> ShotTemplate:
    """Validate a template dict into a ``ShotTemplate``. Every enum is
    checked against the real code vocabulary; structural invariants
    (contiguous indices, cast/dialog roles exist, section durations equal
    their slot sums, the first slot of a section is never a cut) raise
    ``TemplateError`` naming the offending path."""
    if not isinstance(data, Mapping):
        raise TemplateError(f"{where}: not a JSON object")
    tid = _clean(data.get("id"))
    if not tid:
        raise TemplateError(f"{where}: missing id")
    where = f"template {tid!r}"
    function = _enum(f"{where}.function", data.get("function"), SCENE_FUNCTION_VALUES)

    roles: List[Role] = []
    for i, r in enumerate(data.get("roles") or []):
        rid = _clean(r.get("id")) if isinstance(r, Mapping) else None
        if not rid:
            raise TemplateError(f"{where}.roles[{i}]: missing id")
        if any(x.id == rid for x in roles):
            raise TemplateError(f"{where}.roles[{i}]: duplicate role id {rid!r}")
        roles.append(
            Role(
                id=rid,
                screen_side=_enum(
                    f"{where}.roles[{i}].screen_side",
                    r.get("screen_side", "CENTER"),
                    SCREEN_SIDE_VALUES,
                ),
                note=_clean(r.get("note")) or "",
            )
        )
    if not roles:
        raise TemplateError(f"{where}: needs at least one role")
    role_ids = {r.id for r in roles}

    sections: List[Section] = []
    next_beat = 0
    for si, sec in enumerate(data.get("sections") or []):
        sw = f"{where}.sections[{si}]"
        if not isinstance(sec, Mapping):
            raise TemplateError(f"{sw}: not an object")
        if int(sec.get("panel_index", -1)) != si:
            raise TemplateError(f"{sw}: panel_index must be {si}")
        slots: List[Slot] = []
        for ki, sl in enumerate(sec.get("slots") or []):
            kw = f"{sw}.slots[{ki}]"
            if not isinstance(sl, Mapping):
                raise TemplateError(f"{kw}: not an object")
            if int(sl.get("beat_index", -1)) != next_beat:
                raise TemplateError(f"{kw}: beat_index must be {next_beat}")
            next_beat += 1
            cast = tuple(str(c) for c in (sl.get("cast") or []))
            for c in cast:
                if c not in role_ids:
                    raise TemplateError(f"{kw}.cast: unknown role {c!r}")
            dialog_slot = _clean(sl.get("dialog_slot"))
            if dialog_slot is not None and dialog_slot not in role_ids:
                raise TemplateError(f"{kw}.dialog_slot: unknown role {dialog_slot!r}")
            is_cut = bool(sl.get("is_cut", False))
            if ki == 0 and is_cut:
                raise TemplateError(f"{kw}: the first slot of a section is never a cut")
            cam = sl.get("camera")
            if not isinstance(cam, Mapping):
                raise TemplateError(f"{kw}: missing camera block")
            cw = f"{kw}.camera"
            motion = _enum(
                f"{cw}.camera_motion", cam.get("camera_motion"), CAMERA_MOTION_VALUES
            )
            amplitude = _enum(
                f"{cw}.camera_amplitude",
                cam.get("camera_amplitude"),
                CAMERA_AMPLITUDE_VALUES,
                nullable=True,
            )
            speed = _enum(
                f"{cw}.camera_speed",
                cam.get("camera_speed"),
                CAMERA_SPEED_VALUES,
                nullable=True,
            )
            if motion == "static" and (amplitude or speed):
                raise TemplateError(f"{cw}: static motion takes no amplitude/speed")
            camera = Camera(
                shot_size=_enum(
                    f"{cw}.shot_size", cam.get("shot_size"), SHOT_SIZE_VALUES
                ),
                angle=_enum(f"{cw}.angle", cam.get("angle"), ANGLE_VALUES),
                lens=_enum(f"{cw}.lens", cam.get("lens"), LENS_VALUES),
                composition=_enum(
                    f"{cw}.composition", cam.get("composition"), COMPOSITION_VALUES
                ),
                camera_motion=motion,
                camera_amplitude=amplitude,
                camera_speed=speed,
            )
            slots.append(
                Slot(
                    beat_index=int(sl["beat_index"]),
                    duration_s=_num(f"{kw}.duration_s", sl.get("duration_s")),
                    kind=_enum(f"{kw}.kind", sl.get("kind"), SLOT_KIND_VALUES),
                    cast=cast,
                    dialog_slot=dialog_slot,
                    is_cut=is_cut,
                    camera=camera,
                    note=_clean(sl.get("note")),
                )
            )
        if not slots:
            raise TemplateError(f"{sw}: needs at least one slot")
        duration = _num(f"{sw}.duration_s", sec.get("duration_s"))
        slot_sum = round(sum(s.duration_s for s in slots), 3)
        if abs(slot_sum - duration) > 0.05:
            raise TemplateError(
                f"{sw}: duration_s {duration} != sum of slot durations {slot_sum}"
            )
        sections.append(
            Section(
                panel_index=si,
                duration_s=duration,
                label=_clean(sec.get("label")) or f"Section {si + 1}",
                slots=tuple(slots),
                note=_clean(sec.get("note")),
            )
        )
    if not sections:
        raise TemplateError(f"{where}: needs at least one section")

    known = {"id", "function", "roles", "sections", "description", "source"}
    return ShotTemplate(
        id=tid,
        function=function,
        roles=tuple(roles),
        sections=tuple(sections),
        description=_clean(data.get("description")),
        source=_clean(data.get("source")),
        extra={k: v for k, v in data.items() if k not in known},
    )


_cache: Dict[str, Dict[str, ShotTemplate]] = {}


def load_templates(directory: Optional[Path] = None) -> Dict[str, ShotTemplate]:
    """Load + validate every ``*.json`` under ``directory`` (default
    ``data/templates/``). Cached per directory; a malformed file raises
    ``TemplateError`` naming the file -- a bad template must not load
    silently. ``reload_templates`` drops the cache."""
    d = Path(directory) if directory is not None else TEMPLATES_DIR
    key = str(d.resolve())
    if key in _cache:
        return _cache[key]
    out: Dict[str, ShotTemplate] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise TemplateError(f"{path.name}: invalid JSON: {exc}") from exc
            try:
                t = parse_template(data, where=path.name)
            except TemplateError as exc:
                raise TemplateError(f"{path.name}: {exc}") from exc
            if t.id in out:
                raise TemplateError(f"{path.name}: duplicate template id {t.id!r}")
            out[t.id] = t
    _cache[key] = out
    return out


def reload_templates() -> None:
    _cache.clear()


def get_template(template_id: str, directory: Optional[Path] = None) -> ShotTemplate:
    templates = load_templates(directory)
    if template_id not in templates:
        raise TemplateError(
            f"unknown template {template_id!r}; available: "
            f"{', '.join(sorted(templates)) or '(none)'}"
        )
    return templates[template_id]


def list_templates(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    return [summarize(t) for t in load_templates(directory).values()]


def summarize(t: ShotTemplate) -> Dict[str, Any]:
    return {
        "id": t.id,
        "function": t.function,
        "description": t.description,
        "roles": [
            {"id": r.id, "screen_side": r.screen_side, "note": r.note} for r in t.roles
        ],
        "sections": [
            {
                "panel_index": s.panel_index,
                "duration_s": s.duration_s,
                "label": s.label,
                "slot_count": len(s.slots),
            }
            for s in t.sections
        ],
        "slot_count": t.slot_count,
        "duration_s": t.duration_s,
    }


# ---- Step 1: role binding ----------------------------------------------------


def _json_str(v: str) -> str:
    """A GBNF literal for a JSON-encoded string, with the surrounding
    quotes escaped for the grammar."""
    inner = json.dumps(v)[1:-1].replace("\\", "\\\\").replace('"', '\\"')
    return f'"\\"{inner}\\""'


def role_bind_grammar(template: ShotTemplate, names: Sequence[str]) -> str:
    """``{"A": <name>, "B": <name>}`` where ``<name>`` is restricted to the
    scene's castable character names -- the model cannot invent one."""
    if not names:
        raise TemplateError("role binding needs at least one character name")
    fields = ' ws "," ws '.join(
        f'"\\"{r.id}\\"" ws ":" ws name' for r in template.roles
    )
    name_alts = " | ".join(_json_str(n) for n in names)
    return (
        f'root ::= "{{" ws {fields} ws "}}"\n'
        f"name ::= {name_alts}\n" + _COMMON_RULES.replace("{{", "{").replace("}}", "}")
    )


def _roster_lines(subjects: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"- {s['name']}: {s.get('description') or ''}" for s in subjects)


def build_role_bind_user_prompt(
    template: ShotTemplate,
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
) -> str:
    roles = "\n".join(
        f"- {r.id} ({r.screen_side.lower()} of frame): {r.note}" for r in template.roles
    )
    return (
        f"Story logline: {outline.get('logline') or ''}\n"
        f"Scene: {scene['name']} — {scene.get('setting') or scene.get('location') or ''}\n"
        f"Scene notes: {scene.get('notes') or 'none'}\n"
        f"Scene function: {template.function}\n\n"
        f"Template roles:\n{roles}\n\n"
        f"Characters in this story (use these exact names):\n"
        f"{_roster_lines(subjects)}\n\n"
        "Assign one distinct character to every role. Write the JSON."
    )


def validate_role_bind_response(
    raw: str, template: ShotTemplate, roster: Mapping[str, int]
) -> Dict[str, int]:
    """``{role_id: subject_id}``; every role bound, to a known character,
    no character in two roles."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise TemplateError(f"role binding is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError("role binding is not a JSON object")
    out: Dict[str, int] = {}
    for r in template.roles:
        name = _clean(data.get(r.id))
        if not name or name.lower() not in roster:
            raise TemplateError(f"role {r.id!r} bound to unknown character {name!r}")
        sid = roster[name.lower()]
        if sid in out.values():
            raise TemplateError(f"character {name!r} bound to more than one role")
        out[r.id] = sid
    return out


# ---- Step 2: instantiate -------------------------------------------------------


def instantiate(
    template: ShotTemplate, role_map: Mapping[str, int]
) -> List[Dict[str, Any]]:
    """Expand sections into panel records and slots into beat records with
    every structural field filled from the template and every prose field
    empty. ``subject_ids`` are resolved here -- the compiler's
    ``_panel_subjects`` and ``refplan`` read them per beat."""
    missing = [r.id for r in template.roles if r.id not in role_map]
    if missing:
        raise TemplateError(f"unbound template roles: {', '.join(missing)}")
    panels: List[Dict[str, Any]] = []
    for sec in template.sections:
        beats: List[Dict[str, Any]] = []
        for i, sl in enumerate(sec.slots):
            cam = sl.camera
            beats.append(
                {
                    "sort_order": i,
                    "duration_s": sl.duration_s,
                    "kind": sl.kind,
                    "is_cut": 1 if sl.is_cut else 0,
                    "subject_ids": [role_map[c] for c in sl.cast],
                    "shot_size": cam.shot_size,
                    "angle": cam.angle,
                    "lens": cam.lens,
                    "composition": cam.composition,
                    "camera_motion": cam.camera_motion,
                    "camera_amplitude": cam.camera_amplitude,
                    "camera_speed": cam.camera_speed,
                    "movement_motivation": None,
                    "light_quality": None,
                    "action": "",
                    "reveals": None,
                    "emotional_intent": None,
                    "sound": None,
                    "dialog": [],
                }
            )
        panels.append(
            {
                "sort_order": sec.panel_index,
                "action": "",
                "duration_s": sec.duration_s,
                "subtext": sec.note,
                "is_turn": 0,
                "beats": beats,
            }
        )
    return panels


# ---- Step 3: fill -------------------------------------------------------------


def fill_grammar(section: Section) -> str:
    """One rule per slot: the array has exactly ``len(slots)`` elements and
    a slot with a ``dialog_slot`` gets ``string`` (never ``null``) for its
    dialog, so an unfilled line is structurally impossible."""
    slot_rules = []
    for i, sl in enumerate(section.slots):
        dialog = "string" if sl.dialog_slot else '"null"'
        slot_rules.append(
            f'slot{i} ::= "{{" ws "\\"action\\"" ws ":" ws string ws "," ws '
            f'"\\"reveals\\"" ws ":" ws string ws "," ws '
            f'"\\"emotional_intent\\"" ws ":" ws string ws "," ws '
            f'"\\"sound\\"" ws ":" ws nullable ws "," ws '
            f'"\\"dialog\\"" ws ":" ws {dialog} ws "}}"'
        )
    seq = ' ws "," ws '.join(f"slot{i}" for i in range(len(section.slots)))
    return (
        f'root ::= "[" ws {seq} ws "]"\n'
        + "\n".join(slot_rules)
        + "\n"
        + _COMMON_RULES.replace("{{", "{").replace("}}", "}")
    )


def _camera_phrase(cam: Camera) -> str:
    bits = [
        cam.shot_size,
        cam.angle,
        f"{cam.lens} lens",
        cam.composition.replace("_", " "),
    ]
    motion = cam.camera_motion.replace("_", " ")
    if cam.camera_amplitude or cam.camera_speed:
        motion += (
            f" ({' '.join(x for x in (cam.camera_speed, cam.camera_amplitude) if x)})"
        )
    bits.append(motion)
    return ", ".join(bits)


def describe_slot(sl: Slot, role_names: Mapping[str, str], *, index: int) -> str:
    cast = ", ".join(role_names[c] for c in sl.cast) or "nobody (detail insert)"
    speaker = role_names[sl.dialog_slot] if sl.dialog_slot else "nobody speaks"
    cut = "cut" if sl.is_cut else "continuous"
    line = (
        f"Slot {index + 1} — {sl.kind}, {sl.duration_s:.1f}s, {cut}; "
        f"on screen: {cast}; speaks: {speaker}; camera: {_camera_phrase(sl.camera)}"
    )
    if sl.note:
        line += f"; direction: {sl.note}"
    return line


def build_fill_user_prompt(
    template: ShotTemplate,
    section: Section,
    role_names: Mapping[str, str],
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    outline: Mapping[str, Any],
    previous: Sequence[Tuple[Section, Sequence[Mapping[str, Any]]]] = (),
) -> str:
    roles = "\n".join(
        f"- {r.id} = {role_names[r.id]} ({r.screen_side.lower()} of frame): {r.note}"
        for r in template.roles
    )
    prev_lines: List[str] = []
    for psec, fills in previous:
        prev_lines.append(f"[{psec.label}]")
        for sl, f in zip(psec.slots, fills):
            who = role_names[sl.dialog_slot] if sl.dialog_slot else None
            said = f' — {who}: "{f.get("dialog")}"' if who and f.get("dialog") else ""
            prev_lines.append(f"  {sl.kind}: {f.get('action')}{said}")
    prev_block = (
        "What has already happened in this scene:\n" + "\n".join(prev_lines) + "\n\n"
        if prev_lines
        else "This is the first section of the scene.\n\n"
    )
    later = [s.label for s in template.sections if s.panel_index > section.panel_index]
    later_line = (
        f"Sections still to come after this one: {', '.join(later)}\n"
        if later
        else "This is the final section of the scene.\n"
    )
    slots = "\n".join(
        describe_slot(sl, role_names, index=i) for i, sl in enumerate(section.slots)
    )
    return (
        f"Story logline: {outline.get('logline') or ''}\n"
        f"Tone: {outline.get('tone') or 'unspecified'}\n"
        f"Scene: {scene['name']} — setting: "
        f"{scene.get('setting') or scene.get('location') or 'unspecified'}\n"
        f"Time: {scene.get('time_of_day') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"mood: {scene.get('mood') or 'unspecified'}\n"
        f"Scene notes: {scene.get('notes') or 'none'}\n"
        f"Scene function: {template.function}\n\n"
        f"Cast:\n{roles}\n"
        f"Character descriptions:\n{_roster_lines(subjects)}\n\n"
        f"{prev_block}"
        f'Section to write now: "{section.label}" '
        f"({section.duration_s:.1f}s, {len(section.slots)} slots)"
        f"{' — ' + section.note if section.note else ''}\n"
        f"{later_line}\n"
        f"Slots (fixed; write exactly one JSON object per slot, in order):\n{slots}\n\n"
        "Write the JSON array."
    )


def validate_fill_response(raw: str, section: Section) -> List[Dict[str, Any]]:
    data = _loads_array(raw)
    if not isinstance(data, list):
        raise TemplateError("fill response is not a JSON array")
    if len(data) != len(section.slots):
        raise TemplateError(
            f"fill response has {len(data)} slots; section "
            f"{section.label!r} has {len(section.slots)}"
        )
    out: List[Dict[str, Any]] = []
    for i, (sl, item) in enumerate(zip(section.slots, data)):
        if not isinstance(item, Mapping):
            raise TemplateError(f"slot {i + 1}: not an object")
        action = _clean(item.get("action"))
        if not action:
            raise TemplateError(f"slot {i + 1}: empty action")
        dialog = _clean(item.get("dialog"))
        if sl.dialog_slot and not dialog:
            raise TemplateError(
                f"slot {i + 1}: dialog_slot {sl.dialog_slot!r} requires a spoken line"
            )
        if not sl.dialog_slot:
            dialog = None
        out.append(
            {
                "action": action,
                "reveals": _clean(item.get("reveals")),
                "emotional_intent": _clean(item.get("emotional_intent")),
                "sound": _clean(item.get("sound")),
                "dialog": dialog,
            }
        )
    return out


def apply_fill(
    panel: Dict[str, Any],
    section: Section,
    fill: Sequence[Mapping[str, Any]],
    role_map: Mapping[str, int],
    voices: Mapping[int, Optional[str]] = {},
) -> None:
    """Write a validated fill into an instantiated panel record (in
    place). The panel's ``action`` becomes the section's slot actions
    joined, so the shot header reads sensibly."""
    beats = panel["beats"]
    for sl, beat, f in zip(section.slots, beats, fill):
        beat["action"] = f["action"]
        beat["reveals"] = f.get("reveals")
        beat["emotional_intent"] = f.get("emotional_intent")
        beat["sound"] = f.get("sound")
        if sl.dialog_slot:
            sid = role_map[sl.dialog_slot]
            beat["dialog"] = [
                {
                    "subject_id": sid,
                    "voice": voices.get(sid),
                    "delivery": None,
                    "language": "English",
                    "text": f["dialog"],
                }
            ]
        else:
            beat["dialog"] = []
    panel["action"] = " ".join(b["action"] for b in beats if b.get("action"))


# ---- Step 4: conform -------------------------------------------------------


def conform(
    template: ShotTemplate,
    panels: Sequence[Mapping[str, Any]],
    role_map: Mapping[str, int],
) -> None:
    """Assert the panel records match the template exactly: section and
    slot counts, durations, every camera field, ``kind``, ``is_cut``,
    cast, and that every ``dialog_slot`` is filled by the bound character
    and every other slot is silent. Raises ``TemplateConformanceError``
    listing every miss."""
    misses: List[str] = []
    if len(panels) != len(template.sections):
        misses.append(f"{len(panels)} panels for {len(template.sections)} sections")
    for sec, panel in zip(template.sections, panels):
        pw = f"section {sec.panel_index + 1} ({sec.label})"
        if abs(float(panel.get("duration_s") or 0) - sec.duration_s) > 0.05:
            misses.append(
                f"{pw}: duration_s {panel.get('duration_s')} != {sec.duration_s}"
            )
        beats = list(panel.get("beats") or [])
        if len(beats) != len(sec.slots):
            misses.append(f"{pw}: {len(beats)} beats for {len(sec.slots)} slots")
        for sl, beat in zip(sec.slots, beats):
            bw = f"{pw} slot {sl.beat_index + 1}"
            cam = sl.camera
            expected: Dict[str, Any] = {
                "duration_s": sl.duration_s,
                "kind": sl.kind,
                "is_cut": 1 if sl.is_cut else 0,
                "shot_size": cam.shot_size,
                "angle": cam.angle,
                "lens": cam.lens,
                "composition": cam.composition,
                "camera_motion": cam.camera_motion,
                "camera_amplitude": cam.camera_amplitude,
                "camera_speed": cam.camera_speed,
            }
            for k, v in expected.items():
                got = beat.get(k)
                if k == "duration_s":
                    ok = abs(float(got or 0) - float(sl.duration_s)) <= 0.05
                elif k == "is_cut":
                    ok = int(got or 0) == v
                else:
                    ok = got == v
                if not ok:
                    misses.append(f"{bw}: {k} {got!r} != {v!r}")
            want_cast = [role_map[c] for c in sl.cast]
            if list(beat.get("subject_ids") or []) != want_cast:
                misses.append(
                    f"{bw}: subject_ids {beat.get('subject_ids')} != {want_cast}"
                )
            if not (beat.get("action") or "").strip():
                misses.append(f"{bw}: empty action")
            dialog = list(beat.get("dialog") or [])
            if sl.dialog_slot:
                sid = role_map[sl.dialog_slot]
                if (
                    len(dialog) != 1
                    or dialog[0].get("subject_id") != sid
                    or not (dialog[0].get("text") or "").strip()
                ):
                    misses.append(
                        f"{bw}: dialog_slot {sl.dialog_slot!r} not filled by "
                        f"subject {sid}"
                    )
            elif dialog:
                misses.append(f"{bw}: silent slot carries dialog")
    if misses:
        raise TemplateConformanceError(
            f"template {template.id!r} conformance failed: " + "; ".join(misses)
        )
