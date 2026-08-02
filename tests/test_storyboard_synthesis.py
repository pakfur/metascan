"""tests/test_storyboard_synthesis.py"""

from metascan.core.storyboard_synthesis import (
    TAG_STYLE_TARGETS,
    build_render_messages,
    finalize_prompt,
)

BRIEF = "SHOT: close-up\nSUBJECT Maya: red scarf\nACTION: she turns"


def test_tag_style_targets():
    assert TAG_STYLE_TARGETS == frozenset({"sd", "pony"})


def test_tag_style_system_prompt():
    system, user = build_render_messages(BRIEF, "sd")
    assert "comma" in system.lower() and "verbatim" in system.lower()
    assert BRIEF in user


def test_natural_language_system_prompt():
    system, user = build_render_messages(BRIEF, "flux1")
    assert "paragraph" in system.lower() and "verbatim" in system.lower()
    assert BRIEF in user


def test_unknown_target_gets_natural_default():
    s_known, _ = build_render_messages(BRIEF, "qwen")
    s_unknown, _ = build_render_messages(BRIEF, "somefuturemodel")
    assert s_unknown == s_known


def test_finalize_prompt_appends_style():
    assert finalize_prompt(" a cat ", "graphite sketch") == "a cat, graphite sketch"
    assert finalize_prompt("a cat", None) == "a cat"
    assert finalize_prompt("a cat", "  ") == "a cat"
