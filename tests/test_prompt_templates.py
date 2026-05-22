"""Pure-Python tests for the prompt-template composer.

No model invocation — verifies that the system + user prompts returned
by each composer wire the right target builder, expand active extras
into instruction text, respect caption-length, and resolve mutex pairs.
"""

from __future__ import annotations

from metascan.core.prompt_templates import (
    CAPTION_LENGTHS,
    EXTRA_OPTION_LABELS,
    TARGET_PRESETS,
    compose_clean_prompts,
    compose_generate_prompts,
    compose_transform_prompts,
)


# --- Generate -------------------------------------------------------------


def test_generate_sd_emits_booru_framing_and_short_tag_count():
    sys, usr = compose_generate_prompts("sd", "t2i", [], "Short")
    low = sys.lower()
    assert "booru-style tags" in low
    assert "stable diffusion prompt" in low
    assert "use 5-15 tags." in low
    assert "no preamble" in low
    assert usr  # non-empty user prompt


def test_generate_pony_mentions_score_caller_clause():
    sys, _ = compose_generate_prompts("pony", "t2i", [], "Medium")
    assert "Pony Diffusion prompt" in sys
    # Builder explicitly tells the model NOT to emit score/rating tags itself —
    # those come from the caller-side prefix/suffix.
    assert "added by the caller" in sys
    assert "do not emit them yourself" in sys


def test_generate_flux1_uses_natural_language_directive():
    sys, _ = compose_generate_prompts("flux1", "t2i", [], "Medium")
    low = sys.lower()
    assert "flux.1 prompt" in low
    assert "not tags" in low
    # Length instruction text appears verbatim for NL builders.
    assert CAPTION_LENGTHS["Medium"] in sys


def test_generate_flux1_long_appends_long_instruction():
    sys, _ = compose_generate_prompts("flux1", "t2i", [], "Long")
    assert CAPTION_LENGTHS["Long"] in sys


def test_generate_no_extras_omits_dynamic_clauses():
    sys, _ = compose_generate_prompts("flux1", "t2i", [], "Medium")
    # Header always present; option-driven clauses absent when no extras set.
    assert "Include objective details about" not in sys
    assert "watermark" not in sys.lower()


def test_generate_extras_drive_tag_hints_for_sd():
    sys, _ = compose_generate_prompts(
        "sd", "t2i", ["includeLighting", "includeCameraAngle"], "Medium"
    )
    assert "Include tags for: lighting, camera angle." in sys


def test_generate_extras_drive_nl_clauses_for_flux1():
    sys, _ = compose_generate_prompts(
        "flux1", "t2i", ["includeLighting", "includeWatermark"], "Medium"
    )
    assert "precise lighting details" in sys
    assert "watermark" in sys.lower()


def test_generate_qwen_includes_full_text_for_each_extra():
    sys, _ = compose_generate_prompts("qwen", "t2i", ["excludeText"], "Medium")
    full = EXTRA_OPTION_LABELS["excludeText"][1]
    assert full in sys


def test_generate_mutex_keep_pg_wins_over_uncensored():
    sys, _ = compose_generate_prompts(
        "flux1", "t2i", ["keepPG", "includeUncensored"], "Medium"
    )
    # Server-side resolver drops includeUncensored when keepPG also set.
    assert "Keep the description SFW" in sys
    assert "explicit and anatomically correct" not in sys


def test_generate_uncensored_alone_emits_explicit_clause_for_flux1():
    sys, _ = compose_generate_prompts("flux1", "t2i", ["includeUncensored"], "Medium")
    assert "explicit and anatomically correct" in sys
    assert "Keep the description SFW" not in sys


def test_generate_descriptive_length_clamped_for_tag_targets():
    # sd doesn't allow Descriptive (Longest) — server falls back silently.
    sys_descriptive, _ = compose_generate_prompts(
        "sd", "t2i", [], "Descriptive (Longest)"
    )
    sys_medium, _ = compose_generate_prompts("sd", "t2i", [], "Medium")
    assert sys_descriptive == sys_medium


# --- Transform ------------------------------------------------------------


def test_transform_includes_source_and_target_and_extras():
    sys, usr = compose_transform_prompts(
        "an old prompt", "qwen", "t2i", ["includeLighting"], "Medium"
    )
    assert "qwen" in usr
    assert "an old prompt" in usr
    assert "Rewrite the supplied prompt" in sys
    # Extras still drive the per-target builder section.
    assert "Include information about lighting." in sys


def test_transform_preserves_target_specific_framing():
    sys, _ = compose_transform_prompts("old", "pony", "t2i", [], "Medium")
    assert "Pony Diffusion prompt" in sys
    assert "Rewrite" in sys


# --- Clean ----------------------------------------------------------------


def test_clean_returns_terse_system_unchanged():
    sys, usr = compose_clean_prompts("messy,, prompt,, here  ")
    assert "clean" in sys.lower()
    assert "messy,, prompt,, here  " in usr


# --- Presets --------------------------------------------------------------


def test_pony_preset_carries_score_prefix_and_rating_suffix():
    p = TARGET_PRESETS["pony"]
    assert "score_9" in p.prefix
    assert "rating_safe" in p.suffix


def test_sd_preset_caps_at_long_length():
    assert "Descriptive (Longest)" not in TARGET_PRESETS["sd"].allowed_lengths
    assert "Long" in TARGET_PRESETS["sd"].allowed_lengths


def test_natural_language_targets_allow_descriptive_length():
    for tid in ("flux1", "flux2", "zimage", "chroma", "qwen"):
        assert "Descriptive (Longest)" in TARGET_PRESETS[tid].allowed_lengths


def test_every_target_has_a_builder():
    # Composing for every preset must succeed — keeps _BUILDERS in sync with TARGET_PRESETS.
    for tid in TARGET_PRESETS:
        compose_generate_prompts(tid, "t2i", [], "Medium")
