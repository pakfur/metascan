"""Tests for t2i prompt generation (spec section 5).

Every model output below is hand-written with a known expected split; nothing
comes from the prompt library or the real caption CSV.
"""

from __future__ import annotations

import re
import unittest
from typing import Optional

from metascan.core.meta_prompt_templates import parse_output, split_negative_block
from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_models import MODEL_PROFILES, T2iModelProfile
from metascan.core.t2i_prompt import (
    CONTENT_MODES,
    compose_t2i_prompts,
    fallback_prompt,
    parse_t2i_output,
    strip_parentheses,
)

NEW_KEYS = (
    "META_KREA2",
    "T2I_CAPTION_PREAMBLE",
    "T2I_FALLBACK_PREFIX_SD",
    "T2I_FALLBACK_NEGATIVE_SD",
    "T2I_FALLBACK_NEGATIVE_QWEN",
)
RESOLVED = (
    "A 31-year-old West African woman with olive skin, brown eyes and an athletic "
    "build sits on a wooden bench in a sunlit garden, her shoulder-length wavy "
    "copper red hair moving in the breeze."
)
DIRECTIVE_KEYS = {
    "default": None,
    "sfw": "SAFETY_DIRECTIVE",
    "uncensored": "UNCENSORED_DIRECTIVE",
}


def prompt(key: str) -> str:
    return get_prompt_store().get(key)


def reference_negative(meta_key: str) -> str:
    """The ``Negative: ...`` reference example a guideline ends with."""
    found = re.findall(r"^Negative: (.+)$", prompt(meta_key), flags=re.MULTILINE)
    return found[-1]


class SplitNegativeBlockTests(unittest.TestCase):
    def test_two_blocks_separated_by_a_blank_line(self) -> None:
        raw = (
            "A woman in a red sweater sits in a cafe, warm window light, 85mm lens.\n"
            "\n"
            "Negative: low quality, blurry, extra fingers"
        )
        self.assertEqual(
            split_negative_block(raw),
            (
                "A woman in a red sweater sits in a cafe, warm window light, 85mm lens.",
                "low quality, blurry, extra fingers",
            ),
        )

    def test_negative_prompt_label_variant(self) -> None:
        self.assertEqual(
            split_negative_block("positive line\n\nNegative prompt: foo, bar, baz"),
            ("positive line", "foo, bar, baz"),
        )

    def test_markdown_emphasis_around_the_label(self) -> None:
        self.assertEqual(
            split_negative_block("positive\n\n**Negative:** alpha, beta"),
            ("positive", "alpha, beta"),
        )
        self.assertEqual(
            split_negative_block("positive\n\n**Negative: alpha, beta**"),
            ("positive", "alpha, beta"),
        )

    def test_negative_split_across_lines_is_joined(self) -> None:
        raw = "positive body\n\nNegative: first terms,\nsecond terms"
        self.assertEqual(
            split_negative_block(raw), ("positive body", "first terms,\nsecond terms")
        )

    def test_negative_that_starts_on_the_line_after_the_label(self) -> None:
        raw = "positive body\n\nNegative:\nlow quality, blurry"
        self.assertEqual(
            split_negative_block(raw), ("positive body", "low quality, blurry")
        )

    def test_no_negative_block_returns_the_cleaned_text_and_none(self) -> None:
        self.assertEqual(
            split_negative_block("\n\n  just a prompt with no negative  \n"),
            ("just a prompt with no negative", None),
        )

    def test_empty_negative_is_none_not_an_empty_string(self) -> None:
        self.assertEqual(
            split_negative_block("positive\n\nNegative:"), ("positive", None)
        )

    def test_the_word_negative_inside_a_sentence_is_not_a_block(self) -> None:
        raw = "The sign reads Negative: keep out, above a red door."
        self.assertEqual(split_negative_block(raw), (raw, None))

    def test_code_fence_json_header_and_block_labels_are_cleaned_first(self) -> None:
        self.assertEqual(
            split_negative_block("```\npositive content\n\nNegative: a, b\n```"),
            ("positive content", "a, b"),
        )
        self.assertEqual(
            split_negative_block(
                '{"extracted":[1],"overridden":[],"auto":[]}\n\npositive\n\nNegative: x'
            ),
            ("positive", "x"),
        )
        self.assertEqual(
            split_negative_block(
                "Block 1: positive content here\n\nBlock 2: Negative: x, y"
            ),
            ("positive content here", "x, y"),
        )

    def test_empty_input(self) -> None:
        self.assertEqual(split_negative_block(""), ("", None))
        self.assertEqual(split_negative_block("   \n  "), ("", None))

    def test_parse_output_is_split_negative_block_for_negative_targets(self) -> None:
        raw = "score_9, 1girl, solo\n\nNegative: score_6, worst quality"
        for target in ("sd", "pony", "chroma", "qwen"):
            with self.subTest(target=target):
                self.assertEqual(parse_output(target, raw), split_negative_block(raw))

    def test_parse_output_still_leaves_prose_targets_alone(self) -> None:
        raw = "A calm harbour at dawn.\n\nNegative: not a block for this target"
        for target in ("flux1", "flux2", "zimage"):
            with self.subTest(target=target):
                positive, negative = parse_output(target, raw)
                self.assertEqual(positive, raw)
                self.assertIsNone(negative)


class ComposeTests(unittest.TestCase):
    def test_content_modes(self) -> None:
        self.assertEqual(CONTENT_MODES, ("uncensored", "sfw", "default"))

    def test_system_is_preamble_then_guideline_then_directive(self) -> None:
        preamble = prompt("T2I_CAPTION_PREAMBLE").rstrip()
        for profile in MODEL_PROFILES.values():
            for mode, key in DIRECTIVE_KEYS.items():
                with self.subTest(model=profile.id, mode=mode):
                    system, _ = compose_t2i_prompts(profile, RESOLVED, mode)
                    expected = preamble + "\n\n" + prompt(profile.meta_key)
                    if key is not None:
                        expected += prompt(key)
                    self.assertEqual(system, expected)

    def test_parts_appear_in_order(self) -> None:
        preamble = prompt("T2I_CAPTION_PREAMBLE").strip()
        for profile in MODEL_PROFILES.values():
            with self.subTest(model=profile.id):
                system, _ = compose_t2i_prompts(profile, RESOLVED, "sfw")
                guideline = prompt(profile.meta_key).strip()
                directive = prompt("SAFETY_DIRECTIVE").strip()
                self.assertTrue(system.startswith(preamble))
                self.assertLess(system.index(preamble), system.index(guideline))
                self.assertLess(system.index(guideline), system.index(directive))
                self.assertTrue(system.endswith(directive))

    def test_default_mode_adds_no_directive(self) -> None:
        for profile in MODEL_PROFILES.values():
            system, _ = compose_t2i_prompts(profile, RESOLVED, "default")
            self.assertNotIn("Content constraint", system)
            self.assertTrue(system.endswith(prompt(profile.meta_key)))

    def test_sfw_and_uncensored_add_the_existing_directives(self) -> None:
        profile = MODEL_PROFILES["krea2"]
        sfw, _ = compose_t2i_prompts(profile, RESOLVED, "sfw")
        explicit, _ = compose_t2i_prompts(profile, RESOLVED, "uncensored")
        self.assertIn("fully SFW", sfw)
        self.assertNotIn("anatomically-correct", sfw)
        self.assertIn("anatomically-correct", explicit)
        self.assertNotIn("fully SFW", explicit)

    def test_unknown_content_mode_is_rejected(self) -> None:
        for bad in ("", "SFW", "explicit", "none"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    compose_t2i_prompts(MODEL_PROFILES["sd"], RESOLVED, bad)
                self.assertIn("uncensored", str(ctx.exception))

    def test_user_turn_format_is_exact_for_every_model_and_mode(self) -> None:
        for profile in MODEL_PROFILES.values():
            for mode in CONTENT_MODES:
                with self.subTest(model=profile.id, mode=mode):
                    _, user = compose_t2i_prompts(profile, RESOLVED, mode)
                    self.assertEqual(
                        user, "DESCRIPTION:\n" + RESOLVED + "\n\nWrite the prompt now."
                    )

    def test_the_caption_is_carried_verbatim(self) -> None:
        caption = "Line one.\n\nLine two, with  odd   spacing.  "
        _, user = compose_t2i_prompts(MODEL_PROFILES["qwen"], caption, "default")
        self.assertEqual(user, f"DESCRIPTION:\n{caption}\n\nWrite the prompt now.")

    def test_each_model_gets_only_its_own_guideline(self) -> None:
        systems = {
            profile.id: compose_t2i_prompts(profile, RESOLVED, "default")[0]
            for profile in MODEL_PROFILES.values()
        }
        for profile in MODEL_PROFILES.values():
            for other in MODEL_PROFILES.values():
                present = prompt(other.meta_key).strip() in systems[profile.id]
                self.assertEqual(present, profile is other, (profile.id, other.id))


class TemplateTextTests(unittest.TestCase):
    """Structural checks only: the wording is meant to be tuned in the YAML."""

    def test_new_keys_exist_and_are_not_empty(self) -> None:
        for key in NEW_KEYS:
            with self.subTest(key=key):
                self.assertTrue(prompt(key).strip())

    def test_every_profile_guideline_exists(self) -> None:
        for profile in MODEL_PROFILES.values():
            with self.subTest(model=profile.id):
                self.assertTrue(prompt(profile.meta_key).strip())

    def test_no_new_template_contains_a_parenthesis(self) -> None:
        for key in NEW_KEYS:
            with self.subTest(key=key):
                self.assertNotIn("(", prompt(key))
                self.assertNotIn(")", prompt(key))

    def test_composed_krea2_prompts_carry_no_parenthesis_of_our_own(self) -> None:
        # The existing content directives open with a parenthesised header of
        # their own; everything else in the composed prompts is t2i text.
        for mode, key in DIRECTIVE_KEYS.items():
            with self.subTest(mode=mode):
                system, user = compose_t2i_prompts(
                    MODEL_PROFILES["krea2"], "A person by a window.", mode
                )
                if key is not None:
                    system = system.replace(prompt(key), "")
                for text in (system, user):
                    self.assertNotIn("(", text)
                    self.assertNotIn(")", text)

    def test_the_preamble_covers_the_spec_points(self) -> None:
        text = prompt("T2I_CAPTION_PREAMBLE")
        lowered = text.lower()
        self.assertIn("DESCRIPTION", text)
        self.assertIn("no image", lowered)
        self.assertIn("ground truth", lowered)
        self.assertIn("word for word", lowered)
        self.assertIn("format", lowered)
        self.assertIn("parenthes", lowered)

    def test_krea2_guideline_is_prose_and_has_no_negative_block(self) -> None:
        text = prompt("META_KREA2")
        self.assertIn("Krea 2", text)
        self.assertIn("prose", text.lower())
        self.assertNotIn("Negative:", text)

    def test_fallback_negatives_are_the_guidelines_reference_negatives(self) -> None:
        self.assertEqual(
            prompt("T2I_FALLBACK_NEGATIVE_SD").strip(), reference_negative("META_SDXL")
        )
        self.assertEqual(
            prompt("T2I_FALLBACK_NEGATIVE_QWEN").strip(),
            reference_negative("META_QWEN"),
        )

    def test_fallback_prefix_is_the_sdxl_quality_opener(self) -> None:
        self.assertEqual(
            prompt("T2I_FALLBACK_PREFIX_SD").strip(),
            "masterpiece, best quality, highly detailed, sharp focus",
        )


class FallbackTests(unittest.TestCase):
    def expected(self, profile_id: str) -> tuple[str, Optional[str]]:
        caption = RESOLVED
        if profile_id == "sd":
            caption = f"{prompt('T2I_FALLBACK_PREFIX_SD').strip()}, {RESOLVED}"
        negatives = {
            "sd": prompt("T2I_FALLBACK_NEGATIVE_SD").strip(),
            "qwen": prompt("T2I_FALLBACK_NEGATIVE_QWEN").strip(),
        }
        return caption, negatives.get(profile_id)

    def test_each_model_gets_its_own_fallback(self) -> None:
        for profile in MODEL_PROFILES.values():
            with self.subTest(model=profile.id):
                self.assertEqual(
                    fallback_prompt(profile, RESOLVED), self.expected(profile.id)
                )

    def test_models_without_a_negative_get_none(self) -> None:
        for model in ("krea2", "zimage"):
            self.assertEqual(
                fallback_prompt(MODEL_PROFILES[model], RESOLVED), (RESOLVED, None)
            )

    def test_only_sdxl_gets_the_quality_prefix(self) -> None:
        prefix = prompt("T2I_FALLBACK_PREFIX_SD").strip()
        for profile in MODEL_PROFILES.values():
            text, _ = fallback_prompt(profile, RESOLVED)
            self.assertEqual(text.startswith(prefix), profile.id == "sd", profile.id)

    def test_caption_is_stripped_and_an_empty_one_leaves_just_the_prefix(self) -> None:
        krea = MODEL_PROFILES["krea2"]
        self.assertEqual(fallback_prompt(krea, "  spaced out \n"), ("spaced out", None))
        prefix = prompt("T2I_FALLBACK_PREFIX_SD").strip()
        self.assertEqual(fallback_prompt(MODEL_PROFILES["sd"], "   ")[0], prefix)

    def test_a_profile_without_a_stock_negative_gets_none(self) -> None:
        custom = T2iModelProfile("custom", "Custom", "META_FLUX1", True, "ref", 16, 300)
        self.assertEqual(fallback_prompt(custom, RESOLVED), (RESOLVED, None))

    def test_parentheses_are_removed_from_the_fallback(self) -> None:
        for profile in MODEL_PROFILES.values():
            text, negative = fallback_prompt(profile, "Alice (30) waves (twice).")
            self.assertNotIn("(", text)
            self.assertNotIn(")", text)
            self.assertIn("Alice 30 waves twice.", text)
            self.assertTrue(negative is None or "(" not in negative)


class ParseT2iOutputTests(unittest.TestCase):
    POSITIVE = "A woman in a red sweater sits in a cafe, warm window light, 85mm lens."

    def test_models_with_a_negative_split_the_block(self) -> None:
        raw = f"{self.POSITIVE}\n\nNegative: low quality, blurry, extra fingers"
        for model in ("sd", "qwen"):
            with self.subTest(model=model):
                self.assertEqual(
                    parse_t2i_output(MODEL_PROFILES[model], raw),
                    (self.POSITIVE, "low quality, blurry, extra fingers"),
                )

    def test_models_without_a_negative_drop_a_stray_block(self) -> None:
        raw = f"{self.POSITIVE}\n\nNegative: low quality, blurry"
        for model in ("krea2", "zimage"):
            with self.subTest(model=model):
                self.assertEqual(
                    parse_t2i_output(MODEL_PROFILES[model], raw), (self.POSITIVE, None)
                )

    def test_a_missing_block_is_none_for_every_model(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertEqual(
                parse_t2i_output(profile, f"  {self.POSITIVE}\n"), (self.POSITIVE, None)
            )

    def test_fenced_and_labelled_output_is_cleaned(self) -> None:
        raw = "```\nBlock 1: a quiet harbour at dawn\n\nBlock 2: Negative: blur, noise\n```"
        self.assertEqual(
            parse_t2i_output(MODEL_PROFILES["qwen"], raw),
            ("a quiet harbour at dawn", "blur, noise"),
        )

    def test_parentheses_are_removed_from_both_parts(self) -> None:
        raw = "a woman (smiling) in a (red:1.2) coat\n\nNegative: (bad hands:1.3), blur"
        self.assertEqual(
            parse_t2i_output(MODEL_PROFILES["sd"], raw),
            ("a woman smiling in a red:1.2 coat", "bad hands:1.3, blur"),
        )


class StripParenthesesTests(unittest.TestCase):
    def test_text_without_parentheses_is_unchanged(self) -> None:
        for text in ("", "plain text", "line one\nline two", "two  spaces  stay"):
            self.assertEqual(strip_parentheses(text), text)

    def test_parentheses_are_removed_and_gaps_closed(self) -> None:
        cases = {
            "a (b) c": "a b c",
            "((double))": "double",
            "x ( y ) z": "x y z",
            "end (": "end ",
            "a (b)\nc (d)": "a b\nc d",
            "tab\t(x)": "tab\tx",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(strip_parentheses(text), expected)

    def test_result_never_contains_a_parenthesis(self) -> None:
        for text in ("(((", ")))", "a)b(c", "()", "(a)(b)(c)"):
            self.assertNotIn("(", strip_parentheses(text))
            self.assertNotIn(")", strip_parentheses(text))


if __name__ == "__main__":
    unittest.main()


class DirectionTests(unittest.TestCase):
    def test_no_direction_leaves_both_prompts_unchanged(self) -> None:
        profile = MODEL_PROFILES["krea2"]
        plain = compose_t2i_prompts(profile, "A woman reads.", "uncensored")
        for blank in (None, "", "   "):
            self.assertEqual(
                compose_t2i_prompts(profile, "A woman reads.", "uncensored", blank),
                plain,
            )
        self.assertEqual(
            plain[1], "DESCRIPTION:\nA woman reads.\n\nWrite the prompt now."
        )

    def test_a_direction_is_its_own_block_after_the_description(self) -> None:
        profile = MODEL_PROFILES["krea2"]
        system, user = compose_t2i_prompts(
            profile, "A woman reads.", "uncensored", "Give her a soft smile."
        )
        self.assertEqual(
            user,
            "DESCRIPTION:\nA woman reads.\n\nDIRECTION:\nGive her a soft smile."
            "\n\nWrite the prompt now.",
        )
        self.assertIn("DIRECTION", system)  # the preamble explains the block

    def test_the_fallback_appends_the_direction(self) -> None:
        sd = MODEL_PROFILES["sd"]
        plain, _ = fallback_prompt(sd, "A woman reads.")
        with_direction, _ = fallback_prompt(sd, "A woman reads.", "She smiles.")
        self.assertEqual(with_direction, plain + " She smiles.")
        self.assertEqual(
            fallback_prompt(sd, "A woman reads.", "  "),
            fallback_prompt(sd, "A woman reads."),
        )
