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


def test_shots_validator_slim_shape():
    raw = '[{"action": "The chase begins", "duration_s": 10}]'
    panels = story.validate_shots_response(raw)
    assert panels == [{"action": "The chase begins", "duration_s": 10.0}]


def test_shots_grammar_has_no_framing_or_subjects():
    for gone in ("shot_size", "subjects", "angle", "lens"):
        assert gone not in story.SHOTS_GRAMMAR


def test_validate_shots_raises_on_empty():
    with pytest.raises(StoryError):
        validate_shots_response("[]")


def test_beats_validator_maps_subjects_and_framing():
    roster = {"mara": 3, "june": 5}
    raw = (
        '[{"duration_s": 4, "action": "Mara turns", '
        '"shot_size": "CU", "angle": "low", "lens": "bogus", '
        '"subjects": ["Mara", "Nobody"], '
        '"camera_motion": "static", "camera_amplitude": null, '
        '"camera_speed": null, "is_cut": false, "sound": null, '
        '"dialog": []}]'
    )
    beats, warnings = story.validate_beats_response(raw, roster)
    assert beats[0]["shot_size"] == "CU"
    assert beats[0]["angle"] == "low"
    assert beats[0]["lens"] is None  # bogus enum drops to NULL
    assert beats[0]["subject_ids"] == [3]  # unknown name dropped
    assert any("Nobody" in w for w in warnings)


def test_beats_grammar_carries_framing_and_subjects():
    for needed in ("shot_size", "angle", "lens", "subjects"):
        assert needed in story.BEATS_GRAMMAR


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
    beats, warnings = validate_beats_response(raw, {"maya": 7})
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


def test_build_shots_user_prompt_states_max_shot_seconds():
    prompt = story.build_shots_user_prompt(
        json.dumps({"logline": "L"}),
        {"name": "Yard", "setting": "hulls"},
        [],
        None,
        None,
        max_shot_s=10.0,
    )
    assert "at most 10 seconds" in prompt
    # default stays 15s when the caller doesn't override it.
    default_prompt = story.build_shots_user_prompt(
        json.dumps({"logline": "L"}),
        {"name": "Yard", "setting": "hulls"},
        [],
        None,
        None,
    )
    assert "at most 15 seconds" in default_prompt


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


def test_invalid_json_truncation_hint():
    # Long response cut mid-string (the max_tokens signature) gets the hint...
    truncated = '[{"action": "' + "she walks through the yard " * 10
    with pytest.raises(StoryError, match="truncated"):
        validate_shots_response(truncated)
    # ...but short garbage does not claim truncation.
    try:
        validate_shots_response("not json")
    except StoryError as e:
        assert "truncated" not in str(e)


def test_grammar_whitespace_is_bounded():
    # Unbounded ws lets the sampler wander into an infinite newline loop
    # mid-object, burning all of max_tokens on whitespace and returning
    # truncated JSON (regression: ~1 in 3 beats calls failed this way).
    for g in (OUTLINE_GRAMMAR, SCENES_GRAMMAR, SHOTS_GRAMMAR, BEATS_GRAMMAR):
        assert "ws ::= [ \\t\\n]{0,20}" in g
        assert "]*" not in g


def test_grammars_carry_repetition_bounds():
    # Unbounded arrays let the model ramble past any token cap; the
    # bounds are the hard stop (regression: a 25-shot scene truncated
    # twice in a row at max_tokens).
    assert '(ws "," ws shot){0,5}' in SHOTS_GRAMMAR
    assert '(ws "," ws scene){1,7}' in SCENES_GRAMMAR
    assert '(ws "," ws beat){1,5}' in BEATS_GRAMMAR
    assert '(ws "," ws line){0,3}' in BEATS_GRAMMAR
    assert '(ws "," ws arcitem){1,6}' in OUTLINE_GRAMMAR


def test_truncated_shots_array_salvages_leading_elements():
    good = '{"action": "Maya crosses the yard", "duration_s": 10}'
    truncated = f'[{good}, {good}, {{"action": "she rea'
    panels = validate_shots_response(truncated)
    assert len(panels) == 2
    assert panels[0]["duration_s"] == 10.0


def test_truncated_beats_array_salvages_across_nested_dialog():
    beat = (
        '{"duration_s": 4, "action": "she kneels", "shot_size": null, '
        '"angle": null, "lens": null, "subjects": [], '
        '"camera_motion": null, '
        '"camera_amplitude": null, "camera_speed": null, "is_cut": false, '
        '"sound": null, "dialog": [{"subject": null, "voice": "low voice", '
        '"delivery": null, "language": "English", "text": "Easy now."}]}'
    )
    # Truncation lands INSIDE the second beat's nested dialog object —
    # salvage must walk back past the inner '}' to the first complete beat.
    truncated = (
        f'[{beat}, {{"duration_s": 5, "action": "hatch opens", '
        f'"camera_motion": null, "camera_amplitude": null, '
        f'"camera_speed": null, "is_cut": true, "sound": null, '
        f'"dialog": [{{"subject": null, "voice": "gravel'
    )
    beats, warnings = validate_beats_response(truncated, {})
    assert len(beats) == 1
    assert beats[0]["dialog"][0]["text"] == "Easy now."
