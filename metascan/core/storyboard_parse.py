"""Freeform screenplay text -> structured storyboard, via one
GBNF-constrained VlmClient.generate_text call.

This module is pure: prompt text, the grammar, and response validation.
The VLM call itself lives in storyboard_runner.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

SHOT_SIZE_VALUES = ("ECU", "CU", "MCU", "MS", "MLS", "WS", "EWS")
ANGLE_VALUES = ("eye", "low", "high", "overhead", "dutch", "ots", "pov")
LENS_VALUES = ("wide", "normal", "tele", "macro")


class ParseError(ValueError):
    """The VLM response could not be validated into a storyboard tree."""


PARSE_SYSTEM_PROMPT = """\
You convert loosely written scene text into structured storyboard JSON.

Rules:
- Every named character or recurring subject becomes a subjects[] entry.
  Copy the author's physical description of each subject verbatim into
  description — do not paraphrase, embellish, or invent details.
- Each scene heading or clear location change becomes a scenes[] entry.
- Each shot or keyframe sentence becomes one panel. Keep the author's
  action wording; do not merge shots.
- shot_size, angle, and lens: fill only when the text states or clearly
  implies them; otherwise use null.
- A panel's subjects array lists the names of subjects visible in that
  shot, most important first.
Output only the JSON object.
"""


def build_parse_user_prompt(text: str) -> str:
    return f"Scene text:\n\n{text}\n\nProduce the storyboard JSON."


def _alts(values: Tuple[str, ...]) -> str:
    quoted = " | ".join('"\\"{0}\\""'.format(v) for v in values)
    return '{0} | "null"'.format(quoted)


# JSON-shaped GBNF, built with str.format() rather than an f-string so the
# literal braces in the JSON-object rules ("{{" / "}}") don't collide with
# format-slot syntax. Hyphens appear only as literal range operators inside
# character classes (never the invalid escape "\-"): see CLAUDE.md.
_GRAMMAR_TEMPLATE = r"""root ::= "{{" ws "\"subjects\"" ws ":" ws subjects ws "," ws "\"scenes\"" ws ":" ws scenes ws "}}"
subjects ::= "[" ws (subject (ws "," ws subject)*)? ws "]"
subject ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"description\"" ws ":" ws string ws "}}"
scenes ::= "[" ws (scene (ws "," ws scene)*)? ws "]"
scene ::= "{{" ws "\"name\"" ws ":" ws string ws "," ws "\"location\"" ws ":" ws nullable ws "," ws "\"time_of_day\"" ws ":" ws nullable ws "," ws "\"mood\"" ws ":" ws nullable ws "," ws "\"lighting\"" ws ":" ws nullable ws "," ws "\"panels\"" ws ":" ws panels ws "}}"
panels ::= "[" ws (panel (ws "," ws panel)*)? ws "]"
panel ::= "{{" ws "\"shot_size\"" ws ":" ws shotsize ws "," ws "\"angle\"" ws ":" ws angle ws "," ws "\"lens\"" ws ":" ws lens ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"subjects\"" ws ":" ws namelist ws "}}"
shotsize ::= {shotsize_alts}
angle ::= {angle_alts}
lens ::= {lens_alts}
namelist ::= "[" ws (string (ws "," ws string)*)? ws "]"
nullable ::= string | "null"
string ::= "\"" char* "\""
char ::= [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \t\n]*
"""

PARSE_GRAMMAR = _GRAMMAR_TEMPLATE.format(
    shotsize_alts=_alts(SHOT_SIZE_VALUES),
    angle_alts=_alts(ANGLE_VALUES),
    lens_alts=_alts(LENS_VALUES),
)


def _clean_str(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_parse_response(raw: str) -> Dict[str, Any]:
    """Validate + normalize the model's JSON into the shape
    DatabaseManager.replace_storyboard_structure consumes."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ParseError(f"response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ParseError("response is not a JSON object")

    subjects: List[Dict[str, str]] = []
    for s in data.get("subjects") or []:
        if not isinstance(s, dict):
            continue
        name = _clean_str(s.get("name"))
        desc = _clean_str(s.get("description"))
        if name and desc:
            subjects.append({"name": name, "description": desc})

    scenes: List[Dict[str, Any]] = []
    for sc in data.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        name = _clean_str(sc.get("name"))
        if not name:
            continue
        panels: List[Dict[str, Any]] = []
        for p in sc.get("panels") or []:
            if not isinstance(p, dict):
                continue
            action = _clean_str(p.get("action"))
            if not action:
                continue
            shot = p.get("shot_size")
            angle = p.get("angle")
            lens = p.get("lens")
            panels.append(
                {
                    "shot_size": shot if shot in SHOT_SIZE_VALUES else None,
                    "angle": angle if angle in ANGLE_VALUES else None,
                    "lens": lens if lens in LENS_VALUES else None,
                    "action": action,
                    "subjects": [
                        n
                        for n in (p.get("subjects") or [])
                        if isinstance(n, str) and n.strip()
                    ],
                }
            )
        scenes.append(
            {
                "name": name,
                "location": _clean_str(sc.get("location")),
                "time_of_day": _clean_str(sc.get("time_of_day")),
                "mood": _clean_str(sc.get("mood")),
                "lighting": _clean_str(sc.get("lighting")),
                "panels": panels,
            }
        )

    if not scenes:
        raise ParseError("no scenes found in the response")
    return {"subjects": subjects, "scenes": scenes}
