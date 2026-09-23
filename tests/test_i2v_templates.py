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
