"""Tests for metascan/core/i2v_templates.py -- i2v découpage templates.

A template owns the shot structure of an i2v clip; the VLM fills only
prose. Loader tests here, grammar/assembly/lint tests are appended in
later tasks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from metascan.core.i2v_templates import (
    I2vTemplateError,
    get_i2v_template,
    load_i2v_templates,
    parse_i2v_template,
    reload_i2v_templates,
    shots_of,
    summarize_i2v_template,
)

# The dialog example from the spec, verbatim in structure.
DIALOG: Dict[str, Any] = {
    "id": "dialog_ots_15",
    "name": "Dialog — over-the-shoulder",
    "description": "Two people talking.",
    "duration_s": 15,
    "look": "cinematic, photorealistic skin textures, fine grain",
    "soundscape_hint": "room ambience appropriate to the setting",
    "roles": [
        {"id": "A", "note": "the listener first; speaks second"},
        {"id": "B", "note": "speaks first"},
    ],
    "beats": [
        {
            "start_s": 0,
            "end_s": 4,
            "transition": "continuous",
            "cast": ["A", "B"],
            "speaker": "B",
            "camera": {
                "shot_size": "MS",
                "angle": "ots",
                "camera_motion": "truck_right",
                "camera_speed": "slow",
            },
            "note": "over A's shoulder onto B, who is speaking",
        },
        {
            "start_s": 4,
            "end_s": 5,
            "transition": "cut",
            "cast": ["B"],
            "speaker": None,
            "camera": {"shot_size": "CU", "angle": "eye", "camera_motion": "static"},
            "note": "B's face; a reaction, not a line",
        },
        {
            "start_s": 5,
            "end_s": 10,
            "transition": "j_cut",
            "cast": ["A", "B"],
            "speaker": "A",
            "camera": {
                "shot_size": "MS",
                "angle": "eye",
                "camera_motion": "pull_out",
                "camera_speed": "slow",
            },
            "note": "A's line starts before we see A; dolly back to a two-shot",
        },
        {
            "start_s": 10,
            "end_s": 15,
            "transition": "continuous",
            "cast": ["A", "B"],
            "speaker": None,
            "camera": {
                "shot_size": "MS",
                "angle": "eye",
                "camera_motion": "arc",
                "camera_amplitude": "small",
            },
            "note": "B smiles and nods",
        },
    ],
}


def _with(**changes: Any) -> Dict[str, Any]:
    d = copy.deepcopy(DIALOG)
    d.update(changes)
    return d


def _beat_with(index: int, **changes: Any) -> Dict[str, Any]:
    d = copy.deepcopy(DIALOG)
    d["beats"][index].update(changes)
    return d


@pytest.fixture(autouse=True)
def _fresh_cache():
    reload_i2v_templates()
    yield
    reload_i2v_templates()


# ---- parse -------------------------------------------------------------


def test_parse_the_dialog_example():
    t = parse_i2v_template(DIALOG)
    assert t.id == "dialog_ots_15"
    assert t.duration_s == 15.0
    assert [r.id for r in t.roles] == ["A", "B"]
    assert len(t.beats) == 4
    assert t.beats[0].camera.angle == "ots"
    assert t.beats[0].camera.camera_speed == "slow"
    assert t.beats[0].camera.camera_amplitude is None
    assert t.beats[2].transition == "j_cut"
    assert t.beats[2].speaker == "A"
    assert t.beats[1].speaker is None
    assert t.beats[1].cast == ("B",)


def test_optional_fields_default_to_empty():
    d = _with()
    del d["look"]
    del d["soundscape_hint"]
    del d["beats"][0]["note"]
    t = parse_i2v_template(d)
    assert t.look == ""
    assert t.soundscape_hint == ""
    assert t.beats[0].note == ""


def test_roles_may_be_empty_when_no_beat_casts_anyone():
    d = _with(roles=[])
    for b in d["beats"]:
        b["cast"] = []
        b["speaker"] = None
    t = parse_i2v_template(d)
    assert t.roles == ()


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["beats"][0]["camera"].update(shot_size="OTS"), "shot_size"),
        (lambda d: d["beats"][0]["camera"].update(angle="EYE"), "angle"),
        (lambda d: d["beats"][0]["camera"].update(angle="pov"), "angle"),
        (
            lambda d: d["beats"][0]["camera"].update(camera_motion="STATIC"),
            "camera_motion",
        ),
        (
            lambda d: d["beats"][0]["camera"].update(camera_motion="pov"),
            "camera_motion",
        ),
        (
            lambda d: d["beats"][0]["camera"].update(camera_amplitude="huge"),
            "camera_amplitude",
        ),
        (
            lambda d: d["beats"][0]["camera"].update(camera_speed="medium"),
            "camera_speed",
        ),
        (lambda d: d["beats"][0]["camera"].update(lens="fisheye"), "lens"),
        (lambda d: d["beats"][0]["camera"].pop("shot_size"), "shot_size"),
        (lambda d: d["beats"][1].update(transition="dissolve"), "transition"),
        (lambda d: d["beats"][0].update(transition="cut"), "first beat"),
        (lambda d: d["beats"][0].update(cast=["A", "Z"]), "cast"),
        (lambda d: d["beats"][1].update(speaker="A"), "speaker"),  # A not in cast
        (lambda d: d["beats"][1].update(speaker="Z"), "speaker"),
        (lambda d: d["beats"][1].update(start_s=4.5), "start_s"),  # gap
        (lambda d: d["beats"][3].update(end_s=14), "end_s"),  # short of duration
        (lambda d: d["beats"][0].update(start_s=1), "start_s"),  # not 0
        (lambda d: d["beats"][1].update(end_s=4.2), "0.5"),  # beat too short
        (lambda d: d.update(beats=[]), "beats"),
        (lambda d: d.update(duration_s=0), "duration_s"),
        (lambda d: d.update(roles=[{"id": "A"}, {"id": "A"}]), "duplicate"),
        (lambda d: d.update(roles=[{"id": "bad id"}, {"id": "B"}]), "role id"),
        (lambda d: d.pop("name"), "name"),
    ],
)
def test_parse_rejects_bad_templates_naming_the_field(mutate, fragment):
    d = copy.deepcopy(DIALOG)
    mutate(d)
    with pytest.raises(I2vTemplateError) as excinfo:
        parse_i2v_template(d, where="dialog_ots_15.json")
    assert fragment in str(excinfo.value)
    assert "dialog_ots_15.json" in str(excinfo.value)


# ---- shots -------------------------------------------------------------


def test_cut_and_j_cut_open_new_shots_continuous_does_not():
    t = parse_i2v_template(DIALOG)
    assert shots_of(t) == [[0], [1], [2, 3]]


def test_a_template_with_only_continuous_beats_is_one_shot():
    d = copy.deepcopy(DIALOG)
    for b in d["beats"]:
        b["transition"] = "continuous"
    assert shots_of(parse_i2v_template(d)) == [[0, 1, 2, 3]]


# ---- load ---------------------------------------------------------------


def test_load_reads_every_json_and_requires_id_to_match_the_stem(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    other = _with(id="other_15", name="Other")
    (tmp_path / "other_15.json").write_text(json.dumps(other))

    loaded = load_i2v_templates(tmp_path)

    assert sorted(loaded) == ["dialog_ots_15", "other_15"]
    assert loaded["other_15"].name == "Other"


def test_load_rejects_an_id_that_does_not_match_the_filename(tmp_path):
    (tmp_path / "renamed.json").write_text(json.dumps(DIALOG))
    with pytest.raises(I2vTemplateError) as excinfo:
        load_i2v_templates(tmp_path)
    assert "renamed.json" in str(excinfo.value)
    assert "dialog_ots_15" in str(excinfo.value)


def test_load_names_the_file_on_invalid_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    with pytest.raises(I2vTemplateError) as excinfo:
        load_i2v_templates(tmp_path)
    assert "broken.json" in str(excinfo.value)


def test_load_is_cached_until_reload(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    first = load_i2v_templates(tmp_path)
    (tmp_path / "other_15.json").write_text(json.dumps(_with(id="other_15")))
    assert load_i2v_templates(tmp_path) is first
    reload_i2v_templates()
    assert "other_15" in load_i2v_templates(tmp_path)


def test_get_unknown_template_lists_the_known_ones(tmp_path):
    (tmp_path / "dialog_ots_15.json").write_text(json.dumps(DIALOG))
    with pytest.raises(I2vTemplateError) as excinfo:
        get_i2v_template("nope", tmp_path)
    assert "dialog_ots_15" in str(excinfo.value)


def test_missing_directory_loads_as_empty(tmp_path):
    assert load_i2v_templates(tmp_path / "absent") == {}


# ---- summarize ---------------------------------------------------------


def test_summarize_is_json_safe_and_complete():
    t = parse_i2v_template(DIALOG)
    s = summarize_i2v_template(t)
    json.dumps(s)  # must not raise
    assert s["id"] == "dialog_ots_15"
    assert s["duration_s"] == 15.0
    assert s["roles"] == [
        {"id": "A", "note": "the listener first; speaks second"},
        {"id": "B", "note": "speaks first"},
    ]
    assert s["beats"][2]["transition"] == "j_cut"
    assert s["beats"][2]["camera"] == {
        "shot_size": "MS",
        "angle": "eye",
        "camera_motion": "pull_out",
        "camera_amplitude": None,
        "camera_speed": "slow",
        "lens": None,
    }
    assert s["beats"][0]["cast"] == ["A", "B"]


# ---- shipped library ---------------------------------------------------


def test_the_shipped_library_loads_and_has_the_expected_cadences():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    lib = load_i2v_templates(I2V_TEMPLATES_DIR)
    assert sorted(lib) == ["dialog_ots_15", "intimate_15", "melee_12"]
    assert shots_of(lib["dialog_ots_15"]) == [[0], [1], [2, 3]]
    assert shots_of(lib["melee_12"]) == [[0, 1], [2]]
    assert shots_of(lib["intimate_15"]) == [[0, 1, 2]]
    assert lib["dialog_ots_15"].duration_s == 15.0
    assert lib["melee_12"].duration_s == 12.0
    assert lib["intimate_15"].duration_s == 15.0
    assert [r.id for r in lib["melee_12"].roles] == ["A", "B"]
    assert [r.id for r in lib["intimate_15"].roles] == ["A"]


# ---- grammar / prompt / fill ------------------------------------------

from metascan.core.i2v_compiler import I2vError  # noqa: E402
from metascan.core.i2v_templates import (  # noqa: E402
    build_i2v_template_user_prompt,
    describe_beat,
    i2v_template_grammar,
    i2v_template_max_tokens,
    validate_i2v_template_fill,
)


def _fill(t, **over) -> str:
    """A well-formed VLM response for ``t``."""
    roles = [
        {
            "id": r.id,
            "bound": True,
            "description": f"desc of {r.id}",
            "tag": f"tag {r.id}",
        }
        for r in t.roles
    ]
    beats = []
    for b in t.beats:
        beat = {"action": f"action for {b.start_s}"}
        if b.speaker:
            beat["line"] = f"line by {b.speaker}"
        beats.append(beat)
    data = {
        "roles": roles,
        "beats": beats,
        "overall_soundscape": "room tone",
        "non_diegetic_music": "none",
    }
    data.update(over)
    return json.dumps(data)


def test_grammar_bakes_in_role_ids_and_beat_count():
    t = parse_i2v_template(DIALOG)
    g = i2v_template_grammar(t)
    assert "role0 ::=" in g and "role1 ::=" in g and "role2 ::=" not in g
    assert '"\\"A\\""' in g and '"\\"B\\""' in g
    assert "beat3 ::=" in g and "beat4 ::=" not in g
    assert 'boolean ::= "true" | "false"' in g


def test_grammar_requires_a_line_exactly_where_a_speaker_is_set():
    t = parse_i2v_template(DIALOG)
    rules = dict(
        line.split(" ::= ", 1)
        for line in i2v_template_grammar(t).splitlines()
        if " ::= " in line
    )
    assert '"\\"line\\""' in rules["beat0"]  # B speaks
    assert '"\\"line\\""' not in rules["beat1"]
    assert '"\\"line\\""' in rules["beat2"]  # A speaks
    assert '"\\"line\\""' not in rules["beat3"]


def test_grammar_with_no_roles_is_still_valid():
    d = _with(roles=[])
    for b in d["beats"]:
        b["cast"], b["speaker"] = [], None
    g = i2v_template_grammar(parse_i2v_template(d))
    assert '"\\"roles\\"" ws ":" ws "[" ws  ws "]"' in g


def test_max_tokens_scales_with_beats_and_roles():
    t = parse_i2v_template(DIALOG)
    assert i2v_template_max_tokens(t) > 260 + 90 * 4


def test_describe_beat_reads_like_a_brief():
    t = parse_i2v_template(DIALOG)
    assert describe_beat(t, 0) == (
        "Beat 1 (0.0-4.0 s, medium shot, over the shoulder, camera trucks "
        "right at slow speed; on screen: A, B; B speaks): over A's shoulder "
        "onto B, who is speaking"
    )
    assert describe_beat(t, 1) == (
        "Beat 2 (4.0-5.0 s, CUT to a close-up, eye level, camera holds a static "
        "shot; on screen: B; no line): B's face; a reaction, not a line"
    )
    assert describe_beat(t, 2).startswith("Beat 3 (5.0-10.0 s, J-CUT to a medium shot")
    assert "camera arcs around the subject with small amplitude" in describe_beat(t, 3)


def test_user_prompt_lists_roles_beats_idea_and_soundscape_hint():
    t = parse_i2v_template(DIALOG)
    p = build_i2v_template_user_prompt(t, "two old friends argue about money")
    assert "two old friends argue about money" in p
    assert "15-second video" in p
    assert "Role A: the listener first; speaks second" in p
    assert "Role B: speaks first" in p
    assert describe_beat(t, 0) in p
    assert describe_beat(t, 3) in p
    assert "room ambience appropriate to the setting" in p
    assert "bound" in p  # explains the bound flag
    assert "Beat 1" in p and "exactly what the image depicts" in p


def test_user_prompt_with_empty_idea_says_so():
    t = parse_i2v_template(DIALOG)
    assert "(none -- infer" in build_i2v_template_user_prompt(t, "  ")


def test_validate_fill_round_trips_a_good_response():
    t = parse_i2v_template(DIALOG)
    f = validate_i2v_template_fill(_fill(t), t)
    assert f.roles["A"].bound is True
    assert f.roles["B"].tag == "tag B"
    assert [b.line for b in f.beats] == ["line by B", None, "line by A", None]
    assert f.overall_soundscape == "room tone"


def test_validate_fill_normalises_whitespace_and_defaults_sound():
    t = parse_i2v_template(DIALOG)
    raw = _fill(t, overall_soundscape="  ", non_diegetic_music="")
    f = validate_i2v_template_fill(raw, t)
    assert f.overall_soundscape == "Natural ambient sound consistent with the scene."
    assert f.non_diegetic_music == "No non-diegetic music."


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"beats": [{"action": "x"}]}, "4 beats"),
        ({"roles": []}, "role"),
    ],
)
def test_validate_fill_rejects_structural_mismatch(over, fragment):
    t = parse_i2v_template(DIALOG)
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(_fill(t, **over), t)
    assert fragment in str(excinfo.value)


def test_validate_fill_rejects_a_missing_or_blank_line_where_a_speaker_is_set():
    t = parse_i2v_template(DIALOG)
    data = json.loads(_fill(t))
    data["beats"][0]["line"] = "   "
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(json.dumps(data), t)
    assert "beat 1" in str(excinfo.value)


def test_validate_fill_rejects_a_blank_tag_or_description():
    t = parse_i2v_template(DIALOG)
    data = json.loads(_fill(t))
    data["roles"][1]["tag"] = ""
    with pytest.raises(I2vError) as excinfo:
        validate_i2v_template_fill(json.dumps(data), t)
    assert "B" in str(excinfo.value)


def test_validate_fill_rejects_non_json():
    t = parse_i2v_template(DIALOG)
    with pytest.raises(I2vError):
        validate_i2v_template_fill("{not json", t)


# ---- assembly ---------------------------------------------------------

from metascan.core.i2v_compiler import ALIGNMENT_LINE  # noqa: E402
from metascan.core.i2v_templates import assemble_i2v_template_prompt  # noqa: E402

DIALOG_FILL = {
    "roles": [
        {
            "id": "A",
            "bound": True,
            "description": "a woman in her thirties with dark hair and a red wool coat",
            "tag": "the woman in the red coat",
        },
        {
            "id": "B",
            "bound": True,
            "description": "a man in his forties with grey stubble and a black leather jacket",
            "tag": "the man in the leather jacket",
        },
    ],
    "beats": [
        {
            "action": "He leans across the table and speaks under his breath",
            "line": "I told you, we shouldn't be here",
        },
        {"action": "His jaw tightens and he glances toward the door"},
        {"action": "She sits back and folds her arms", "line": "It's already too late"},
        {"action": "He exhales and nods once"},
    ],
    "overall_soundscape": "low jazz piano, glasses, muffled chatter",
    "non_diegetic_music": "none",
}

EXPECTED_DIALOG = (
    ALIGNMENT_LINE
    + "\n\nintegrated_multimodal_description: [Shot 1] Cinematic, photorealistic "
    "skin textures, fine grain. The subjects, composition, and setting shown in "
    "<Picture 1> are established at 0.00 seconds and keep their appearance, "
    "clothing, colors, and spatial relationships. An over-the-shoulder medium "
    "shot looks past a woman in her thirties with dark hair and a red wool coat "
    "onto a man in his forties with grey stubble and a black leather jacket. "
    "The camera trucks right at slow speed. He leans across the table and "
    "speaks under his breath. The man in the leather jacket (S1) says: "
    "<d>[English] I told you, we shouldn't be here.</d> "
    "[Shot 2] At 00:04.000, the shot cuts to a close-up of the man in the "
    "leather jacket. The camera holds a static shot. His jaw tightens and he "
    "glances toward the door. "
    "[Shot 3] At 00:05.000, the woman in the red coat (S2) says: "
    "<d>[English] It's already too late.</d>, the words carrying over from the "
    "previous shot, as the shot cuts to a medium shot of the woman in the red "
    "coat and the man in the leather jacket. The camera pulls out at slow "
    "speed. She sits back and folds her arms. "
    "At 00:10.000, the camera arcs around the subject with small amplitude. "
    "He exhales and nods once."
    "\n\noverall_soundscape: Low jazz piano, glasses, muffled chatter."
    "\n\nnon_diegetic_music: None."
)


def test_dialog_golden():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, notes = assemble_i2v_template_prompt(t, fill)
    assert text == EXPECTED_DIALOG
    assert notes == []


def test_speaker_ids_follow_first_line_order():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert text.index("(S1)") < text.index("(S2)")
    # Beat 1's line is sentence-initial (capitalised); beat 3's j_cut line
    # continues the "At MM:SS.mmm," stamp (not capitalised) -- see EXPECTED_DIALOG.
    assert "The man in the leather jacket (S1)" in text
    assert "the woman in the red coat (S2)" in text


def test_unbound_role_is_kept_out_of_shot_one_and_enters_later():
    t = parse_i2v_template(DIALOG)
    data = copy.deepcopy(DIALOG_FILL)
    data["roles"][0]["bound"] = False  # A is not in the picture
    fill = validate_i2v_template_fill(json.dumps(data), t)
    text, notes = assemble_i2v_template_prompt(t, fill)

    shot1 = text.split("[Shot 2]")[0]
    assert "red wool coat" not in shot1  # A's description absent from Shot 1
    assert "looks past" not in shot1  # ots collapses when only one is in frame
    assert "A medium shot frames a man in his forties" in shot1
    # A's full description appears at its first rendered mention, in Shot 3.
    shot3 = text.split("[Shot 3]")[1]
    assert (
        "a woman in her thirties with dark hair and a red wool coat (S2) says" in shot3
    )
    assert notes == [
        "role A is not in the picture; the template casts it in the first shot, "
        "so it enters at beat 3 instead"
    ]


def test_unbound_speaker_in_shot_one_speaks_off_screen():
    t = parse_i2v_template(DIALOG)
    data = copy.deepcopy(DIALOG_FILL)
    data["roles"][1]["bound"] = False  # B, who speaks in beat 1, is invented
    fill = validate_i2v_template_fill(json.dumps(data), t)
    text, notes = assemble_i2v_template_prompt(t, fill)
    shot1 = text.split("[Shot 2]")[0]
    assert (
        "Off-screen, a man in his forties with grey stubble and a black leather "
        "jacket (S1) says: <d>[English] I told you, we shouldn't be here.</d>"
    ) in shot1
    assert "A medium shot frames a woman in her thirties" in shot1
    assert notes and "role B" in notes[0]


def test_role_first_seen_in_a_continuous_beat_is_introduced():
    d = copy.deepcopy(DIALOG)
    d["beats"][0]["cast"], d["beats"][0]["speaker"] = ["B"], "B"
    d["beats"][1]["transition"] = "continuous"
    d["beats"][1]["cast"] = ["A", "B"]
    d["beats"][2]["transition"] = "continuous"
    d["beats"][3]["transition"] = "continuous"
    t = parse_i2v_template(d)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert (
        "At 00:04.000, the camera holds a static shot. A woman in her thirties with dark hair and a red wool coat is now in frame."
        in text
    )
    assert "[Shot 2]" not in text


def test_look_is_optional_and_shot_one_opener_is_the_single_take_one():
    from metascan.core.i2v_compiler import _OPENING

    d = _with(look="")
    t = parse_i2v_template(d)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    text, _ = assemble_i2v_template_prompt(t, fill)
    assert "integrated_multimodal_description: " + _OPENING in text


def test_shipped_templates_assemble_without_error():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    for t in load_i2v_templates(I2V_TEMPLATES_DIR).values():
        fill = validate_i2v_template_fill(_fill(t), t)
        text, notes = assemble_i2v_template_prompt(t, fill)
        assert text.startswith(ALIGNMENT_LINE)
        assert notes == []
        # ALIGNMENT_LINE itself cites "(from [Shot 1])", so the full text
        # always has one more "[Shot " match than there are shots.
        assert text.count("[Shot ") == len(shots_of(t)) + 1


def test_melee_and_intimate_shapes():
    from metascan.core.i2v_templates import I2V_TEMPLATES_DIR

    lib = load_i2v_templates(I2V_TEMPLATES_DIR)
    melee = lib["melee_12"]
    text, _ = assemble_i2v_template_prompt(
        melee, validate_i2v_template_fill(_fill(melee), melee)
    )
    assert "[Shot 1] High contrast, sharp reflections, fast kinetic energy. " in text
    assert "A wide shot from a low angle frames desc of A and desc of B." in text
    assert "At 00:03.000, the camera tracks the subject at fast speed." in text
    assert (
        "[Shot 2] At 00:07.000, the shot cuts to a medium shot of tag A and tag B. The camera shakes slightly."
        in text
    )

    intimate = lib["intimate_15"]
    text, _ = assemble_i2v_template_prompt(
        intimate, validate_i2v_template_fill(_fill(intimate), intimate)
    )
    assert (
        "An extreme close-up on a macro lens frames desc of A. The camera tilts up at slow speed."
        in text
    )
    assert "At 00:05.000, the camera pushes in at slow speed." in text
    assert "At 00:10.000, the camera holds a static shot." in text
    assert "[Shot 2]" not in text


# ---- lint --------------------------------------------------------------

from metascan.core.i2v_compiler import lint_i2v_prompt  # noqa: E402
from metascan.core.i2v_templates import lint_against_template  # noqa: E402


def _dialog_text():
    t = parse_i2v_template(DIALOG)
    fill = validate_i2v_template_fill(json.dumps(DIALOG_FILL), t)
    return t, assemble_i2v_template_prompt(t, fill)[0]


def test_generated_text_lints_clean_against_its_template():
    t, text = _dialog_text()
    assert lint_against_template(text, t) == []
    # and the full lint (base + template) adds nothing template-related
    assert [
        w for w in lint_i2v_prompt(text, 15.0, template=t) if "Shot" in w or "beat" in w
    ] == []


def test_lint_flags_a_missing_shot():
    t, text = _dialog_text()
    text = text.replace("[Shot 3] At 00:05.000, ", "")
    issues = lint_against_template(text, t)
    assert any("2 shots" in w and "3" in w for w in issues)


def test_lint_flags_non_contiguous_numbering():
    t, text = _dialog_text()
    text = text.replace("[Shot 3]", "[Shot 4]")
    assert any("contiguous" in w for w in lint_against_template(text, t))


def test_lint_flags_a_timestamp_on_shot_one():
    t, text = _dialog_text()
    text = text.replace("[Shot 1] ", "[Shot 1] At 00:00.000, ")
    assert any(
        "Shot 1" in w and "timestamp" in w for w in lint_against_template(text, t)
    )


def test_lint_flags_cut_times_out_of_order_or_past_the_end():
    t, text = _dialog_text()
    issues = lint_against_template(text.replace("At 00:05.000", "At 00:03.000"), t)
    assert any("increase" in w for w in issues)
    issues = lint_against_template(text.replace("At 00:05.000", "At 00:16.000"), t)
    assert any("15" in w and "duration" in w for w in issues)


def test_lint_flags_a_missing_camera_phrase_in_a_shot():
    t, text = _dialog_text()
    text = text.replace("The camera pulls out at slow speed. ", "")
    issues = lint_against_template(text, t)
    msg = next(w for w in issues if "pulls out" in w)
    assert "Shot 3" in msg
    # The label is a short, well-formed identifier -- not describe_beat's
    # prose truncated at its first colon (which falls mid-sentence, at
    # "; on screen:", leaving an unbalanced paren).
    assert "(Beat 3, 5.0-10.0 s)" in msg
    assert "on screen" not in msg


def test_parenthetical_shot_citation_inside_the_body_still_counts_as_a_shot():
    """A hand edit that wraps a marker in a parenthetical aside, e.g.
    '(as noted, [Shot 2]) At ...', must not be mistaken for
    ALIGNMENT_LINE's own '(from [Shot 1])' citation and dropped entirely
    -- that used to cascade into a wrong shot count, a wrong contiguity
    complaint, a bogus cut-time complaint, and a camera complaint against
    the wrong beat. Scoping the scan to the
    integrated_multimodal_description field (rather than guessing from a
    marker's punctuation) fixes that: the shot is still recognized and
    correctly numbered; the only issue reported is the genuine one -- this
    specific edit broke the marker's immediate 'At MM:SS.mmm,' adjacency."""
    t, text = _dialog_text()
    text = text.replace("[Shot 2] At", "(as noted, [Shot 2]) At")
    issues = lint_against_template(text, t)
    assert issues == ["Shot 2 has no 'At MM:SS.mmm,' cut time"]


def test_lint_flags_a_missing_line_where_a_speaker_is_set():
    t, text = _dialog_text()
    text = text.replace(
        "The man in the leather jacket (S1) says: <d>[English] I told you, we shouldn't be here.</d>",
        "",
    )
    issues = lint_against_template(text, t)
    assert any("Shot 1" in w and "B speaks" in w for w in issues)


def test_lint_flags_a_cut_time_that_moved_from_the_template():
    t, text = _dialog_text()
    text = text.replace("At 00:04.000", "At 00:04.500")
    issues = lint_against_template(text, t)
    assert any("Shot 2" in w and "00:04.000" in w for w in issues)


def test_base_lint_is_unchanged_when_no_template_is_given():
    _, text = _dialog_text()
    assert lint_i2v_prompt(text, 15.0) == lint_i2v_prompt(text, 15.0, template=None)
