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
    guidance: Mapping[str, float],
    is_turn_scene: bool,
) -> str:
    arc = ", ".join(scene.get("arc_beats") or []) or "unspecified"
    turn_directive = (
        "This scene contains the story's TURN: exactly one shot must set "
        "is_turn to true — the shot where the scene's emotional value "
        "reverses. Every other shot sets is_turn false.\n"
        if is_turn_scene
        else "This scene does not contain the story's turn: every shot "
        "sets is_turn false.\n"
    )
    return (
        f"Story outline:\n{outline_json}\n\n"
        f"Scene to break into shots: {scene['name']}"
        f" — {scene.get('setting') or scene.get('location') or ''}\n"
        f"Mood: {scene.get('mood') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"time: {scene.get('time_of_day') or 'unspecified'}\n"
        f"Arc stages this scene covers: {arc}\n"
        f"Emotional charge (protagonist's POV, -5..+5): opens at "
        f"{scene.get('charge_in')}, closes at {scene.get('charge_out')}\n"
        f"Previous scene: {prev_scene_name or '(story opening)'}\n"
        f"Next scene: {next_scene_name or '(story ending)'}\n\n"
        f"Subjects (use these exact names):\n{_roster_lines(subjects)}\n\n"
        f"{turn_directive}"
        "Write the shot list JSON.\n"
        f"Produce between {int(guidance['shots_min'])} and "
        f"{int(guidance['shots_max'])} shots, each lasting between "
        f"{guidance['panel_min_s']:.0f} and {guidance['panel_max_s']:.0f} "
        "seconds."
    )


def build_beats_user_prompt(
    outline: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    guidance: Mapping[str, float],
    prev_panel_action: Optional[str],
    prev_beat_summary: Optional[str],
) -> str:
    duration = float(panel.get("duration_s") or 12.0)
    turn_line = (
        "This shot is the story's TURN: reserve its tightest framing for "
        "the beat where the turn lands.\n"
        if panel.get("is_turn")
        else ""
    )
    if prev_panel_action:
        prev_block = f"Previous shot in this scene: {prev_panel_action}\n"
        if prev_beat_summary:
            prev_block += (
                f"The previous shot's last beat ended on: {prev_beat_summary}\n"
            )
    else:
        prev_block = (
            "This is the scene's opening shot: the first beat must "
            "establish the space wide (WS or EWS).\n"
        )
    return (
        f"Story logline: {outline.get('logline') or ''}\n"
        f"Tone: {outline.get('tone') or 'unspecified'}\n"
        f"Scene: {scene['name']} — setting: "
        f"{scene.get('setting') or scene.get('location') or 'unspecified'}\n"
        f"Time: {scene.get('time_of_day') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"mood: {scene.get('mood') or 'unspecified'}\n"
        f"Shot: {panel['action']}\n"
        f"Shot subtext: {panel.get('subtext') or 'unspecified'}\n"
        f"{turn_line}"
        f"{prev_block}"
        f"Target duration: {duration:.0f} seconds\n"
        f"Produce {int(guidance['beats_min'])} to "
        f"{int(guidance['beats_max'])} beats of roughly "
        f"{guidance['beat_asl_s']:.0f} seconds each.\n"
        f"Subject roster (assign per beat; exact names):\n"
        f"{_roster_lines(subjects)}\n\n"
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


# -- Cinematic lints (spec §6) ------------------------------------------------
# Each returns human-readable violations phrased as instructions: the
# runner appends them verbatim to a targeted re-roll prompt, and small
# models correct well against specific complaints.

_TIGHTNESS: Dict[str, int] = {v: i for i, v in enumerate(SHOT_SIZE_VALUES)}


def describe_beat_framing(beat: Mapping[str, Any]) -> str:
    """Short framing summary for re-injection into the next beats call."""
    bits = [
        beat.get("shot_size"),
        beat.get("angle"),
        beat.get("lens"),
        beat.get("composition"),
        beat.get("light_quality"),
    ]
    present = [str(b) for b in bits if b]
    return ", ".join(present) or "unspecified framing"


def lint_scene_charges(
    scenes: Sequence[Mapping[str, Any]], arc: Sequence[Mapping[str, Any]]
) -> List[str]:
    out: List[str] = []
    expected = [a["beat"] for a in arc if isinstance(a, dict) and a.get("beat")]
    got = [b for sc in scenes for b in (sc.get("arc_beats") or [])]
    if expected and got != expected:
        out.append(
            "the scenes' arc_beats concatenated in scene order must be "
            f"exactly {expected} (every outline arc entry in exactly one "
            f"scene, in story order, no gaps); you produced {got}"
        )
    charges = [(sc.get("charge_in"), sc.get("charge_out")) for sc in scenes]
    for i, (cin, cout) in enumerate(charges, 1):
        if cin is None or cout is None:
            out.append(f"scene {i} is missing charge_in/charge_out")
    for i in range(1, len(charges)):
        prev_out, cur_in = charges[i - 1][1], charges[i][0]
        if prev_out is not None and cur_in is not None and prev_out != cur_in:
            out.append(
                f"scene {i + 1} charge_in={cur_in} but scene {i} "
                f"charge_out={prev_out} — the chain must be continuous"
            )
    swings = [
        abs(cout - cin) for cin, cout in charges if cin is not None and cout is not None
    ]
    turn_idx = next(
        (i for i, sc in enumerate(scenes) if "turn" in (sc.get("arc_beats") or [])),
        None,
    )
    if turn_idx is not None and swings:
        cin, cout = charges[turn_idx]
        if cin is not None and cout is not None and abs(cout - cin) < max(swings):
            out.append(
                f"the turn scene (scene {turn_idx + 1}) must have the "
                "largest charge swing of any scene; another scene swings "
                "harder"
            )
    first_in = charges[0][0] if charges else None
    last_out = charges[-1][1] if charges else None
    if (
        first_in is not None
        and last_out is not None
        and first_in != 0
        and last_out != 0
        and (first_in > 0) == (last_out > 0)
    ):
        out.append("the story must not end on the same charge polarity it opened on")
    return out


def lint_shots(panels: Sequence[Mapping[str, Any]], scene_is_turn: bool) -> List[str]:
    out: List[str] = []
    if scene_is_turn:
        turns = sum(1 for p in panels if p.get("is_turn"))
        if turns != 1:
            out.append(
                "this scene contains the story's turn: exactly one shot "
                f"must set is_turn true (you marked {turns})"
            )
    for i, p in enumerate(panels, 1):
        sub = (p.get("subtext") or "").strip()
        if not sub:
            out.append(f"shot {i} has an empty subtext")
        elif sub.lower() == (p.get("action") or "").strip().lower():
            out.append(
                f"shot {i}'s subtext restates its action — subtext is what "
                "the shot means but does not show"
            )
    return out


def lint_beats(
    beats: Sequence[Mapping[str, Any]],
    *,
    is_turn_panel: bool,
    is_scene_opener: bool,
    prev_shot_sizes: Sequence[Optional[str]] = (),
) -> List[str]:
    out: List[str] = []
    sizes = list(prev_shot_sizes) + [b.get("shot_size") for b in beats]
    run = 1
    for i in range(1, len(sizes)):
        if sizes[i] is not None and sizes[i] == sizes[i - 1]:
            run += 1
        else:
            run = 1
        if run == 3:
            out.append(
                f'three consecutive beats use shot_size "{sizes[i]}" — '
                "never the same shot size three beats running; vary the "
                "framing"
            )
    if is_scene_opener and beats:
        if beats[0].get("shot_size") not in ("WS", "EWS"):
            out.append(
                "the first beat of a scene's opening shot must establish "
                "the space wide: use WS or EWS"
            )
    for i, b in enumerate(beats, 1):
        motion = b.get("camera_motion")
        if (
            motion
            and motion != "static"
            and not (b.get("movement_motivation") or "").strip()
        ):
            out.append(
                f'beat {i} has camera_motion "{motion}" but empty '
                "movement_motivation — name what in the subject's behavior "
                'or emotional state pulls the camera, or use "static"'
            )
        if not (b.get("reveals") or "").strip():
            out.append(
                f"beat {i} has an empty reveals — state what this beat "
                "shows that the previous beat did not"
            )
    if is_turn_panel and len(beats) > 1:
        indexed = [
            (i, _TIGHTNESS[b["shot_size"]])
            for i, b in enumerate(beats)
            if b.get("shot_size") in _TIGHTNESS
        ]
        if indexed and min(indexed, key=lambda t: t[1])[0] == 0:
            out.append(
                "this shot is the story's turn: its tightest framing must "
                "land on the beat where the turn hits, not on beat 1"
            )
    return out
