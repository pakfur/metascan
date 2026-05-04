"""Pure-Python tests for the prompt-template composer.

No model invocation — just verifies that the system + user prompts
returned by each composer mention the right model, contain (or omit)
the style clause as appropriate, and reject invalid input.
"""

from __future__ import annotations

import pytest

from metascan.core.prompt_templates import (
    STYLE_PHRASES,
    TARGET_MODEL_GUIDANCE,
    compose_clean_prompts,
    compose_generate_prompts,
    compose_transform_prompts,
)


def test_generate_includes_target_model_and_format():
    sys, usr = compose_generate_prompts("sdxl", "t2i", [])
    assert "sdxl" in sys.lower()
    assert "t2i" in sys.lower()
    assert TARGET_MODEL_GUIDANCE["sdxl"] in sys
    assert "no preamble" in sys.lower()
    assert usr  # non-empty


def test_generate_no_styles_has_no_style_clause():
    sys, _ = compose_generate_prompts("flux-chroma", "t2i", [])
    assert "stylistic directions" not in sys.lower()


def test_generate_with_styles_concatenates_phrases():
    sys, _ = compose_generate_prompts("pony", "t2i", ["anime", "cinematic"])
    assert STYLE_PHRASES["anime"] in sys
    assert STYLE_PHRASES["cinematic"] in sys
    assert "stylistic directions" in sys.lower()


def test_generate_rejects_more_than_three_styles():
    with pytest.raises(ValueError, match="3"):
        compose_generate_prompts(
            "sdxl",
            "t2i",
            ["anime", "cinematic", "watercolor", "comic"],
        )


def test_transform_includes_source_and_target():
    sys, usr = compose_transform_prompts("an old prompt", "qwen-t2i", "t2i")
    assert "qwen-t2i" in sys.lower()
    assert "an old prompt" in usr
    assert "rewrite" in sys.lower()


def test_clean_returns_terse_system():
    sys, usr = compose_clean_prompts("messy,, prompt,, here  ")
    assert "clean" in sys.lower()
    assert "messy,, prompt,, here  " in usr
