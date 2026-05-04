"""Snapshot + parse tests for VLM prompt templates and grammar."""

import json

from metascan.core.vlm_prompts import (
    CAPTION_STYLE_PROMPTS,
    PROMPT_EXTRACTION_PROMPT,
    TAGGING_GRAMMAR,
    TAGGING_SYSTEM_PROMPT,
    TAGGING_USER_PROMPT,
    normalize_tags,
    parse_tags_response,
)


def test_tagging_system_prompt_mentions_nsfw_honesty():
    assert (
        "nsfw" in TAGGING_SYSTEM_PROMPT.lower()
        or "explicit" in TAGGING_SYSTEM_PROMPT.lower()
    )


def test_tagging_system_prompt_requests_json_array():
    assert "json" in TAGGING_SYSTEM_PROMPT.lower()
    assert (
        "array" in TAGGING_SYSTEM_PROMPT.lower()
        or "list" in TAGGING_SYSTEM_PROMPT.lower()
    )


def test_tagging_user_prompt_is_short():
    assert len(TAGGING_USER_PROMPT) < 200


def test_tagging_grammar_is_valid_gbnf():
    assert "::=" in TAGGING_GRAMMAR
    assert "string" in TAGGING_GRAMMAR or "tag" in TAGGING_GRAMMAR


def test_caption_style_prompts_have_all_four_styles():
    assert set(CAPTION_STYLE_PROMPTS.keys()) == {"sdxl", "flux", "pony", "natural"}


def test_prompt_extraction_prompt_present():
    assert isinstance(PROMPT_EXTRACTION_PROMPT, str)
    assert len(PROMPT_EXTRACTION_PROMPT) > 20


def test_parse_tags_response_extracts_array():
    raw = '["red dress", "outdoor", "smiling"]'
    assert parse_tags_response(raw) == ["red dress", "outdoor", "smiling"]


def test_parse_tags_response_handles_whitespace_and_dedup():
    raw = '[" Red Dress ", "outdoor", "Outdoor", "smiling"]'
    assert parse_tags_response(raw) == ["red dress", "outdoor", "smiling"]


def test_parse_tags_response_returns_empty_on_garbage():
    assert parse_tags_response("not json at all") == []
    assert parse_tags_response("") == []


def test_parse_tags_response_handles_object_with_tags_key():
    raw = json.dumps({"tags": ["a", "b"]})
    assert parse_tags_response(raw) == ["a", "b"]


def test_normalize_tags_lowercases_strips_dedups():
    assert normalize_tags([" Red ", "blue", "RED", "  "]) == ["red", "blue"]


def test_normalize_tags_strips_punctuation_edges():
    assert normalize_tags(["red.", "!blue", "(green)"]) == ["red", "blue", "green"]
