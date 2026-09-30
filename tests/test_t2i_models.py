"""Tests for the t2i model profile table (spec section 5.1)."""

from __future__ import annotations

import dataclasses
import unittest

from metascan.core.t2i_characters import IDENTITY_STYLES
from metascan.core.t2i_models import (
    MODEL_PROFILES,
    T2iModelError,
    T2iModelProfile,
    effective_identity,
    get_profile,
)

# id, label, guideline key, has_negative, identity, dim multiple, max tokens
SPEC_TABLE = [
    ("krea2", "Krea 2", "META_KREA2", False, "ref", 16, 420),
    ("qwen", "Qwen-Image", "META_QWEN", True, "ref", 16, 320),
    ("sd", "SDXL", "META_SDXL", True, "noun", 64, 480),
    ("zimage", "Z-Image", "META_ZIMAGE", False, "ref", 16, 280),
]


class ProfileTableTests(unittest.TestCase):
    def test_table_matches_the_spec_and_keeps_its_order(self) -> None:
        self.assertEqual(list(MODEL_PROFILES), ["krea2", "qwen", "sd", "zimage"])
        for row in SPEC_TABLE:
            with self.subTest(model=row[0]):
                profile = MODEL_PROFILES[row[0]]
                self.assertEqual(
                    (
                        profile.id,
                        profile.label,
                        profile.meta_key,
                        profile.has_negative,
                        profile.identity,
                        profile.dim_multiple,
                        profile.max_tokens,
                    ),
                    row,
                )

    def test_profiles_are_frozen(self) -> None:
        profile = MODEL_PROFILES["sd"]
        self.assertIsInstance(profile, T2iModelProfile)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.max_tokens = 1  # type: ignore[misc]

    def test_every_default_identity_is_a_known_style(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertIn(profile.identity, IDENTITY_STYLES)

    def test_only_qwen_and_sdxl_have_a_negative_prompt(self) -> None:
        self.assertEqual(
            sorted(p.id for p in MODEL_PROFILES.values() if p.has_negative),
            ["qwen", "sd"],
        )

    def test_dimension_multiples_are_positive_powers_of_two(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertGreater(profile.dim_multiple, 0)
            self.assertEqual(profile.dim_multiple & (profile.dim_multiple - 1), 0)


class GetProfileTests(unittest.TestCase):
    def test_known_ids_return_the_table_entry(self) -> None:
        for model_id, profile in MODEL_PROFILES.items():
            self.assertIs(get_profile(model_id), profile)

    def test_unknown_id_raises_a_keyerror_subclass_naming_the_choices(self) -> None:
        for bad in ("flux", "", "KREA2", None):
            with self.subTest(bad=bad):
                with self.assertRaises(T2iModelError) as ctx:
                    get_profile(bad)  # type: ignore[arg-type]
                self.assertIsInstance(ctx.exception, KeyError)
                message = str(ctx.exception)
                self.assertFalse(message.startswith("'"), message)
                for known in MODEL_PROFILES:
                    self.assertIn(known, message)


class EffectiveIdentityTests(unittest.TestCase):
    def test_no_override_uses_the_profile_default(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertEqual(effective_identity(profile, {}), profile.identity)

    def test_a_valid_override_for_the_model_wins(self) -> None:
        sd = MODEL_PROFILES["sd"]
        krea = MODEL_PROFILES["krea2"]
        self.assertEqual(effective_identity(sd, {"sd": "name"}), "name")
        self.assertEqual(effective_identity(krea, {"krea2": "noun"}), "noun")
        self.assertEqual(effective_identity(krea, {"krea2": "ref"}), "ref")

    def test_overrides_for_other_models_are_ignored(self) -> None:
        self.assertEqual(
            effective_identity(MODEL_PROFILES["qwen"], {"sd": "name", "krea2": "noun"}),
            "ref",
        )

    def test_an_unknown_style_falls_back_to_the_default(self) -> None:
        for bad in ("bogus", "", "REF", None, 3):
            with self.subTest(bad=bad):
                self.assertEqual(
                    effective_identity(MODEL_PROFILES["sd"], {"sd": bad}),  # type: ignore[dict-item]
                    "noun",
                )


if __name__ == "__main__":
    unittest.main()
