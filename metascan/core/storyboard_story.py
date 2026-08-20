"""Story-engine stage plumbing: grammars, validators, prompt builders.

Pure module (no I/O beyond the prompt store): the VLM calls live in
storyboard_runner.compose_story. Mirrors storyboard_parse.py. System
prompts are YAML-backed (hot reload); grammars are built here from the
enum constants — deviation from spec §4.6, recorded there.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.storyboard_parse import (
    ANGLE_VALUES,
    LENS_VALUES,
    SHOT_SIZE_VALUES,
)

STAGES = ("outline", "scenes", "shots", "beats")

CAMERA_MOTION_VALUES = (
    "zoom_in",
    "zoom_out",
    "push_in",
    "pull_out",
    "pan_left",
    "pan_right",
    "truck_left",
    "truck_right",
    "tilt_up",
    "tilt_down",
    "pedestal_up",
    "pedestal_down",
    "arc",
    "tracking",
    "static",
    "shake_slight",
    "shake_strong",
    "pov",
    "roll_cw",
    "roll_ccw",
)
CAMERA_AMPLITUDE_VALUES = ("small", "large")
CAMERA_SPEED_VALUES = ("slow", "fast")
ARC_BEAT_VALUES = ("setup", "rising", "turn", "climax", "resolution")
PACING_VALUES = ("contemplative", "standard", "propulsive")
COMPOSITION_VALUES = (
    "thirds_left",
    "thirds_right",
    "centered",
    "symmetrical",
    "negative_space",
    "frame_in_frame",
    "leading_lines",
    "deep_staging",
)
LIGHT_QUALITY_VALUES = (
    "hard",
    "soft",
    "dappled",
    "practical",
    "window",
    "firelight",
    "ambient",
)

_PROMPT_KEYS = frozenset(
    {
        "STORY_OUTLINE_SYSTEM",
        "STORY_SCENES_SYSTEM",
        "STORY_SHOTS_SYSTEM",
        "STORY_BEATS_SYSTEM",
    }
)


def __getattr__(name: str) -> str:
    if name in _PROMPT_KEYS:
        from metascan.core.prompt_store import get_prompt_store

        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class StoryError(ValueError):
    """A stage response could not be validated."""


# ASL-derived guidance (spec §4). Beats are the film-shot unit (one beat ==
# one H3 [Shot n]); panels are generation containers capped by shot_cap.
_PACING_TABLE: Dict[str, Dict[str, float]] = {
    "contemplative": {
        "panel_min_s": 10.0,
        "panel_max_s": 15.0,
        "shots_min": 1,
        "shots_max": 3,
        "beats_min": 1,
        "beats_max": 3,
        "beat_asl_s": 7.0,
    },
    "standard": {
        "panel_min_s": 8.0,
        "panel_max_s": 15.0,
        "shots_min": 2,
        "shots_max": 4,
        "beats_min": 2,
        "beats_max": 4,
        "beat_asl_s": 4.5,
    },
    "propulsive": {
        "panel_min_s": 6.0,
        "panel_max_s": 12.0,
        "shots_min": 3,
        "shots_max": 6,
        "beats_min": 3,
        "beats_max": 5,
        "beat_asl_s": 2.5,
    },
}


def pacing_guidance(pacing: str, shot_cap: float) -> Dict[str, float]:
    """Per-call prompt numbers for the shots/beats stages. Unknown pacing
    falls back to standard; panel bounds clamp to the video target's cap."""
    row = dict(_PACING_TABLE.get(pacing, _PACING_TABLE["standard"]))
    row["panel_max_s"] = min(row["panel_max_s"], float(shot_cap))
    row["panel_min_s"] = min(row["panel_min_s"], row["panel_max_s"])
    return row


# -- Grammar building blocks (str.format templates; literal JSON braces
# are doubled). Hyphens never appear as "\-" (CLAUDE.md). ------------------

_COMMON_RULES = r"""nullable ::= string | "null"
number ::= [0-9]+ ("." [0-9]+)?
boolean ::= "true" | "false"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]{{0,20}}
"""


def _alts(values: Tuple[str, ...], with_null: bool = True) -> str:
    quoted = " | ".join('"\\"{0}\\""'.format(v) for v in values)
    return '{0} | "null"'.format(quoted) if with_null else quoted


_OUTLINE_TEMPLATE = (
    r"""root ::= "{{" ws "\"logline\"" ws ":" ws string ws "," ws "\"tone\"" ws ":" ws string ws "," ws "\"pacing\"" ws ":" ws pacingv ws "," ws "\"duration_target_s\"" ws ":" ws number ws "," ws "\"subjects\"" ws ":" ws subjects ws "," ws "\"arc\"" ws ":" ws arc ws "}}"
subjects ::= "[" ws subject (ws "," ws subject){{0,5}} ws "]"
subject ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"description\"" ws ":" ws string ws "," ws "\"voice\"" ws ":" ws nullable ws "}}"
arc ::= "[" ws arcitem (ws "," ws arcitem){{1,6}} ws "]"
arcitem ::= "{{" ws "\"beat\"" ws ":" ws arcbeat ws "," ws "\"summary\"" ws ":" ws string ws "}}"
arcbeat ::= {arcbeat_alts}
pacingv ::= {pacing_alts}
"""
    + _COMMON_RULES
)

OUTLINE_GRAMMAR = _OUTLINE_TEMPLATE.format(
    arcbeat_alts=_alts(ARC_BEAT_VALUES, with_null=False),
    pacing_alts=_alts(PACING_VALUES, with_null=False),
)

_SCENES_TEMPLATE = (
    r"""root ::= "[" ws scene (ws "," ws scene){{1,7}} ws "]"
scene ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"subtitle\"" ws ":" ws nullable ws "," ws "\"setting\"" ws ":" ws nullable ws "," ws "\"location\"" ws ":" ws nullable ws "," ws "\"time_of_day\"" ws ":" ws nullable ws "," ws "\"mood\"" ws ":" ws nullable ws "," ws "\"lighting\"" ws ":" ws nullable ws "," ws "\"notes\"" ws ":" ws nullable ws "," ws "\"arc_beats\"" ws ":" ws arcbeats ws "," ws "\"charge_in\"" ws ":" ws charge ws "," ws "\"charge_out\"" ws ":" ws charge ws "}}"
arcbeats ::= "[" ws (arcbeat (ws "," ws arcbeat){{0,4}})? ws "]"
arcbeat ::= {arcbeat_alts}
charge ::= "-"? [0-5]
"""
    + _COMMON_RULES
)

SCENES_GRAMMAR = _SCENES_TEMPLATE.format(
    arcbeat_alts=_alts(ARC_BEAT_VALUES, with_null=False)
)

SHOTS_GRAMMAR = (
    r"""root ::= "[" ws shot (ws "," ws shot){{0,5}} ws "]"
shot ::= "{{" ws "\"action\"" ws ":" ws string ws "," ws "\"duration_s\"" ws ":" ws number ws "," ws "\"subtext\"" ws ":" ws string ws "," ws "\"is_turn\"" ws ":" ws boolean ws "}}"
"""
    + _COMMON_RULES
).format()

_BEATS_TEMPLATE = (
    r"""root ::= "[" ws beat (ws "," ws beat){{1,5}} ws "]"
beat ::= "{{" ws "\"duration_s\"" ws ":" ws number ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"reveals\"" ws ":" ws string ws "," ws "\"emotional_intent\"" ws ":" ws string ws "," ws "\"shot_size\"" ws ":" ws shotsize ws "," ws "\"angle\"" ws ":" ws angle ws "," ws "\"lens\"" ws ":" ws lens ws "," ws "\"composition\"" ws ":" ws composition ws "," ws "\"light_quality\"" ws ":" ws lightq ws "," ws "\"subjects\"" ws ":" ws namelist ws "," ws "\"camera_motion\"" ws ":" ws motion ws "," ws "\"camera_amplitude\"" ws ":" ws amplitude ws "," ws "\"camera_speed\"" ws ":" ws speed ws "," ws "\"movement_motivation\"" ws ":" ws nullable ws "," ws "\"is_cut\"" ws ":" ws boolean ws "," ws "\"sound\"" ws ":" ws nullable ws "," ws "\"dialog\"" ws ":" ws dialog ws "}}"
shotsize ::= {shotsize_alts}
angle ::= {angle_alts}
lens ::= {lens_alts}
composition ::= {composition_alts}
lightq ::= {lightq_alts}
namelist ::= "[" ws (string (ws "," ws string){{0,5}})? ws "]"
motion ::= {motion_alts}
amplitude ::= {amplitude_alts}
speed ::= {speed_alts}
dialog ::= "[" ws (line (ws "," ws line){{0,3}})? ws "]"
line ::= "{{" ws "\"subject\"" ws ":" ws nullable ws "," ws "\"voice\"" ws ":" ws nullable ws "," ws "\"delivery\"" ws ":" ws nullable ws "," ws "\"language\"" ws ":" ws string ws "," ws "\"text\"" ws ":" ws string ws "}}"
"""
    + _COMMON_RULES
)

BEATS_GRAMMAR = _BEATS_TEMPLATE.format(
    shotsize_alts=_alts(SHOT_SIZE_VALUES),
    angle_alts=_alts(ANGLE_VALUES),
    lens_alts=_alts(LENS_VALUES),
    composition_alts=_alts(COMPOSITION_VALUES),
    lightq_alts=_alts(LIGHT_QUALITY_VALUES),
    motion_alts=_alts(CAMERA_MOTION_VALUES),
    amplitude_alts=_alts(CAMERA_AMPLITUDE_VALUES),
    speed_alts=_alts(CAMERA_SPEED_VALUES),
)


# -- User-prompt builders --------------------------------------------------


def _roster_lines(subjects: Sequence[Mapping[str, Any]]) -> str:
    return (
        "\n".join(f"- {s['name']}: {s['description']}" for s in subjects)
        or "(none yet)"
    )


def build_outline_user_prompt(
    premise: str, subjects: Sequence[Mapping[str, Any]]
) -> str:
    return (
        f"Premise:\n{premise}\n\nExisting subjects (reuse names verbatim; "
        f"do not re-describe them):\n{_roster_lines(subjects)}\n\n"
        "Write the story outline JSON."
    )


def build_scenes_user_prompt(outline_json: str) -> str:
    return f"Story outline:\n{outline_json}\n\nWrite the scene list JSON."


def build_shots_user_prompt(
    outline_json: str,
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    prev_scene_name: Optional[str],
    next_scene_name: Optional[str],
    max_shot_s: float = 15.0,
) -> str:
    return (
        f"Story outline:\n{outline_json}\n\n"
        f"Scene to break into shots: {scene['name']}"
        f" — {scene.get('setting') or scene.get('location') or ''}\n"
        f"Mood: {scene.get('mood') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"time: {scene.get('time_of_day') or 'unspecified'}\n"
        f"Previous scene: {prev_scene_name or '(story opening)'}\n"
        f"Next scene: {next_scene_name or '(story ending)'}\n\n"
        f"Subjects (use these exact names):\n{_roster_lines(subjects)}\n\n"
        "Write the shot list JSON.\n"
        f"Each shot must be at most {max_shot_s:.0f} seconds long."
    )


def build_beats_user_prompt(
    logline: str,
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> str:
    return (
        f"Story logline: {logline}\n"
        f"Scene: {scene['name']} — mood {scene.get('mood') or 'unspecified'}\n"
        f"Shot: {panel['action']}\n"
        f"Target duration: {panel.get('duration_s') or 12.0} seconds\n"
        f"Subject roster (assign per beat; exact names):\n{_roster_lines(subjects)}\n\n"
        "Write the beat list JSON."
    )


# -- Validators ------------------------------------------------------------


def _loads_array(raw: str) -> Any:
    """Parse a JSON array, salvaging complete leading elements when the
    response was truncated at the token limit mid-element.

    Grammar-constrained output is structurally valid until the exact
    point generation stopped, so trimming back to the last parseable
    element boundary and closing the array recovers everything the model
    finished. Falls through to ``_loads`` (and its truncation-hint
    error) when nothing salvageable remains.
    """
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    if isinstance(raw, str):
        end = len(raw)
        while True:
            i = raw.rfind("}", 0, end)
            if i < 0:
                break
            end = i
            try:
                data = json.loads(raw[: i + 1] + "]")
            except ValueError:
                # A '}' inside a string, or a nested object whose parent
                # element is itself unclosed — keep walking backwards.
                continue
            if isinstance(data, list) and data:
                return data
    return _loads(raw)


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as e:
        # A grammar-constrained response can only be malformed when
        # llama-server cut generation off at max_tokens — long output that
        # doesn't end on a JSON closer is the signature of that truncation.
        hint = ""
        if (
            isinstance(raw, str)
            and len(raw) > 200
            and not raw.rstrip().endswith(("}", "]"))
        ):
            hint = " (the response appears truncated at the token limit)"
        raise StoryError(f"response is not valid JSON: {e}{hint}") from e


def _clean(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_outline_response(raw: str) -> Dict[str, Any]:
    data = _loads(raw)
    if not isinstance(data, dict):
        raise StoryError("outline response is not a JSON object")
    logline = _clean(data.get("logline"))
    tone = _clean(data.get("tone"))
    if not logline or not tone:
        raise StoryError("outline is missing logline/tone")
    try:
        duration = float(data.get("duration_target_s") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        duration = 90.0
    subjects = []
    for s in data.get("subjects") or []:
        if not isinstance(s, dict):
            continue
        name = _clean(s.get("name"))
        desc = _clean(s.get("description"))
        if name and desc:
            subjects.append(
                {"name": name, "description": desc, "voice": _clean(s.get("voice"))}
            )
    arc = [
        {"beat": a["beat"], "summary": _clean(a.get("summary")) or ""}
        for a in (data.get("arc") or [])
        if isinstance(a, dict) and a.get("beat") in ARC_BEAT_VALUES
    ]
    if not arc:
        raise StoryError("outline has no arc entries")
    pacing = data.get("pacing")
    if pacing not in PACING_VALUES:
        pacing = "standard"
    return {
        "logline": logline,
        "tone": tone,
        "pacing": pacing,
        "duration_target_s": duration,
        "subjects": subjects,
        "arc": arc,
    }


def _charge(v: Any) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if -5 <= n <= 5 else None


def validate_scenes_response(raw: str) -> List[Dict[str, Any]]:
    data = _loads_array(raw)
    if not isinstance(data, list):
        raise StoryError("scenes response is not a JSON array")
    scenes = []
    for sc in data:
        if not isinstance(sc, dict):
            continue
        name = _clean(sc.get("name"))
        if not name:
            continue
        scenes.append(
            {
                "name": name,
                "subtitle": _clean(sc.get("subtitle")),
                "setting": _clean(sc.get("setting")),
                "location": _clean(sc.get("location")),
                "time_of_day": _clean(sc.get("time_of_day")),
                "mood": _clean(sc.get("mood")),
                "lighting": _clean(sc.get("lighting")),
                "notes": _clean(sc.get("notes")),
                "arc_beats": [
                    b for b in (sc.get("arc_beats") or []) if b in ARC_BEAT_VALUES
                ],
                "charge_in": _charge(sc.get("charge_in")),
                "charge_out": _charge(sc.get("charge_out")),
            }
        )
    if not scenes:
        raise StoryError("no scenes in the response")
    return scenes


def validate_shots_response(raw: str) -> List[Dict[str, Any]]:
    data = _loads_array(raw)
    if not isinstance(data, list):
        raise StoryError("shots response is not a JSON array")
    panels: List[Dict[str, Any]] = []
    for p in data:
        if not isinstance(p, dict):
            continue
        action = _clean(p.get("action"))
        if not action:
            continue
        try:
            duration = float(p.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        panels.append(
            {
                "action": action,
                "duration_s": duration if duration > 0 else 12.0,
                "subtext": _clean(p.get("subtext")),
                "is_turn": 1 if p.get("is_turn") else 0,
            }
        )
    if not panels:
        raise StoryError("no shots in the response")
    return panels


def validate_beats_response(
    raw: str, roster: Mapping[str, int]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    data = _loads_array(raw)
    if not isinstance(data, list):
        raise StoryError("beats response is not a JSON array")
    beats: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for b in data:
        if not isinstance(b, dict):
            continue
        action = _clean(b.get("action"))
        if not action:
            continue
        try:
            duration = float(b.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        ids: List[int] = []
        for n in b.get("subjects") or []:
            if not isinstance(n, str):
                continue
            key = n.strip().lower()
            if key in roster:
                ids.append(roster[key])
            elif key:
                warnings.append(f"unknown subject {n!r} dropped")
        dialog = []
        for line in b.get("dialog") or []:
            if not isinstance(line, dict):
                continue
            text = _clean(line.get("text"))
            if not text:
                continue
            name = _clean(line.get("subject"))
            dialog.append(
                {
                    "subject_id": roster.get(name.strip().lower()) if name else None,
                    "voice": _clean(line.get("voice")),
                    "delivery": _clean(line.get("delivery")),
                    "language": _clean(line.get("language")) or "English",
                    "text": text,
                }
            )
        beat = {
            "duration_s": duration if duration > 0 else 4.0,
            "action": action,
            "shot_size": (
                b.get("shot_size") if b.get("shot_size") in SHOT_SIZE_VALUES else None
            ),
            "angle": b.get("angle") if b.get("angle") in ANGLE_VALUES else None,
            "lens": b.get("lens") if b.get("lens") in LENS_VALUES else None,
            "subject_ids": ids,
            "camera_motion": (
                b.get("camera_motion")
                if b.get("camera_motion") in CAMERA_MOTION_VALUES
                else None
            ),
            "camera_amplitude": (
                b.get("camera_amplitude")
                if b.get("camera_amplitude") in CAMERA_AMPLITUDE_VALUES
                else None
            ),
            "camera_speed": (
                b.get("camera_speed")
                if b.get("camera_speed") in CAMERA_SPEED_VALUES
                else None
            ),
            "is_cut": 1 if b.get("is_cut") else 0,
            "sound": _clean(b.get("sound")),
            "dialog": dialog,
            "composition": (
                b.get("composition")
                if b.get("composition") in COMPOSITION_VALUES
                else None
            ),
            "light_quality": (
                b.get("light_quality")
                if b.get("light_quality") in LIGHT_QUALITY_VALUES
                else None
            ),
            "emotional_intent": _clean(b.get("emotional_intent")),
            "reveals": _clean(b.get("reveals")),
            "movement_motivation": _clean(b.get("movement_motivation")),
        }
        if beat["camera_motion"] in (None, "static"):
            beat["camera_amplitude"] = None
            beat["camera_speed"] = None
            beat["movement_motivation"] = None
        beats.append(beat)
    if not beats:
        raise StoryError("no beats in the response")
    return beats, warnings


def rescale_beat_durations(
    beats: List[Dict[str, Any]], target_s: float, tolerance: float = 0.25
) -> List[Dict[str, Any]]:
    """Proportionally rescale beat durations to ``target_s`` when their sum
    is outside ``target_s * (1 ± tolerance)``. Arithmetic is always
    code-side (spec §4.4) — never re-prompt the model for math."""
    total = sum(float(b.get("duration_s") or 0) for b in beats)
    if total <= 0 or target_s <= 0:
        return beats
    if abs(total - target_s) <= target_s * tolerance:
        return beats
    factor = target_s / total
    for b in beats:
        b["duration_s"] = round(float(b["duration_s"]) * factor, 1)
    return beats
