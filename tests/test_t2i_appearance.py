"""Tests for the person sampler (metascan/core/t2i_appearance.py) and how
the caption engine uses it.

Configs here are small and hand-written, or built from slot lists by
``tests/_t2i_appearance.py``; the shipped person_appearance.yaml is covered
in ``test_t2i_wildcards.py``. No user data.
"""

from __future__ import annotations

import copy
import random
import unittest
from typing import Any, Dict

from metascan.core import t2i_structured
from metascan.core.t2i_appearance import (
    AppearanceError,
    Sampler,
    parse,
    validate,
)
from metascan.core.t2i_characters import (
    CharacterConfig,
    Library,
    mark_caption,
    resolve_caption,
)
from metascan.core.t2i_wildcards import _reject_reason
from tests._t2i_appearance import SLOTS, library_from_lists
from tests.test_t2i_structured import reply, subject


def tiny() -> Dict[str, Any]:
    """Two ancestries with opposite tones, a mixed blend, ordinal skin."""
    return {
        "attributes": {
            "gender": {
                "values": {
                    "woman": {"weight": 1, "text": "woman"},
                    "man": {"weight": 1, "text": "man"},
                }
            },
            "ancestry": {
                "values": {
                    "north": {"weight": 1, "text": "Northern"},
                    "south": {"weight": 1, "text": "Southern"},
                    "mixed": {
                        "weight": 1,
                        "text": "mixed {a} and {b}",
                        "blend": {"count": 2, "ordinal": ["tone"], "ordinal_jitter": 0},
                    },
                }
            },
            "tone": {
                "weights_from": "ancestry",
                "values": {
                    "pale": {"text": "pale"},
                    "mid": {"text": "medium"},
                    "deep": {"text": "deep"},
                },
            },
            "hair_color": {
                "values": {
                    "black": {"weight": 1, "text": "black"},
                    "red": {
                        "weight": 1,
                        "text": "red",
                        "requires": {"ancestry": ["north"]},
                    },
                }
            },
        },
        "profiles": {
            "north": {"tone": {"pale": 1}},
            "south": {"tone": {"deep": 1}},
        },
        "compose": {"hair_token": [{"text": "{hair_color} hair"}]},
        "engine": {
            "genders": {"female": "woman", "male": "man"},
            "slots": {
                "ethnicity": "{ancestry}",
                "skin": "{tone} skin",
                "hair": "{hair_token}",
            },
            "head": ["ethnicity"],
            "with": ["skin", "hair"],
            "distinct": "hair",
        },
    }


class ValidateTests(unittest.TestCase):
    def test_the_tiny_config_is_valid(self) -> None:
        self.assertEqual(validate(tiny()), [])
        parse(tiny(), _reject_reason)

    def test_a_condition_on_a_later_attribute_is_an_error(self) -> None:
        cfg = tiny()
        cfg["attributes"]["tone"]["values"]["pale"]["requires"] = {
            "hair_color": ["black"]
        }
        errs = validate(cfg)
        self.assertTrue(any("sampled later" in e for e in errs), errs)
        with self.assertRaises(AppearanceError):
            parse(cfg)

    def test_engine_mistakes_are_errors(self) -> None:
        for change, needle in (
            (lambda e: e["slots"].update(skin="{nope} skin"), "unknown placeholder"),
            (lambda e: e.update(head=["missing"]), "not an engine slot"),
            (lambda e: e.update(distinct="missing"), "engine.distinct"),
            (lambda e: e.update(genders={"female": "lady"}), "not a gender value"),
        ):
            cfg = tiny()
            change(cfg["engine"])
            with self.subTest(needle=needle):
                with self.assertRaises(AppearanceError) as ctx:
                    parse(cfg)
                self.assertIn(needle, str(ctx.exception))

    def test_parse_never_changes_its_input(self) -> None:
        cfg = tiny()
        cfg["attributes"]["hair_color"]["values"]["black"]["text"] = [
            "black",
            "teen black",
        ]
        before = copy.deepcopy(cfg)
        _, warnings = parse(cfg, _reject_reason)
        self.assertEqual(cfg, before)
        self.assertEqual(len(warnings), 1)


class SamplerTests(unittest.TestCase):
    def test_a_seeded_sampler_is_deterministic(self) -> None:
        cfg = tiny()
        a = [Sampler(cfg, random.Random(5)).sample() for _ in range(3)]
        b = [Sampler(cfg, random.Random(5)).sample() for _ in range(3)]
        self.assertEqual(a, b)

    def test_requires_and_profiles_are_obeyed(self) -> None:
        cfg = tiny()
        sampler = Sampler(cfg, random.Random(1))
        for _ in range(200):
            state = sampler.sample({"ancestry": "south"})
            self.assertEqual(state["tone"]["ids"], ["deep"])
            self.assertEqual(state["hair_color"]["ids"], ["black"])

    def test_a_mixed_person_takes_the_midpoint_of_ordinal_attributes(self) -> None:
        sampler = Sampler(tiny(), random.Random(2))
        for _ in range(50):
            state = sampler.sample({"ancestry": "mixed"})
            self.assertEqual(sorted(state["ancestry"]["parents"]), ["north", "south"])
            self.assertEqual(state["tone"]["ids"], ["mid"])
            self.assertIn(
                state["ancestry"]["texts"][0],
                (
                    "mixed Northern and Southern",
                    "mixed Southern and Northern",
                ),
            )
            summary = sampler.summary(state)
            self.assertEqual(summary["ancestry"], "mixed")
            self.assertIn("north", summary["ancestry_parents"])


class PersonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.appearance, _ = parse(tiny(), _reject_reason)

    def test_a_person_is_a_function_of_key_and_name(self) -> None:
        a = self.appearance.person("k1", "ALICE", "female")
        self.assertEqual(a, self.appearance.person("k1", "ALICE", "female"))
        self.assertEqual(a.attributes["gender"], "woman")
        self.assertEqual(
            self.appearance.person("k1", "ADAM", "male").attributes["gender"], "man"
        )

    def test_a_taken_hair_is_drawn_again_with_the_same_ancestry(self) -> None:
        for i in range(40):
            first = self.appearance.person(f"key{i}", "ALICE", "female")
            again = self.appearance.person(
                f"key{i}", "BELLA", "female", {first.slots["hair"].casefold()}
            )
            plain = self.appearance.person(f"key{i}", "BELLA", "female")
            if again.clashed:
                continue  # southern people only have black hair: no way out
            self.assertNotEqual(again.slots["hair"], first.slots["hair"])
            self.assertEqual(again.attributes["ancestry"], plain.attributes["ancestry"])

    def test_a_config_that_can_draw_nobody_gives_none(self) -> None:
        cfg = tiny()
        for value in cfg["attributes"]["hair_color"]["values"].values():
            value["requires"] = {"ancestry": ["mixed"]}
        cfg["attributes"]["ancestry"]["values"]["mixed"]["weight"] = 0
        appearance, _ = parse(cfg, _reject_reason)
        self.assertIsNone(appearance.person("k", "ALICE", "female"))
        library = Library(config=CharacterConfig(), lists={}, appearance=appearance)
        res = resolve_caption("__ALICE__ waves.", 1, "ref", library)
        self.assertEqual(res.text, "A woman waves.")
        self.assertTrue(any("no valid person" in w for w in res.warnings))

    def test_compose_and_engine_texts_are_screened_up_front(self) -> None:
        for where in ("compose", "engine"):
            cfg = tiny()
            if where == "compose":
                cfg["compose"]["hair_token"] = [{"text": "{hair_color} teen hair"}]
            else:
                cfg["engine"]["slots"]["skin"] = "{tone} skin (soft)"
            with self.subTest(where=where):
                with self.assertRaises(AppearanceError):
                    parse(cfg, _reject_reason)

    def test_a_drawn_slot_that_fails_the_screen_is_left_out(self) -> None:
        # Each text passes alone; joined they spell a minor term ("tween").
        cfg = tiny()
        cfg["engine"]["slots"]["skin"] = "{tone}{hair_color}"
        for value in cfg["attributes"]["tone"]["values"].values():
            value["text"] = "t"
        for value in cfg["attributes"]["hair_color"]["values"].values():
            value["text"] = "ween"
        appearance, warnings = parse(cfg, _reject_reason)
        self.assertEqual(warnings, [])
        person = appearance.person("k", "ALICE", "female")
        self.assertNotIn("skin", person.slots)
        self.assertEqual(person.slots["hair"], "ween hair")
        self.assertTrue(any("rejected" in w for w in person.warnings))
        library = Library(config=CharacterConfig(), lists={}, appearance=appearance)
        res = resolve_caption("__ALICE__ waves.", 1, "ref", library)
        self.assertNotIn("tween", res.text)


# --- the caption engine with appearance layouts ------------------------------

STYLE_LISTS = {
    "age": ("31-year-old",),
    "ethnicity": ("Ghanaian",),
    "skin": ("deep brown skin",),
    "hair": ("black hair",),
    "hairstyle": ("long black braids",),
    "body.female": ("a slim build",),
    "body.male": ("a lean build",),
}
STYLE_LAYOUT: Dict[str, Any] = {
    "slots": (*SLOTS, "hairstyle"),
    "with_": ("skin", "hairstyle", "body"),
    "spells": {"hair": ["hairstyle"]},
}


class EngineLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = library_from_lists(STYLE_LISTS, **STYLE_LAYOUT)

    def test_the_full_hairstyle_is_described_and_the_handle_names_the_colour(
        self,
    ) -> None:
        res = resolve_caption(
            "__ALICE__ waves. __ALICE__ smiles.", 1, "ref", self.library
        )
        self.assertEqual(
            res.text,
            "A 31-year-old Ghanaian woman with deep brown skin, long black braids "
            "and a slim build waves. The black-haired woman smiles.",
        )
        self.assertEqual(res.attributes["ALICE"]["gender"], "woman")

    def test_a_spelled_hair_token_replaces_the_drawn_hairstyle(self) -> None:
        res = resolve_caption(
            "__ALICE__ waves, her short __HAIR__ in the wind.", 1, "ref", self.library
        )
        self.assertEqual(
            res.text,
            "A 31-year-old Ghanaian woman with deep brown skin and a slim build "
            "waves, her short black hair in the wind.",
        )

    def test_no_hair_token_means_no_hair_handle_and_the_bare_word(self) -> None:
        lists = {k: v for k, v in STYLE_LISTS.items() if k != "hair"}
        lists["hairstyle"] = ("a clean-shaven head",)
        library = library_from_lists(lists, **STYLE_LAYOUT)
        res = resolve_caption(
            "__ADAM__ nods. __ADAM__ rubs his __HAIR__.", 1, "ref", library
        )
        self.assertIn("The man rubs his hair.", res.text)

    def test_the_cast_member_carries_its_hair_trait(self) -> None:
        marked = mark_caption("__ALICE__ and __BELLA__ talk.", 1, self.library)
        alice = marked.cast[0]
        self.assertEqual(alice.hair, "black hair")
        self.assertEqual(alice.hair_trait, "long black braids")
        self.assertIn("long black braids", alice.traits)
        spelled = mark_caption("__ALICE__ shows her __HAIR__.", 1, self.library)
        self.assertIsNone(spelled.cast[0].hair_trait)

    def test_a_reply_that_says_the_hair_replaces_the_drawn_hairstyle(self) -> None:
        cast = mark_caption("__ALICE__ and __BELLA__ sit.", 1, self.library).cast
        for own_hair, shows, hides in (
            ("a messy bun", "a messy bun", "long black braids"),
            ("", "long black braids", "a messy bun"),
        ):
            raw = reply(
                [],
                subjects=[subject("ALICE", hair=own_hair), subject("BELLA")],
            )
            data = t2i_structured.parse(raw, cast)
            prompt, _ = t2i_structured.render(data, cast, _moss())
            alice = prompt.split("On the right")[0]
            with self.subTest(own_hair=own_hair):
                self.assertIn(shows, alice)
                self.assertNotIn(hides, alice)


def _moss() -> Any:
    from metascan.core.t2i_models import MODEL_PROFILES

    return MODEL_PROFILES["moss"]


if __name__ == "__main__":
    unittest.main()
