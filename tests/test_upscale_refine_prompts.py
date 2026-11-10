"""Refine prompts for the Upscale dialog: the shipped YAML, the store's
fallbacks and reloads, and the subject guess."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import yaml

from backend.config import get_upscale_config
from metascan.core.upscale_refine_prompts import (
    BUILT_IN,
    FILENAME,
    RefinePromptStore,
    guess_refine_prompt,
    guess_subject,
    parse_refine_prompts,
)

SHIPPED = Path(__file__).resolve().parent.parent / "data" / FILENAME


def test_the_shipped_file_matches_the_built_in_prompts():
    parsed = parse_refine_prompts(yaml.safe_load(SHIPPED.read_text(encoding="utf-8")))
    assert parsed == list(BUILT_IN)


def test_no_prompt_has_parentheses():
    # ComfyUI reads parentheses as weights.
    for p in BUILT_IN:
        assert not set("()") & set(p.prompt + p.negative), p.id


def test_a_missing_file_gives_the_built_in_prompts(tmp_path):
    assert RefinePromptStore(tmp_path / FILENAME).prompts() == BUILT_IN


def _write(path: Path, text: str, bump: int = 0) -> None:
    path.write_text(text, encoding="utf-8")
    # Two writes inside one timestamp tick still read as a change.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


def test_the_file_replaces_them_and_is_read_again_when_it_changes(tmp_path):
    path = tmp_path / FILENAME
    _write(
        path,
        "prompts:\n  - id: a\n    label: A\n    prompt: 'one\n      two'\n"
        "    for: people\n",
    )
    store = RefinePromptStore(path)
    (first,) = store.prompts()
    assert (first.id, first.prompt, first.negative, first.subject) == (
        "a",
        "one two",
        "",
        "people",
    )
    _write(path, "prompts:\n  - {id: b, label: B, prompt: x}\n", bump=5)
    assert [p.id for p in store.prompts()] == ["b"]
    path.unlink()
    assert store.prompts() == BUILT_IN


@pytest.mark.parametrize(
    "text",
    [
        "prompts: [",  # not YAML
        "- just a list",  # wrong shape
        "prompts:\n  - {id: a, label: A}\n",  # no valid entry
    ],
)
def test_an_unusable_file_gives_the_built_in_prompts(tmp_path, text, caplog):
    path = tmp_path / FILENAME
    _write(path, text)
    with caplog.at_level(logging.WARNING):
        assert RefinePromptStore(path).prompts() == BUILT_IN
    assert "built-in" in caplog.text


def test_bad_entries_are_skipped_and_the_rest_kept(caplog):
    data = {
        "prompts": [
            "not a mapping",
            {"id": "a", "label": "A", "prompt": "p", "for": "cats"},
            {"id": "a", "label": "again", "prompt": "q"},
            {"id": "b", "label": "", "prompt": "r"},
            {"id": "c", "label": "C", "prompt": "s", "negative": 3},
        ]
    }
    with caplog.at_level(logging.WARNING):
        got = parse_refine_prompts(data)
    assert [(p.id, p.subject, p.negative) for p in got] == [
        ("a", None, ""),
        ("c", None, ""),
    ]


@pytest.mark.parametrize(
    "tags, subject",
    [
        ([], "scene"),
        ([("lake", "clip"), ("dusk", "prompt")], "scene"),
        ([("woman", "prompt")], "people"),
        ([("portrait", "vlm")], "people"),
        ([("woman", "vlm"), ("anime", "vlm")], "illustration"),
        ([("digital art", "vlm+prompt")], "illustration"),
        # A prompt word names what is in the picture as often as its medium.
        ([("woman", "prompt"), ("painting", "prompt")], "people"),
        ([("Woman ", "clip")], "people"),
    ],
)
def test_the_subject_guess(tags, subject):
    assert guess_subject(tags) == subject


def test_the_guess_falls_back_to_the_first_prompt():
    assert guess_refine_prompt([("woman", "clip")], BUILT_IN) == "photo_people"
    no_people = [p for p in BUILT_IN if p.subject != "people"]
    assert guess_refine_prompt([("woman", "clip")], no_people) == "photo_scene"
    assert guess_refine_prompt([], []) is None


@pytest.mark.parametrize(
    "raw, want", [(None, 0.3), (0.4, 0.4), (0, 0.0), (1.5, 0.3), (True, 0.3)]
)
def test_the_denoise_limit_setting(raw, want):
    cfg = {"upscale": {} if raw is None else {"refine_prompt_max_denoise": raw}}
    assert get_upscale_config(cfg)["refine_prompt_max_denoise"] == want
