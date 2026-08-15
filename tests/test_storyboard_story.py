"""Pure story-engine module: grammars, validators, duration math (spec V1 §4)."""

import json

import pytest

from metascan.core import storyboard_story as story
from metascan.core.storyboard_story import (
    BEATS_GRAMMAR,
    CAMERA_MOTION_VALUES,
    OUTLINE_GRAMMAR,
    SCENES_GRAMMAR,
    SHOTS_GRAMMAR,
    StoryError,
    rescale_beat_durations,
    validate_beats_response,
    validate_outline_response,
    validate_scenes_response,
    validate_shots_response,
)

ALL_GRAMMARS = (OUTLINE_GRAMMAR, SCENES_GRAMMAR, SHOTS_GRAMMAR, BEATS_GRAMMAR)


def test_grammars_have_no_invalid_hyphen_escape():
    for g in ALL_GRAMMARS:
        assert r"\-" not in g  # SIGSEGVs llama-server (CLAUDE.md)


def test_grammars_have_root_and_balanced_quotes():
    for g in ALL_GRAMMARS:
        assert g.startswith("root ::=")
        assert g.count('"') % 2 == 0


def test_validate_outline_happy_path():
    raw = json.dumps(
        {
            "logline": "A scavenger finds a live ship.",
            "tone": "tense, hopeful",
            "duration_target_s": 90,
            "subjects": [
                {
                    "name": "Maya",
                    "description": "late 20s, shaved head",
                    "voice": "clear alto",
                },
                {"name": "The Ship", "description": "rusted hull", "voice": None},
            ],
            "arc": [
                {"beat": "setup", "summary": "Maya scavenges alone."},
                {"beat": "resolution", "summary": "The ship lifts off."},
            ],
        }
    )
    out = validate_outline_response(raw)
    assert out["logline"].startswith("A scavenger")
    assert out["duration_target_s"] == 90.0
    assert out["subjects"][0]["voice"] == "clear alto"
    assert [a["beat"] for a in out["arc"]] == ["setup", "resolution"]


def test_validate_outline_rejects_garbage_and_empty_arc():
    with pytest.raises(StoryError):
        validate_outline_response("not json")
    with pytest.raises(StoryError):
        validate_outline_response(
            json.dumps(
                {
                    "logline": "x",
                    "tone": "y",
                    "duration_target_s": 60,
                    "subjects": [],
                    "arc": [],
                }
            )
        )


def test_validate_scenes_happy_and_empty():
    raw = json.dumps(
        [
            {
                "name": "Salvage yard",
                "subtitle": None,
                "setting": "twisted hulls",
                "location": "yard",
                "time_of_day": "dusk",
                "mood": "tense",
                "lighting": "amber haze",
                "notes": None,
            },
        ]
    )
    scenes = validate_scenes_response(raw)
    assert scenes[0]["name"] == "Salvage yard"
    assert scenes[0]["subtitle"] is None
    with pytest.raises(StoryError):
        validate_scenes_response("[]")


def test_validate_shots_resolves_roster_and_warns_on_unknown():
    raw = json.dumps(
        [
            {
                "shot_size": "WS",
                "angle": "eye",
                "lens": "wide",
                "action": "Maya crosses the yard",
                "subjects": ["maya", "Ghost"],
                "duration_s": 12,
            },
        ]
    )
    panels, warnings = validate_shots_response(raw, {"maya": 7})
    assert panels[0]["subject_ids"] == [7]
    assert panels[0]["duration_s"] == 12.0
    assert any("Ghost" in w for w in warnings)
    with pytest.raises(StoryError):
        validate_shots_response("[]", {})


def test_validate_beats_camera_enums_and_dialog_roster():
    raw = json.dumps(
        [
            {
                "duration_s": 4,
                "action": "she kneels",
                "camera_motion": "push_in",
                "camera_amplitude": "small",
                "camera_speed": "slow",
                "is_cut": False,
                "sound": "wind",
                "dialog": [
                    {
                        "subject": "Maya",
                        "voice": None,
                        "delivery": "whispered",
                        "language": "English",
                        "text": "Hello, old girl.",
                    }
                ],
            },
            {
                "duration_s": 5,
                "action": "hatch opens",
                "camera_motion": "not_a_motion",
                "camera_amplitude": None,
                "camera_speed": None,
                "is_cut": True,
                "sound": None,
                "dialog": [],
            },
        ]
    )
    beats = validate_beats_response(raw, {"maya": 7})
    assert beats[0]["camera_motion"] == "push_in"
    assert beats[0]["dialog"][0]["subject_id"] == 7
    assert beats[0]["dialog"][0]["text"] == "Hello, old girl."
    assert beats[1]["camera_motion"] is None  # unknown enum dropped to None
    assert beats[1]["is_cut"] == 1
    with pytest.raises(StoryError):
        validate_beats_response("[]", {})


def test_rescale_beat_durations_only_outside_tolerance():
    beats = [{"duration_s": 2.0}, {"duration_s": 2.0}]
    # sum 4.0 vs target 16.0 -> way out, rescaled to sum ~16
    rescale_beat_durations(beats, 16.0)
    assert abs(sum(b["duration_s"] for b in beats) - 16.0) < 0.3
    beats2 = [{"duration_s": 5.0}, {"duration_s": 6.0}]
    rescale_beat_durations(beats2, 12.0)  # sum 11 within ±25% of 12 -> untouched
    assert [b["duration_s"] for b in beats2] == [5.0, 6.0]


def test_camera_vocabulary_matches_spec():
    for v in ("push_in", "static", "roll_ccw", "tracking", "pov"):
        assert v in CAMERA_MOTION_VALUES


def test_system_prompts_resolve_from_store():
    for key in (
        "STORY_OUTLINE_SYSTEM",
        "STORY_SCENES_SYSTEM",
        "STORY_SHOTS_SYSTEM",
        "STORY_BEATS_SYSTEM",
    ):
        assert isinstance(getattr(story, key), str)
        assert len(getattr(story, key)) > 100
