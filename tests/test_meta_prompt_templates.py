"""Pure-Python tests for the Qwen3-VL meta-prompt composer + parser.

These complement (and largely supplant) ``test_prompt_templates.py``.
The legacy module is still exercised by the older test file because
the API's ``transform`` / ``clean`` paths still call into it.
"""

from __future__ import annotations

import pytest

from metascan.core.meta_prompt_templates import (
    EXTRA_OPTION_LABELS,
    MODELS_WITH_NEGATIVE,
    MUTEX_PAIRS,
    TARGET_PRESETS,
    compose_generate_prompts,
    parse_output,
)


# --- Composer -------------------------------------------------------------


def test_every_target_has_a_meta_prompt():
    """``compose_generate_prompts`` must succeed for every preset id."""
    for tid in TARGET_PRESETS:
        sys, usr = compose_generate_prompts(tid, "t2i", [])
        assert sys.strip()
        assert usr.strip()


def test_user_prompt_is_short_and_image_directed():
    _, usr = compose_generate_prompts("flux1", "t2i", [])
    assert "analyze the attached image" in usr.lower()
    # Keep the user prompt short — the heavy lifting belongs in system.
    assert len(usr) < 200


def test_flux1_meta_prompt_is_prose_oriented():
    sys, _ = compose_generate_prompts("flux1", "t2i", [])
    low = sys.lower()
    assert "flux.1" in low
    # Defensive add-on the doc author baked in for Qwen3 8B drift.
    assert "sentences only" in low or "sentence" in low


def test_flux2_meta_prompt_has_hex_guidance_and_phrase_directive():
    sys, _ = compose_generate_prompts("flux2", "t2i", [])
    assert "HEX" in sys
    assert "comma-separated phrases" in sys.lower()
    # Defensive add-on: phrase-only output for Qwen3 8B.
    assert "Phrases separated by commas" in sys


def test_zimage_meta_prompt_requires_camera_anchors_for_humans():
    sys, _ = compose_generate_prompts("zimage", "t2i", [])
    assert "real camera body" in sys
    assert "non-idealized facial feature" in sys


def test_chroma_meta_prompt_caps_negative_at_twelve():
    sys, _ = compose_generate_prompts("chroma", "t2i", [])
    assert "12 terms" in sys


def test_qwen_meta_prompt_emphasises_brevity_and_text_quoting():
    sys, _ = compose_generate_prompts("qwen", "t2i", [])
    assert "BREVITY WINS" in sys
    assert "double quotes" in sys.lower()


def test_sdxl_meta_prompt_caps_negatives():
    sys, _ = compose_generate_prompts("sd", "t2i", [])
    assert "Cap at 25 terms" in sys


def test_pony_meta_prompt_includes_full_score_stack():
    sys, _ = compose_generate_prompts("pony", "t2i", [])
    assert "score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up" in sys
    # Defensive add-on: forces booru tag form on the 8B.
    assert "Danbooru/e621 booru tag conventions" in sys


# --- Extras: includeSafety / includeUncensored ----------------------------


def test_no_extras_omits_content_directives():
    sys, _ = compose_generate_prompts("flux1", "t2i", [])
    assert "Content constraint" not in sys


def test_include_safety_appends_sfw_directive():
    sys, _ = compose_generate_prompts("flux1", "t2i", ["includeSafety"])
    assert "Content constraint" in sys
    assert "fully SFW" in sys
    low = sys.lower()
    assert "nudity" in low and "sexual" in low


def test_include_uncensored_appends_explicit_directive():
    sys, _ = compose_generate_prompts("flux1", "t2i", ["includeUncensored"])
    assert "Content constraint" in sys
    assert "anatomically-correct" in sys
    # Make sure we didn't accidentally include the SFW directive too.
    assert "fully SFW" not in sys


def test_uncensored_directive_mentions_pony_rating_explicit():
    sys, _ = compose_generate_prompts("pony", "t2i", ["includeUncensored"])
    assert "rating_explicit" in sys


def test_safety_directive_mentions_negative_block_handling():
    """Safety directive instructs Qwen3 to add nsfw/nudity to the negative
    block where one is produced — meaningful for sd/pony/chroma/qwen."""
    sys, _ = compose_generate_prompts("sd", "t2i", ["includeSafety"])
    assert "nsfw" in sys.lower()


def test_safety_and_uncensored_mutex_resolves_to_safety():
    """Both flags arriving simultaneously: server picks the safer one."""
    sys, _ = compose_generate_prompts(
        "flux1", "t2i", ["includeSafety", "includeUncensored"]
    )
    assert "fully SFW" in sys
    assert "anatomically-correct" not in sys


def test_mutex_resolution_is_order_independent():
    """Whichever is first in the list, the safe option wins."""
    sys_a, _ = compose_generate_prompts(
        "flux1", "t2i", ["includeUncensored", "includeSafety"]
    )
    sys_b, _ = compose_generate_prompts(
        "flux1", "t2i", ["includeSafety", "includeUncensored"]
    )
    assert "fully SFW" in sys_a
    assert "fully SFW" in sys_b


# --- Presets --------------------------------------------------------------


def test_models_with_negative_are_marked_in_presets():
    for tid in MODELS_WITH_NEGATIVE:
        assert TARGET_PRESETS[tid].has_negative is True
    for tid in TARGET_PRESETS:
        if tid not in MODELS_WITH_NEGATIVE:
            assert TARGET_PRESETS[tid].has_negative is False


def test_pony_preset_no_longer_emits_score_prefix():
    """Score tags are emitted INSIDE the meta-prompt's positive output;
    the user-side prefix is empty so prefix+output doesn't double them up."""
    p = TARGET_PRESETS["pony"]
    assert p.prefix == ""
    assert "score_" not in p.suffix


def test_extra_option_labels_match_extra_option_literal_keys():
    """Every option in MUTEX_PAIRS must also appear in EXTRA_OPTION_LABELS."""
    for a, b in MUTEX_PAIRS:
        assert a in EXTRA_OPTION_LABELS
        assert b in EXTRA_OPTION_LABELS


# --- Output parser --------------------------------------------------------


@pytest.mark.parametrize("target", ["flux1", "flux2", "zimage"])
def test_parse_output_no_negative_for_prose_only_targets(target):
    raw = "A young woman reading a book by a window. Soft afternoon light."
    pos, neg = parse_output(target, raw)
    assert pos == raw
    assert neg is None


def test_parse_output_strips_surrounding_whitespace():
    raw = "\n\n  positive text  \n\n"
    pos, neg = parse_output("flux1", raw)
    assert pos == "positive text"
    assert neg is None


def test_parse_output_splits_negative_block_for_sd():
    raw = (
        "masterpiece, best quality, a young woman in a red sweater, sitting in a cafe.\n"
        "\n"
        "Negative: low quality, blurry, deformed, extra fingers"
    )
    pos, neg = parse_output("sd", raw)
    assert "young woman" in pos
    assert "Negative:" not in pos
    assert neg is not None
    assert "low quality" in neg
    assert "extra fingers" in neg


def test_parse_output_splits_negative_block_for_pony():
    raw = (
        "score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up,\n"
        "source_anime, rating_safe, 1girl, solo, long_hair\n"
        "\n"
        "Negative: score_6, score_5, worst quality, low quality, nsfw"
    )
    pos, neg = parse_output("pony", raw)
    assert "1girl" in pos
    assert neg is not None
    assert "score_6" in neg


def test_parse_output_handles_negative_prompt_label_variant():
    """Some 8B runs emit 'Negative prompt:' instead of 'Negative:'."""
    raw = "positive line\n\nNegative prompt: foo, bar, baz"
    pos, neg = parse_output("chroma", raw)
    assert pos == "positive line"
    assert neg == "foo, bar, baz"


def test_parse_output_tolerates_markdown_emphasis_on_negative_label():
    raw = "positive\n\n**Negative:** alpha, beta"
    _, neg = parse_output("qwen", raw)
    assert neg == "alpha, beta"


def test_parse_output_strips_block_labels_when_present():
    raw = "Block 1: positive content here\n" "\n" "Block 2: Negative: x, y, z"
    pos, neg = parse_output("sd", raw)
    assert "Block 1" not in pos
    assert "positive content here" in pos
    assert neg == "x, y, z"


def test_parse_output_strips_code_fence():
    raw = "```\npositive content\n\nNegative: a, b\n```"
    pos, neg = parse_output("sd", raw)
    assert pos == "positive content"
    assert neg == "a, b"


def test_parse_output_handles_negative_with_inline_and_continuation():
    """Some responses break the negative across multiple lines."""
    raw = (
        "positive prompt body\n"
        "\n"
        "Negative: first batch of terms,\n"
        "second batch of terms"
    )
    _, neg = parse_output("sd", raw)
    assert neg is not None
    assert "first batch" in neg
    assert "second batch" in neg


def test_parse_output_returns_none_negative_when_block_missing():
    """Negative-supporting target but the model failed to emit a Negative
    block — the entire response is treated as the positive prompt."""
    raw = "just a positive prompt with no negative section at all"
    pos, neg = parse_output("sd", raw)
    assert pos == raw
    assert neg is None


def test_parse_output_returns_empty_negative_as_none():
    """Model emitted "Negative:" but nothing after it — return None, not ''."""
    raw = "positive\n\nNegative:"
    pos, neg = parse_output("sd", raw)
    assert pos == "positive"
    assert neg is None
