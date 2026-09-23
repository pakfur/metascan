"""Tests for metascan/core/i2v_form.py -- the editable per-clip form state.

An i2v_videos row carries two sets of values. The as-rendered columns
(prompt_used, seed, duration_s, quality, steps, width, height, megapixels,
loras) are facts about the clip and never change. ``form_state`` is the
editable copy the dialog loads on click and autosaves into.
"""

from __future__ import annotations

import pytest

from metascan.core.i2v_form import (
    FORM_FIELDS,
    FormStateError,
    build_form_state,
    form_state_for_row,
    validate_form_patch,
)

MEGAPIXEL_OPTIONS = [0.25, 0.5, 0.75, 1.0]


def test_build_form_state_holds_exactly_the_form_fields():
    state = build_form_state(
        idea="a cat stretches",
        prompt="[Shot 1] ...",
        duration_s=10,
        quality="quality",
        megapixels=0.75,
        steps=30,
        seed=42,
        loras=[{"name": "a.safetensors", "strength": 0.8}],
    )
    assert set(state) == set(FORM_FIELDS)
    assert state["duration_s"] == 10.0
    assert state["loras"] == [{"name": "a.safetensors", "strength": 0.8}]


def test_build_form_state_normalises_missing_values():
    state = build_form_state(
        idea=None,
        prompt="p",
        duration_s=6,
        quality="fast",
        megapixels=0.5,
        steps=None,
        seed=1,
        loras=None,
    )
    assert state["idea"] == ""
    assert state["steps"] is None  # Fast renders send no step count
    assert state["loras"] == []


def test_a_stored_form_state_is_returned_as_is():
    stored = build_form_state(
        idea="edited idea",
        prompt="edited",
        duration_s=15,
        quality="fast",
        megapixels=1.0,
        steps=None,
        seed=9,
        loras=[],
    )
    row = {"form_state": stored, "prompt_used": "as rendered", "seed": 1}
    assert form_state_for_row(row, MEGAPIXEL_OPTIONS) == stored


def test_a_stored_form_state_missing_newer_fields_is_completed_from_the_row():
    # A field added to the form later must not break older stored states.
    row = {
        "form_state": {"idea": "x", "prompt": "edited"},
        "prompt_used": "as rendered",
        "seed": 7,
        "duration_s": 6.0,
        "quality": "fast",
    }
    state = form_state_for_row(row, MEGAPIXEL_OPTIONS)
    assert set(state) == set(FORM_FIELDS)
    assert state["prompt"] == "edited"  # the stored value wins
    assert state["seed"] == 7  # the gap is filled from the facts


def test_a_legacy_row_gets_a_form_state_built_from_its_rendered_facts():
    row = {
        "form_state": None,
        "idea": "a dog runs",
        "prompt_used": "[Shot 1] the dog",
        "seed": 1411732318,
        "duration_s": 10.0,
        "quality": "quality",
        "steps": 25,
        "width": 640,
        "height": 1184,
        "megapixels": None,
        "loras": None,
    }
    assert form_state_for_row(row, MEGAPIXEL_OPTIONS) == {
        "idea": "a dog runs",
        "prompt": "[Shot 1] the dog",
        "duration_s": 10.0,
        "quality": "quality",
        "megapixels": 0.75,  # 640x1184 = 0.758 MP, snapped to the option
        "steps": 25,
        "seed": 1411732318,
        "loras": [],
    }


def test_legacy_megapixels_prefers_the_stored_setting_over_the_pixel_count():
    row = {"form_state": None, "width": 640, "height": 1184, "megapixels": 0.5}
    assert form_state_for_row(row, MEGAPIXEL_OPTIONS)["megapixels"] == 0.5


def test_legacy_row_without_dimensions_has_no_megapixels():
    row = {"form_state": None, "width": None, "height": None}
    assert form_state_for_row(row, MEGAPIXEL_OPTIONS)["megapixels"] is None


def test_legacy_row_restart_blanks_are_tolerated():
    # quality/idea were in-memory only; a row ingested after a restart has
    # neither. The form must still load.
    state = form_state_for_row({"form_state": None, "seed": 3}, MEGAPIXEL_OPTIONS)
    assert state["idea"] == ""
    assert state["quality"] is None
    assert state["seed"] == 3


def test_validate_accepts_a_partial_patch_and_normalises_it():
    assert validate_form_patch({"idea": "new", "duration_s": 15}) == {
        "idea": "new",
        "duration_s": 15.0,
    }


def test_validate_accepts_clearing_steps():
    assert validate_form_patch({"steps": None}) == {"steps": None}


def test_validate_cleans_loras():
    patch = validate_form_patch(
        {"loras": [{"name": "a.safetensors", "strength": "0.5", "junk": 1}]}
    )
    assert patch == {"loras": [{"name": "a.safetensors", "strength": 0.5}]}


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"nope": 1}, "nope"),
        ({"idea": 5}, "idea"),
        ({"prompt": None}, "prompt"),
        ({"duration_s": 0}, "duration_s"),
        ({"duration_s": "long"}, "duration_s"),
        ({"quality": "ultra"}, "quality"),
        ({"megapixels": -1}, "megapixels"),
        ({"steps": 0}, "steps"),
        ({"steps": 2.5}, "steps"),
        ({"seed": "abc"}, "seed"),
        ({"seed": True}, "seed"),
        ({"loras": "a.safetensors"}, "loras"),
        ({"loras": [{"strength": 1}]}, "loras"),
        ({"loras": [{"name": "a", "strength": "x"}]}, "loras"),
    ],
)
def test_validate_rejects_bad_values_naming_the_field(body, fragment):
    with pytest.raises(FormStateError) as excinfo:
        validate_form_patch(body)
    assert fragment in str(excinfo.value)


def test_validate_rejects_an_empty_patch():
    with pytest.raises(FormStateError):
        validate_form_patch({})
