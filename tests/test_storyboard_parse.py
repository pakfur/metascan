"""tests/test_storyboard_parse.py"""

import json

import pytest

from metascan.core.storyboard_parse import (
    PARSE_GRAMMAR,
    PARSE_SYSTEM_PROMPT,
    ParseError,
    build_parse_user_prompt,
    validate_parse_response,
)


def _payload():
    return {
        "subjects": [{"name": "MAYA", "description": "late 20s, red scarf"}],
        "scenes": [
            {
                "name": "Salvage Yard",
                "location": "salvage yard",
                "time_of_day": "dusk",
                "mood": "tense",
                "lighting": "amber haze",
                "panels": [
                    {
                        "shot_size": "ECU",
                        "angle": "eye",
                        "lens": None,
                        "action": "hand rests on hull seam",
                        "subjects": ["MAYA"],
                    }
                ],
            }
        ],
    }


def test_validate_roundtrip():
    out = validate_parse_response(json.dumps(_payload()))
    assert out["subjects"][0]["name"] == "MAYA"
    assert out["scenes"][0]["panels"][0]["shot_size"] == "ECU"


def test_unknown_enum_values_become_none():
    p = _payload()
    p["scenes"][0]["panels"][0]["shot_size"] = "SUPERWIDE"
    p["scenes"][0]["panels"][0]["angle"] = "worm"
    out = validate_parse_response(json.dumps(p))
    panel = out["scenes"][0]["panels"][0]
    assert panel["shot_size"] is None and panel["angle"] is None


def test_panel_without_action_is_dropped():
    p = _payload()
    p["scenes"][0]["panels"].append(
        {"shot_size": None, "angle": None, "lens": None, "action": "  ", "subjects": []}
    )
    out = validate_parse_response(json.dumps(p))
    assert len(out["scenes"][0]["panels"]) == 1


def test_no_scenes_raises():
    with pytest.raises(ParseError):
        validate_parse_response(json.dumps({"subjects": [], "scenes": []}))


def test_garbage_raises():
    with pytest.raises(ParseError):
        validate_parse_response("not json")


def test_grammar_has_no_backslash_hyphen_escape():
    assert "\\-" not in PARSE_GRAMMAR  # invalid GBNF escape -> llama SIGSEGV
    assert "::=" in PARSE_GRAMMAR


def test_grammar_terminals_cover_shape():
    for token in (
        '"subjects"',
        '"scenes"',
        '"panels"',
        '"action"',
        '"shot_size"',
        '"ECU"',
        '"pov"',
        '"macro"',
    ):
        assert token.replace('"', '\\"') in PARSE_GRAMMAR or token in PARSE_GRAMMAR


def test_system_prompt_and_user_template():
    assert "json" in PARSE_SYSTEM_PROMPT.lower()
    assert (
        "verbatim" in PARSE_SYSTEM_PROMPT.lower()
        or "exact" in PARSE_SYSTEM_PROMPT.lower()
    )
    up = build_parse_user_prompt("INT. YARD - DUSK")
    assert "INT. YARD - DUSK" in up
