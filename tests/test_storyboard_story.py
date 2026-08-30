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


def test_scenes_grammar_and_validator_carry_brief():
    assert '"\\"brief\\""' in story.SCENES_GRAMMAR
    raw = json.dumps(
        [
            {
                "name": "S",
                "arc_beats": [],
                "charge_in": 0,
                "charge_out": 1,
                "function": "negotiation",
                "brief": "A asks B.",
            }
        ]
    )
    assert story.validate_scenes_response(raw)[0]["brief"] == "A asks B."


def test_scenes_prompt_includes_premise():
    p = story.build_scenes_user_prompt('{"logline": "L"}', "Two rivals meet.")
    assert "Premise:\nTwo rivals meet." in p and "Story outline:" in p


def test_shots_validator_slim_shape():
    raw = '[{"action": "The chase begins", "duration_s": 10}]'
    panels = story.validate_shots_response(raw)
    assert panels == [
        {
            "action": "The chase begins",
            "duration_s": 10.0,
            "subtext": None,
            "is_turn": 0,
        }
    ]


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


def _guidance():
    return story.pacing_guidance("standard", 15.0)


def test_build_shots_user_prompt_carries_spine_and_pacing():
    scene = {
        "name": "Yard",
        "setting": "hulls",
        "mood": "tense",
        "lighting": None,
        "time_of_day": "dusk",
        "arc_beats": ["turn"],
        "charge_in": -3,
        "charge_out": 2,
    }
    p = story.build_shots_user_prompt(
        "{}", scene, [], None, None, _guidance(), is_turn_scene=True
    )
    assert "Arc stages this scene covers: turn" in p
    assert "opens at -3, closes at 2" in p
    assert "exactly one shot must set is_turn to true" in p.lower()
    assert "between 2 and 4 shots" in p
    assert "8 and 15 seconds" in p
    p2 = story.build_shots_user_prompt(
        "{}",
        dict(scene, arc_beats=["setup"]),
        [],
        None,
        None,
        _guidance(),
        is_turn_scene=False,
    )
    assert "every shot sets is_turn false" in p2.lower()


def test_build_beats_user_prompt_reinjects_bible_and_context():
    outline = {"logline": "L", "tone": "grim"}
    scene = {
        "name": "Yard",
        "setting": "rusting hulls",
        "location": None,
        "time_of_day": "dusk",
        "mood": "tense",
        "lighting": "sodium lamps",
    }
    panel = {
        "action": "Maya crosses",
        "duration_s": 10.0,
        "subtext": "she is afraid",
        "is_turn": 1,
    }
    p = story.build_beats_user_prompt(
        outline,
        scene,
        panel,
        [],
        _guidance(),
        prev_panel_action="He watches",
        prev_beat_summary="MCU, low, soft",
    )
    assert "Tone: grim" in p
    assert "rusting hulls" in p and "sodium lamps" in p and "dusk" in p
    assert "Shot subtext: she is afraid" in p
    assert "tightest" in p.lower()  # turn directive
    assert "Previous shot in this scene: He watches" in p
    assert "MCU, low, soft" in p
    assert "2 to 4 beats" in p
    # opener variant
    p2 = story.build_beats_user_prompt(
        outline, scene, dict(panel, is_turn=0), [], _guidance(), None, None
    )
    assert "opening shot" in p2 and "WS or EWS" in p2


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


def test_story_scale_grammar_caps():
    # "standard" must stay byte-identical to the constants (pre-scale
    # behavior); unknown scales fall back to it.
    assert story.outline_grammar("standard") == OUTLINE_GRAMMAR
    assert story.scenes_grammar("standard") == SCENES_GRAMMAR
    assert story.shots_grammar("standard") == SHOTS_GRAMMAR
    assert story.shots_grammar("bogus") == SHOTS_GRAMMAR
    # Per-scale hard ceilings.
    assert '(ws "," ws shot){0,3}' in story.shots_grammar("short")
    assert '(ws "," ws shot){0,11}' in story.shots_grammar("extended")
    assert '(ws "," ws scene){0,3}' in story.scenes_grammar("short")
    assert '(ws "," ws scene){1,11}' in story.scenes_grammar("extended")
    assert '(ws "," ws arcitem){1,3}' in story.outline_grammar("short")
    assert '(ws "," ws arcitem){1,11}' in story.outline_grammar("extended")


def test_story_scale_shot_guidance_and_tokens():
    std = story.pacing_guidance("standard", 15.0)
    ext = story.pacing_guidance("standard", 15.0, "extended")
    sht = story.pacing_guidance("standard", 15.0, "short")
    assert (std["shots_min"], std["shots_max"]) == (2, 4)
    assert (ext["shots_min"], ext["shots_max"]) == (4, 8)
    assert (sht["shots_min"], sht["shots_max"]) == (1, 2)
    # Extended propulsive hits the extended grammar ceiling of 12.
    assert story.pacing_guidance("propulsive", 15.0, "extended")["shots_max"] == 12
    # Beat guidance never scales -- beats are bounded by the clip cap.
    assert ext["beats_min"] == std["beats_min"]
    assert ext["beats_max"] == std["beats_max"]
    assert ext["beat_asl_s"] == std["beat_asl_s"]
    # Token budgets scale with the list caps; beats stay at 1600.
    assert story.stage_max_tokens("shots", "standard") == 1600
    assert story.stage_max_tokens("shots", "extended") == 3200
    assert story.stage_max_tokens("scenes", "extended") == 4096
    assert story.stage_max_tokens("beats", "extended") == 1600


def test_outline_prompt_carries_scale_hint():
    p_std = story.build_outline_user_prompt("a premise", [])
    assert p_std == story.build_outline_user_prompt("a premise", [], "standard")
    assert "extended middle" in story.build_outline_user_prompt(
        "a premise", [], "extended"
    )
    assert "tight" in story.build_outline_user_prompt("a premise", [], "short")


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


def test_pacing_table_and_shot_cap_clamp():
    g = story.pacing_guidance("standard", 15.0)
    assert g["panel_min_s"] == 8.0 and g["panel_max_s"] == 15.0
    assert g["shots_min"] == 2 and g["shots_max"] == 4
    assert g["beats_min"] == 2 and g["beats_max"] == 4
    assert g["beat_asl_s"] == 4.5
    clamped = story.pacing_guidance("contemplative", 8.0)
    assert clamped["panel_max_s"] == 8.0
    assert clamped["panel_min_s"] == 8.0  # min never exceeds max
    assert story.pacing_guidance("bogus", 15.0) == story.pacing_guidance(
        "standard", 15.0
    )


def test_new_vocabularies_match_spec():
    assert story.PACING_VALUES == ("contemplative", "standard", "propulsive")
    assert "negative_space" in story.COMPOSITION_VALUES
    assert len(story.COMPOSITION_VALUES) == 8
    assert story.LIGHT_QUALITY_VALUES == (
        "hard",
        "soft",
        "dappled",
        "practical",
        "window",
        "firelight",
        "ambient",
    )


def test_outline_pacing_validated_with_fallback():
    base = {
        "logline": "L",
        "tone": "T",
        "duration_target_s": 60,
        "subjects": [],
        "arc": [{"beat": "setup", "summary": "s"}],
    }
    good = dict(base, pacing="propulsive")
    assert story.validate_outline_response(json.dumps(good))["pacing"] == "propulsive"
    bad = dict(base, pacing="glacial")
    assert story.validate_outline_response(json.dumps(bad))["pacing"] == "standard"
    assert story.validate_outline_response(json.dumps(base))["pacing"] == "standard"


def test_scenes_dramatic_fields_validated():
    raw = json.dumps(
        [
            {
                "name": "A",
                "arc_beats": ["setup", "bogus", "turn"],
                "charge_in": -2,
                "charge_out": 3,
            },
            {"name": "B", "charge_in": 99, "charge_out": "x"},
        ]
    )
    scenes = story.validate_scenes_response(raw)
    assert scenes[0]["arc_beats"] == ["setup", "turn"]  # unknown stage dropped
    assert scenes[0]["charge_in"] == -2 and scenes[0]["charge_out"] == 3
    assert scenes[1]["arc_beats"] == []
    assert scenes[1]["charge_in"] is None  # out of range
    assert scenes[1]["charge_out"] is None  # garbage


def test_shots_subtext_and_is_turn():
    raw = json.dumps(
        [
            {"action": "a", "duration_s": 8, "subtext": " hides it ", "is_turn": True},
            {"action": "b", "duration_s": 8},
        ]
    )
    panels = story.validate_shots_response(raw)
    assert panels[0]["subtext"] == "hides it" and panels[0]["is_turn"] == 1
    assert panels[1]["subtext"] is None and panels[1]["is_turn"] == 0


def test_beats_visual_grammar_fields_and_static_nulling():
    beat = {
        "duration_s": 3,
        "action": "a",
        "shot_size": "CU",
        "angle": None,
        "lens": None,
        "subjects": [],
        "camera_motion": "static",
        "camera_amplitude": "large",
        "camera_speed": "fast",
        "movement_motivation": "won't matter",
        "is_cut": False,
        "sound": None,
        "dialog": [],
        "composition": "negative_space",
        "light_quality": "firelight",
        "emotional_intent": "jaw set",
        "reveals": "the empty chair",
    }
    beats, _ = story.validate_beats_response(json.dumps([beat]), {})
    b = beats[0]
    assert b["composition"] == "negative_space"
    assert b["light_quality"] == "firelight"
    assert b["emotional_intent"] == "jaw set"
    assert b["reveals"] == "the empty chair"
    # static motion mechanically nulls amplitude/speed/motivation
    assert b["camera_amplitude"] is None
    assert b["camera_speed"] is None
    assert b["movement_motivation"] is None
    bad = dict(beat, composition="rule_of_odds", light_quality="neon")
    beats, _ = story.validate_beats_response(json.dumps([bad]), {})
    assert beats[0]["composition"] is None and beats[0]["light_quality"] is None


def test_new_grammars_carry_new_fields():
    # Field names are GBNF string literals, so the JSON quotes are
    # backslash-escaped in the grammar source (matches every existing
    # field, e.g. \"logline\") — not bare '"pacing"'.
    assert r"\"pacing\"" in story.OUTLINE_GRAMMAR
    for token in (r"\"arc_beats\"", r"\"charge_in\"", r"\"charge_out\""):
        assert token in story.SCENES_GRAMMAR
    for token in (r"\"subtext\"", r"\"is_turn\""):
        assert token in story.SHOTS_GRAMMAR
    for token in (
        r"\"composition\"",
        r"\"light_quality\"",
        r"\"emotional_intent\"",
        r"\"reveals\"",
        r"\"movement_motivation\"",
    ):
        assert token in story.BEATS_GRAMMAR


def _scene(name, arc_beats, cin, cout):
    return {"name": name, "arc_beats": arc_beats, "charge_in": cin, "charge_out": cout}


def test_lint_scene_charges_happy_path():
    arc = [{"beat": "setup"}, {"beat": "turn"}, {"beat": "resolution"}]
    scenes = [
        _scene("A", ["setup"], -1, -3),
        _scene("B", ["turn", "resolution"], -3, 2),
    ]
    assert story.lint_scene_charges(scenes, arc) == []


def test_lint_scene_charges_catches_each_rule():
    arc = [{"beat": "setup"}, {"beat": "turn"}]
    # broken chain + missing charge
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], -1, -2), _scene("B", ["turn"], 1, None)], arc
    )
    assert any("chain must be continuous" in m for m in v)
    assert any("missing charge" in m for m in v)
    # coverage: arc stage never lands / wrong order
    v = story.lint_scene_charges([_scene("A", ["turn", "setup"], 0, 4)], arc)
    assert any("arc_beats" in m for m in v)
    # turn scene must swing hardest
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], -4, 4), _scene("B", ["turn"], 4, 3)], arc
    )
    assert any("largest charge swing" in m for m in v)
    # polarity flip
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], 2, 4), _scene("B", ["turn"], 4, 1)], arc
    )
    assert any("polarity" in m for m in v)
    # neutral open (0) never trips the polarity rule
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], 0, 2), _scene("B", ["turn"], 2, 4)], arc
    )
    assert not any("polarity" in m for m in v)


def test_lint_shots_turn_and_subtext():
    ok = [
        {"action": "she waits", "subtext": "she is afraid to knock", "is_turn": 0},
        {"action": "he opens", "subtext": "he knew she'd come", "is_turn": 1},
    ]
    assert story.lint_shots(ok, scene_is_turn=True) == []
    v = story.lint_shots(ok, scene_is_turn=False)
    assert v == []  # stray turns are zeroed mechanically, not linted
    two_turns = [dict(ok[0], is_turn=1), ok[1]]
    v = story.lint_shots(two_turns, scene_is_turn=True)
    assert any("exactly one shot" in m for m in v)
    v = story.lint_shots(
        [{"action": "she waits", "subtext": " She Waits ", "is_turn": 1}],
        scene_is_turn=True,
    )
    assert any("restates" in m for m in v)
    v = story.lint_shots([{"action": "a", "subtext": None, "is_turn": 1}], True)
    assert any("empty subtext" in m for m in v)


def _beat(size, motion=None, motivation=None, reveals="something new"):
    return {
        "shot_size": size,
        "camera_motion": motion,
        "movement_motivation": motivation,
        "reveals": reveals,
    }


def test_lint_beats_rules():
    # triple repeat, including across the panel boundary
    v = story.lint_beats(
        [_beat("MCU"), _beat("MCU")],
        is_turn_panel=False,
        is_scene_opener=False,
        prev_shot_sizes=["MCU"],
    )
    assert any("three consecutive" in m for m in v)
    # opener must be wide
    v = story.lint_beats([_beat("CU")], is_turn_panel=False, is_scene_opener=True)
    assert any("establish" in m for m in v)
    assert (
        story.lint_beats([_beat("WS")], is_turn_panel=False, is_scene_opener=True) == []
    )
    # unmotivated move
    v = story.lint_beats(
        [_beat("WS", motion="push_in")], is_turn_panel=False, is_scene_opener=False
    )
    assert any("movement_motivation" in m for m in v)
    # empty reveals
    v = story.lint_beats(
        [_beat("WS", reveals=None)], is_turn_panel=False, is_scene_opener=False
    )
    assert any("reveals" in m for m in v)
    # turn panel: tightest framing must not sit on beat 1
    v = story.lint_beats(
        [_beat("ECU"), _beat("WS")], is_turn_panel=True, is_scene_opener=False
    )
    assert any("tightest" in m for m in v)
    assert (
        story.lint_beats(
            [_beat("WS"), _beat("ECU")], is_turn_panel=True, is_scene_opener=False
        )
        == []
    )


def test_describe_beat_framing():
    assert story.describe_beat_framing(
        {
            "shot_size": "MCU",
            "angle": "low",
            "lens": None,
            "composition": "centered",
            "light_quality": "soft",
        }
    ) == ("MCU, low, centered, soft")
    assert story.describe_beat_framing({}) == "unspecified framing"
