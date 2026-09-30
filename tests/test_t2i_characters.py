"""Tests for the pure t2i caption engine (spec section 3).

Every caption in this file is hand-written with known expected properties.
The word lists are small stand-ins defined below -- never the shipped
starter lists and never rows of the real caption CSV.

A caption's characters are drawn from the sha256 of its text, so a different
caption is a different cast. The tests that pin wording therefore run on
``FORCED``, whose lists hold one value each: the cast is the same whatever the
caption says, and the expected text is written out by hand. The tests about
the draws themselves run on ``LIBRARY``, whose lists have variety.
"""

from __future__ import annotations

import hashlib
import time
import unittest
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from metascan.core.t2i_characters import (
    IDENTITY_STYLES,
    CharacterConfig,
    Library,
    ResolvedCaption,
    resolve_caption,
)

LISTS: Dict[str, Tuple[str, ...]] = {
    "age": (
        "27-year-old",
        "31-year-old",
        "24-year-old",
        "38-year-old",
        "45-year-old",
        "52-year-old",
    ),
    "ethnicity": (
        "Nordic",
        "Latina",
        "East Asian",
        "West African",
        "South Asian",
        "Mediterranean",
    ),
    "skin": ("fair skin", "olive skin", "deep brown skin", "warm tan skin"),
    "eyes": ("green eyes", "hazel eyes", "brown eyes", "blue eyes"),
    "face": (
        "an oval face",
        "a heart-shaped face",
        "high cheekbones",
        "a soft round face",
    ),
    "hair": (
        "auburn hair",
        "jet-black hair",
        "platinum blonde hair",
        "chestnut brown hair",
        "copper red hair",
    ),
    "body.female": (
        "a slim build",
        "a petite frame",
        "an athletic build",
        "a curvy build",
    ),
    "body.male": (
        "an athletic build",
        "a lean build",
        "a stocky build",
        "a broad-shouldered build",
    ),
    # breasts / vagina / penis lists are deliberately absent: they exercise
    # the bare-word fallback. Tests that need them add abstract placeholders.
}

PLACEHOLDER_LISTS: Dict[str, Tuple[str, ...]] = {
    "breasts": ("breasts-alpha", "breasts-beta", "breasts-gamma"),
    "vagina": ("vagina-alpha", "vagina-beta"),
    "penis": ("penis-alpha", "penis-beta", "penis-gamma"),
}

# One value per list, so a lone female character is always this woman. Only her
# own lists are given: two characters of one gender would share a hair value.
FORCED_LISTS: Dict[str, Tuple[str, ...]] = {
    "age": ("31-year-old",),
    "ethnicity": ("West African",),
    "skin": ("olive skin",),
    "eyes": ("brown eyes",),
    "face": ("a soft round face",),
    "hair": ("copper red hair",),
    "body.female": ("an athletic build",),
    "body.male": ("a lean build",),
}

CONFIG = CharacterConfig()


def make_library(
    extra: Optional[Mapping[str, Tuple[str, ...]]] = None,
    drop: Sequence[str] = (),
    config: Optional[CharacterConfig] = None,
    base: Optional[Mapping[str, Tuple[str, ...]]] = None,
) -> Library:
    lists = dict(LISTS if base is None else base)
    lists.update(extra or {})
    for key in drop:
        lists.pop(key, None)
    return Library(config=config or CONFIG, lists=lists)


LIBRARY = make_library()
FORCED = make_library(base=FORCED_LISTS)


def resolve(
    caption: str,
    seed: int = 101,
    style: str = "ref",
    library: Optional[Library] = None,
) -> ResolvedCaption:
    return resolve_caption(caption, seed, style, library or LIBRARY)


def resolve_forced(caption: str, style: str = "ref") -> ResolvedCaption:
    """``resolve`` with the forced cast: for tests about wording, not draws."""
    return resolve(caption, style=style, library=FORCED)


def takes(caption: str, count: int) -> List[str]:
    """``count`` captions that differ only in a closing "Take N.": each is its
    own cast, which is how the tests sample many draws now that the seed does
    not vary them. No parentheses, so the suffix cannot fake or hide one."""
    return [f"{caption} Take {n}." for n in range(count)]


def cap1(text: str) -> str:
    return text[:1].upper() + text[1:]


def cast_key(caption: str) -> str:
    """What a caption's character draws are keyed on, restated independently of
    the module: the sha256 of the text exactly as given."""
    return hashlib.sha256(caption.encode("utf-8")).hexdigest()


def draw(key: object, name: str, slot: str, salt: int, size: int) -> int:
    """The draw formula, restated independently of the module."""
    digest = hashlib.sha256(f"{key}|{name}|{slot}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


# The forced woman (``FORCED_LISTS``), as the engine words her:
ALICE_INLINE = (
    "a 31-year-old West African woman with olive skin, brown eyes, "
    "a soft round face, copper red hair and an athletic build"
)
ALICE_TRAIL = (
    "The copper-red-haired woman has olive skin, brown eyes, "
    "a soft round face and an athletic build."
)


class DefaultsTests(unittest.TestCase):
    def test_identity_styles(self) -> None:
        self.assertEqual(IDENTITY_STYLES, ("ref", "noun", "name"))

    def test_character_config_defaults_match_the_spec(self) -> None:
        cfg = CharacterConfig()
        self.assertEqual(
            cfg.female_names, ("ALICE", "BELLA", "CLARA", "DIANNA", "EMMA")
        )
        self.assertEqual(cfg.male_names, ("ADAM", "BOB"))
        self.assertEqual((cfg.noun_female, cfg.noun_male), ("woman", "man"))
        self.assertEqual(
            cfg.slots, ("age", "ethnicity", "skin", "eyes", "face", "hair", "body")
        )
        self.assertEqual(cfg.head_slots, ("age", "ethnicity"))
        self.assertEqual(cfg.with_slots, ("skin", "eyes", "face", "hair", "body"))
        self.assertEqual(cfg.token_slots, ("hair", "breasts", "vagina", "penis"))
        self.assertEqual(
            cfg.token_gender,
            (("breasts", "female"), ("vagina", "female"), ("penis", "male")),
        )
        self.assertEqual(
            cfg.body_hair_prefixes,
            (
                "pubic",
                "body",
                "facial",
                "chest",
                "arm",
                "leg",
                "underarm",
                "armpit",
                "stomach",
            ),
        )


class AppendixBGoldenTests(unittest.TestCase):
    """The spec's worked examples (Appendix B), identity style ref. The two
    with one woman run on the forced cast; the compound subject shows the draws
    its own caption's sha256 gives its two characters."""

    def test_male_and_female_compound_subject(self) -> None:
        caption = (
            "__ADAM__ and __CLARA__ walk along a beach. His short __HAIR__ is damp "
            "and her __HAIR__ is tied back. __ADAM__ carries a surfboard."
        )
        expected = (
            "A 45-year-old East Asian middle aged man and a 31-year-old East Asian "
            "woman walk along a beach. His short jet-black hair is damp and her "
            "auburn hair "
            "is tied back. The jet-black-haired man carries a surfboard. The "
            "jet-black-haired man has fair skin, brown eyes, an oval face and "
            "a lean build. The auburn-haired woman has deep brown skin, green eyes, "
            "high cheekbones and a curvy build."
        )
        self.assertEqual(resolve(caption).text, expected)

    def test_single_character_with_hair_token_and_later_mention(self) -> None:
        caption = (
            "__ALICE__ sits on a wooden bench in a sunlit garden, her "
            "shoulder-length wavy __HAIR__ moving in the breeze. She holds a paper "
            "cup in both hands while __ALICE__ smiles at the camera."
        )
        expected = (
            "A 31-year-old West African woman with olive skin, brown eyes, a soft "
            "round face and an athletic build sits on a wooden bench in a sunlit "
            "garden, her shoulder-length wavy copper red hair moving in the "
            "breeze. She holds a paper cup in both hands while the "
            "copper-red-haired woman smiles at the camera."
        )
        self.assertEqual(resolve_forced(caption).text, expected)

    def test_possessive_first_mention_body_hair_and_fused_suffix(self) -> None:
        caption = (
            "A close-up of __ALICE__'s hands as she combs her __HAIR__ with a "
            "__HAIR__brush, one forearm showing fine body __HAIR__."
        )
        expected = (
            "A close-up of a 31-year-old West African woman's hands as she combs "
            "her copper red hair with a hairbrush, one forearm showing fine body "
            "hair. The copper-red-haired woman has olive skin, brown eyes, a soft "
            "round face and an athletic build."
        )
        self.assertEqual(resolve_forced(caption).text, expected)


class IdentityStyleTests(unittest.TestCase):
    SINGLE = (
        "__ALICE__ sits on a wooden bench in a sunlit garden, her "
        "shoulder-length wavy __HAIR__ moving in the breeze. She holds a paper "
        "cup in both hands while __ALICE__ smiles at the camera."
    )
    TWO_FEMALES = (
        "__ALICE__ stands beside a window while __BELLA__ sits at a desk. Long, "
        "straight __HAIR__ falls over __BELLA__'s shoulders. __ALICE__ turns to "
        "look at __BELLA__."
    )
    POSSESSIVE = (
        "A close-up of __ALICE__'s hands as she combs her __HAIR__ with a "
        "__HAIR__brush, one forearm showing fine body __HAIR__."
    )

    def test_single_character_all_three_styles(self) -> None:
        head_rest = (
            " sits on a wooden bench in a sunlit garden, her shoulder-length wavy "
            "copper red hair moving in the breeze. She holds a paper cup in both "
            "hands while "
        )
        intro = (
            "A 31-year-old West African woman{named} with olive skin, brown eyes, "
            "a soft round face and an athletic build"
        )
        cases = {
            "ref": (intro.format(named=""), "the copper-red-haired woman"),
            "noun": (intro.format(named=""), "the woman"),
            "name": (intro.format(named=" named Alice"), "Alice"),
        }
        for style, (opening, handle) in cases.items():
            with self.subTest(style=style):
                expected = f"{opening}{head_rest}{handle} smiles at the camera."
                self.assertEqual(
                    resolve_forced(self.SINGLE, style=style).text, expected
                )

    def test_two_females_owner_tracking_all_three_styles(self) -> None:
        # Two women of one gender cannot be forced (their hair must differ), so
        # this is the cast their caption's sha256 draws.
        alice = (
            "A 45-year-old Latina middle aged woman{named} with olive skin, green "
            "eyes, a soft round face, chestnut brown hair and an athletic build"
        )
        bella = (
            "a 38-year-old Mediterranean woman{named} with fair skin, hazel "
            "eyes, a soft round face and an athletic build"
        )
        expected = {
            "ref": (
                alice.format(named="")
                + " stands beside a window while "
                + bella.format(named="")
                + " sits at a desk. Long, straight auburn hair falls over "
                "the auburn-haired woman's shoulders. The "
                "chestnut-brown-haired woman turns to look at the "
                "auburn-haired woman."
            ),
            "noun": (
                alice.format(named="")
                + " stands beside a window while "
                + bella.format(named="")
                + " sits at a desk. Long, straight auburn hair falls over "
                "the woman's shoulders. The woman turns to look at the woman."
            ),
            "name": (
                alice.format(named=" named Alice")
                + " stands beside a window while "
                + bella.format(named=" named Bella")
                + " sits at a desk. Long, straight auburn hair falls over "
                "Bella's shoulders. Alice turns to look at Bella."
            ),
        }
        for style, text in expected.items():
            with self.subTest(style=style):
                self.assertEqual(resolve(self.TWO_FEMALES, style=style).text, text)

    def test_possessive_first_mention_all_three_styles(self) -> None:
        common_a = "A close-up of a 31-year-old West African woman"
        common_b = (
            " as she combs her copper red hair with a hairbrush, one forearm "
            "showing fine body hair. "
        )
        rest = "has olive skin, brown eyes, a soft round face and an athletic build."
        expected = {
            "ref": f"{common_a}'s hands{common_b}The copper-red-haired woman {rest}",
            "noun": f"{common_a}'s hands{common_b}The woman {rest}",
            # A name-style handle appears in the trailing sentence too.
            "name": f"{common_a} named Alice's hands{common_b}Alice {rest}",
        }
        for style, text in expected.items():
            with self.subTest(style=style):
                self.assertEqual(
                    resolve_forced(self.POSSESSIVE, style=style).text, text
                )

    def test_ref_falls_back_to_noun_when_hair_does_not_end_in_hair(self) -> None:
        library = make_library({"hair": ("long braids",)})
        res = resolve(
            "__ALICE__ waves. Later __ALICE__ smiles.", library=library, style="ref"
        )
        self.assertIn("Later the woman smiles.", res.text)
        self.assertNotIn("-haired", res.text)

    def test_noun_style_trailing_sentence_keeps_hair(self) -> None:
        text = resolve_forced("A close-up of __ALICE__'s hands.", style="noun").text
        self.assertEqual(
            text,
            "A close-up of a 31-year-old West African woman's hands. The woman has "
            "olive skin, brown eyes, a soft round face, copper red hair and an "
            "athletic build.",
        )

    def test_ref_trailing_sentence_drops_hair_only_when_the_handle_carries_it(
        self,
    ) -> None:
        caption = "A close-up of __ALICE__'s hands."
        ref = resolve_forced(caption, style="ref").text
        self.assertEqual(
            ref,
            "A close-up of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )
        library = make_library({"hair": ("long braids",)}, base=FORCED_LISTS)
        no_handle = resolve(caption, style="ref", library=library).text
        self.assertTrue(no_handle.endswith("long braids and an athletic build."))
        self.assertIn("The woman has", no_handle)


class FirstMentionTests(unittest.TestCase):
    def test_inline_details_and_hair_omitted_when_spelled_out_by_a_token(self) -> None:
        text = resolve_forced("__ALICE__ waves. Her __HAIR__ is loose.").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman with olive skin, brown eyes, a soft "
            "round face and an athletic build waves. Her copper red hair is loose.",
        )

    def test_with_continuation_puts_details_in_a_trailing_sentence(self) -> None:
        text = resolve_forced("__ALICE__ with a red scarf waves.").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman with a red scarf waves. " + ALICE_TRAIL,
        )

    def test_next_word_blocklist_forces_a_trailing_sentence(self) -> None:
        for word in (
            "with",
            "and",
            "or",
            "in",
            "on",
            "at",
            "who",
            "whose",
            "wearing",
            "holding",
            "while",
            "as",
        ):
            with self.subTest(word=word):
                text = resolve_forced(f"__ALICE__ {word} a friend waves.").text
                self.assertEqual(
                    text,
                    f"A 31-year-old West African woman {word} a friend waves. "
                    + ALICE_TRAIL,
                )

    def test_punctuation_after_the_token_forces_a_trailing_sentence(self) -> None:
        for mark in (",", ".", ";", ":", "!", "?", "\u2014"):
            with self.subTest(mark=mark):
                text = resolve_forced(f"Meet __ALICE__{mark} she waves.").text
                self.assertEqual(
                    text,
                    f"Meet a 31-year-old West African woman{mark} she waves. "
                    + ALICE_TRAIL,
                )

    def test_a_verb_after_the_token_stays_inline(self) -> None:
        text = resolve_forced("__ALICE__ waves.").text
        self.assertEqual(text, cap1(ALICE_INLINE) + " waves.")

    def test_token_at_the_end_of_the_string_stays_inline(self) -> None:
        self.assertEqual(
            resolve_forced("Look at __ALICE__").text, "Look at " + ALICE_INLINE
        )

    def test_possessive_forces_a_trailing_sentence_straight_or_curly(self) -> None:
        straight = resolve_forced("A close-up of __ALICE__'s hands.").text
        curly = resolve_forced("A close-up of __ALICE__\u2019s hands.").text
        self.assertEqual(
            straight,
            "A close-up of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )
        self.assertEqual(curly, straight.replace("'", "\u2019"))

    def test_trailing_sentence_gets_a_full_stop_when_the_caption_has_none(self) -> None:
        text = resolve_forced("A portrait of __ALICE__'s hands").text
        self.assertEqual(
            text,
            "A portrait of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )

    def test_trailing_sentence_looks_through_closing_quotes_for_the_stop(self) -> None:
        stopped = resolve_forced('She says "meet __ALICE__, please."').text
        self.assertEqual(
            stopped,
            'She says "meet a 31-year-old West African woman, please." ' + ALICE_TRAIL,
        )
        unstopped = resolve_forced('He said "hi to __ALICE__"').text
        self.assertEqual(
            unstopped,
            'He said "hi to a 31-year-old West African woman". ' + ALICE_TRAIL,
        )

    def test_a_dangling_clause_mark_becomes_the_stop(self) -> None:
        text = resolve_forced("A portrait of __ALICE__'s friend,").text
        self.assertEqual(
            text,
            "A portrait of a 31-year-old West African woman's friend. " + ALICE_TRAIL,
        )

    def test_trailing_whitespace_is_kept_after_the_trailing_sentence(self) -> None:
        text = resolve_forced("__ALICE__, smiling.\n").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman, smiling. " + ALICE_TRAIL + "\n",
        )

    def test_deferred_characters_trail_in_first_mention_order(self) -> None:
        text = resolve("__ALICE__, __BELLA__ and __CLARA__ wave.").text
        self.assertEqual(
            text,
            "A 24-year-old West African woman, a 45-year-old South Asian middle "
            "aged woman and a 52-year-old West African middle aged woman wave. "
            "The platinum-blonde-haired woman has warm tan skin, blue eyes, a "
            "heart-shaped face and an athletic build. The chestnut-brown-haired "
            "woman has deep brown skin, green eyes, a soft round face and a slim "
            "build. The auburn-haired woman has olive skin, brown eyes, an oval "
            "face and an athletic build.",
        )

    def test_compound_subject_with_comma_and_oxford_and(self) -> None:
        text = resolve("__ALICE__, __BELLA__, and __CLARA__ wave.").text
        # Nobody gets inline details: each name is part of a compound subject.
        self.assertNotIn("woman with", text)
        self.assertEqual(text.count(" has "), 3)

    def test_no_trailing_sentence_when_there_is_nothing_left_to_say(self) -> None:
        library = Library(config=CONFIG, lists={"hair": ("auburn hair",)})
        res = resolve(
            "A close-up of __ALICE__'s hands. Her __HAIR__ shines.", library=library
        )
        self.assertEqual(
            res.text, "A close-up of a woman's hands. Her auburn hair shines."
        )

    def test_hair_is_never_lost_when_it_is_the_only_detail(self) -> None:
        library = Library(config=CONFIG, lists={"hair": ("auburn hair",)})
        for style, expected in (
            ("ref", "A close-up of a woman's hands. The woman has auburn hair."),
            ("noun", "A close-up of a woman's hands. The woman has auburn hair."),
            (
                "name",
                "A close-up of a woman named Alice's hands. Alice has auburn hair.",
            ),
        ):
            with self.subTest(style=style):
                res = resolve(
                    "A close-up of __ALICE__'s hands.", style=style, library=library
                )
                self.assertEqual(res.text, expected)

    def test_an_before_a_vowel_or_a_number_spoken_with_a_vowel(self) -> None:
        cases = (
            ({"age": ("82-year-old",)}, "An 82-year-old West African old woman"),
            ({"age": ("88-year-old",)}, "An 88-year-old West African old woman"),
            ({"age": ("80-year-old",)}, "An 80-year-old West African old woman"),
            ({"age": ("18-year-old",)}, "An 18-year-old West African woman"),
            ({"age": ("27-year-old",)}, "A 27-year-old West African woman"),
            ({"age": ("100-year-old",)}, "A 100-year-old West African old woman"),
            ({"age": ("108-year-old",)}, "A 108-year-old West African old woman"),
        )
        for extra, expected in cases:
            with self.subTest(age=extra["age"][0]):
                library = make_library(extra, base=FORCED_LISTS)
                text = resolve("__ALICE__ waves.", library=library).text
                self.assertTrue(text.startswith(expected), text)

    def test_an_before_ordinals_spoken_with_a_vowel(self) -> None:
        cases = (
            ("11th-generation American", "An 11th-generation American woman with"),
            ("8th-generation American", "An 8th-generation American woman with"),
            ("18th-century Parisian", "An 18th-century Parisian woman with"),
            ("7th-generation American", "A 7th-generation American woman with"),
        )
        for ethnicity, expected in cases:
            with self.subTest(ethnicity=ethnicity):
                library = make_library({"age": (), "ethnicity": (ethnicity,)})
                text = resolve("__ALICE__ waves.", library=library).text
                self.assertTrue(text.startswith(expected), text)

    def test_article_follows_the_first_word_when_there_is_no_age(self) -> None:
        cases = (
            ("Asian", "An Asian woman with"),
            ("European", "A European woman with"),
            ("Nordic", "A Nordic woman with"),
            ("Ukrainian", "A Ukrainian woman with"),
            ("Uzbek", "An Uzbek woman with"),
        )
        for ethnicity, expected in cases:
            with self.subTest(ethnicity=ethnicity):
                library = make_library({"age": (), "ethnicity": (ethnicity,)})
                text = resolve("__ALICE__ waves.", library=library).text
                self.assertTrue(text.startswith(expected), text)

    def test_head_is_just_the_noun_when_age_and_ethnicity_are_missing(self) -> None:
        library = make_library({"age": (), "ethnicity": ()}, base=FORCED_LISTS)
        text = resolve("__ALICE__ waves.", library=library).text
        self.assertTrue(text.startswith("A woman with olive skin"), text)


class AgedNounTests(unittest.TestCase):
    """A first mention says "middle aged" from 40 and "old" past 70: a bare
    "woman" or "man" makes image models draw the subject younger than the
    number. Every list here has one value, so every draw is forced and the
    expected text is written out by hand."""

    def resolve_with(
        self,
        age: str,
        caption: str = "__ALICE__ waves.",
        style: str = "ref",
        extra: Optional[Mapping[str, Tuple[str, ...]]] = None,
    ) -> str:
        lists: Dict[str, Tuple[str, ...]] = {
            "age": (age,),
            "ethnicity": ("Nordic",),
            "hair": ("auburn hair",),
        }
        lists.update(extra or {})
        return resolve(caption, style=style, library=make_library(lists)).text

    def test_under_forty_keeps_the_plain_noun(self) -> None:
        cases = (
            ("27-year-old", "A 27-year-old Nordic woman with"),
            ("39-year-old", "A 39-year-old Nordic woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_forty_and_up_is_middle_aged(self) -> None:
        cases = (
            ("40-year-old", "A 40-year-old Nordic middle aged woman with"),
            ("55-year-old", "A 55-year-old Nordic middle aged woman with"),
            ("70-year-old", "A 70-year-old Nordic middle aged woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_over_seventy_is_old(self) -> None:
        cases = (
            ("71-year-old", "A 71-year-old Nordic old woman with"),
            ("82-year-old", "An 82-year-old Nordic old woman with"),
            ("108-year-old", "A 108-year-old Nordic old woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_men_get_the_same_words(self) -> None:
        cases = (
            ("39-year-old", "A 39-year-old Nordic man with"),
            ("40-year-old", "A 40-year-old Nordic middle aged man with"),
            ("70-year-old", "A 70-year-old Nordic middle aged man with"),
            ("71-year-old", "A 71-year-old Nordic old man with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                text = self.resolve_with(age, caption="__ADAM__ waves.")
                self.assertTrue(text.startswith(expected), text)

    def test_spelled_out_ages_count_too(self) -> None:
        cases = (
            ("thirty-nine-year-old", "A thirty-nine-year-old Nordic woman with"),
            ("forty-year-old", "A forty-year-old Nordic middle aged woman with"),
            ("seventy-year-old", "A seventy-year-old Nordic middle aged woman with"),
            ("seventy-one-year-old", "A seventy-one-year-old Nordic old woman with"),
            ("eighty-year-old", "An eighty-year-old Nordic old woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_an_age_with_no_number_is_left_alone(self) -> None:
        cases = (
            ("elderly", "An elderly Nordic woman with"),
            ("middle-aged", "A middle-aged Nordic woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_the_first_number_in_the_value_is_the_age(self) -> None:
        cases = (
            ("35-to-45-year-old", "A 35-to-45-year-old Nordic woman with"),
            ("45-to-50-year-old", "A 45-to-50-year-old Nordic middle aged woman with"),
        )
        for age, expected in cases:
            with self.subTest(age=age):
                self.assertTrue(self.resolve_with(age).startswith(expected))

    def test_no_age_list_means_no_age_wording(self) -> None:
        text = self.resolve_with("ignored", extra={"age": ()})
        self.assertTrue(text.startswith("A Nordic woman with"), text)

    def test_only_the_first_mention_changes(self) -> None:
        caption = "__ALICE__ waves. __ALICE__ smiles."
        for age, phrase in (
            ("52-year-old", "middle aged"),
            ("82-year-old", "old woman"),
        ):
            with self.subTest(age=age):
                text = self.resolve_with(age, caption=caption)
                self.assertEqual(text.count(phrase), 1, text)
                self.assertTrue(text.endswith("The auburn-haired woman smiles."), text)

    def test_every_identity_style_keeps_the_words_on_the_first_mention(self) -> None:
        cases = (
            (
                "ref",
                "A 52-year-old Nordic middle aged woman with",
                "The auburn-haired woman smiles.",
            ),
            (
                "noun",
                "A 52-year-old Nordic middle aged woman with",
                "The woman smiles.",
            ),
            (
                "name",
                "A 52-year-old Nordic middle aged woman named Alice with",
                "Alice smiles.",
            ),
        )
        for style, start, end in cases:
            with self.subTest(style=style):
                text = self.resolve_with(
                    "52-year-old",
                    caption="__ALICE__ waves. __ALICE__ smiles.",
                    style=style,
                )
                self.assertTrue(text.startswith(start), text)
                self.assertTrue(text.endswith(end), text)

    def test_a_deferred_first_mention_has_the_words_and_its_trailing_sentence_does_not(
        self,
    ) -> None:
        text = self.resolve_with(
            "52-year-old", caption="A close-up of __ALICE__'s hands."
        )
        self.assertTrue(
            text.startswith(
                "A close-up of a 52-year-old Nordic middle aged woman's hands."
            ),
            text,
        )
        self.assertIn("The auburn-haired woman has ", text)
        self.assertEqual(text.count("middle aged"), 1, text)

    def test_each_character_is_judged_by_their_own_age(self) -> None:
        text = self.resolve_with(
            "unused",
            caption="__ADAM__ and __CLARA__ walk.",
            extra={"age.male": ("71-year-old",), "age.female": ("27-year-old",)},
        )
        self.assertTrue(
            text.startswith(
                "A 71-year-old Nordic old man and a 27-year-old Nordic woman walk."
            ),
            text,
        )

    def test_the_drawn_values_are_not_rewritten(self) -> None:
        resolved = resolve(
            "__ALICE__ waves.",
            library=make_library({"age": ("52-year-old",), "ethnicity": ("Nordic",)}),
        )
        self.assertEqual(resolved.characters["ALICE"]["age"], "52-year-old")

    def test_the_configured_noun_is_the_one_that_gets_the_words(self) -> None:
        library = make_library(
            {
                "age": ("52-year-old",),
                "ethnicity": ("Nordic",),
                "hair": ("auburn hair",),
            },
            config=CharacterConfig(noun_female="lady"),
        )
        text = resolve("__ALICE__ waves. __ALICE__ smiles.", library=library).text
        self.assertTrue(
            text.startswith("A 52-year-old Nordic middle aged lady with"), text
        )
        self.assertTrue(text.endswith("The auburn-haired lady smiles."), text)

    def test_the_rule_follows_whatever_age_the_caption_draws(self) -> None:
        # The stand-in age list holds 27, 31, 24, 38, 45 and 52: only 45 and 52
        # are middle aged. The expectation is this literal table, not the code.
        middle_aged = {"45-year-old", "52-year-old"}
        seen = set()
        for caption in takes("__ALICE__ waves.", 120):
            resolved = resolve(caption)
            drawn = resolved.characters["ALICE"]
            seen.add(drawn["age"])
            noun = "middle aged woman" if drawn["age"] in middle_aged else "woman"
            head = resolved.text.split(" with ")[0]
            with self.subTest(caption=caption):
                self.assertTrue(head.endswith(f"{drawn['ethnicity']} {noun}"), head)
        self.assertEqual(seen, set(LISTS["age"]))  # the loop really met every age


class CharacteristicTokenTests(unittest.TestCase):
    def test_hair_token_before_the_first_mention_binds_to_the_first_female(
        self,
    ) -> None:
        text = resolve_forced(
            "Her long __HAIR__ falls over her shoulders as __ALICE__ smiles."
        ).text
        self.assertEqual(
            text,
            "Her long copper red hair falls over her shoulders as a 31-year-old "
            "West African woman with olive skin, brown eyes, a soft round face "
            "and an athletic build smiles.",
        )

    def test_hair_with_no_pronoun_goes_to_the_nearest_named_character(self) -> None:
        res = resolve(
            "__ALICE__ and __BELLA__ pose while a stylist adjusts the __HAIR__.",
            style="noun",
        )
        alice = res.characters["ALICE"]["hair"]
        bella = res.characters["BELLA"]["hair"]
        self.assertIn(f"adjusts the {bella}.", res.text)
        # BELLA's hair is spelled out once (the token); ALICE's is not spelled
        # out, so it stays in her trailing description.
        self.assertEqual(res.text.count(bella), 1)
        self.assertEqual(res.text.count(alice), 1)

    def test_feminine_pronoun_beats_proximity(self) -> None:
        res = resolve(
            "__ALICE__ watches __ADAM__ while she brushes her __HAIR__. __ADAM__ nods.",
            style="noun",
        )
        alice = res.characters["ALICE"]["hair"]
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"she brushes her {alice}.", res.text)
        self.assertEqual(res.text.count(alice), 1)
        # ADAM's own hair is not spelled out, so it stays in his description.
        self.assertEqual(res.text.count(adam), 1)

    def test_masculine_pronoun_beats_proximity(self) -> None:
        res = resolve(
            "__ADAM__ watches __ALICE__ while he brushes his __HAIR__. __ALICE__ nods."
        )
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"he brushes his {adam}.", res.text)

    def test_the_nearest_pronoun_wins_when_both_genders_are_in_the_window(
        self,
    ) -> None:
        # "he" and "her" (or "she" and "his") are both inside the six-word
        # window; the one nearest the token decides. The names are given in
        # both orders so the nearest NAMED character is sometimes the wrong one.
        cases = (
            ("__ADAM__ sees __ALICE__ and he touches her __HAIR__.", "ALICE", "her"),
            ("__ALICE__ sees __ADAM__ and he touches her __HAIR__.", "ALICE", "her"),
            ("__ALICE__ sees __ADAM__ and she touches his __HAIR__.", "ADAM", "his"),
            ("__ADAM__ sees __ALICE__ and she touches his __HAIR__.", "ADAM", "his"),
        )
        for caption, owner, pronoun in cases:
            with self.subTest(caption=caption):
                res = resolve(caption)
                hair = res.characters[owner]["hair"]
                self.assertIn(f"touches {pronoun} {hair}.", res.text)

    def test_two_hair_tokens_in_one_sentence_follow_their_own_pronouns(self) -> None:
        # Both "Her" and "his" sit in the second token's window; "his" is nearer.
        for names in ("__ALICE__ and __ADAM__", "__ADAM__ and __ALICE__"):
            with self.subTest(names=names):
                res = resolve(
                    f"{names} stand together. "
                    "Her __HAIR__ is long and his __HAIR__ is short."
                )
                alice = res.characters["ALICE"]["hair"]
                adam = res.characters["ADAM"]["hair"]
                self.assertNotEqual(alice, adam)
                self.assertIn(f"Her {alice} is long and his {adam} is short.", res.text)

    def test_the_nearest_pronoun_beats_an_earlier_one_of_the_other_gender(
        self,
    ) -> None:
        cases = (
            (
                "__ADAM__ and __ALICE__ talk. He holds her __HAIR__ back.",
                "ALICE",
                "He holds her {} back.",
            ),
            (
                "__ALICE__ and __ADAM__ talk. He holds her __HAIR__ back.",
                "ALICE",
                "He holds her {} back.",
            ),
            (
                "__ALICE__ and __ADAM__ talk. She strokes his __HAIR__.",
                "ADAM",
                "She strokes his {}.",
            ),
            (
                "__ADAM__ and __ALICE__ talk. She strokes his __HAIR__.",
                "ADAM",
                "She strokes his {}.",
            ),
        )
        for caption, owner, phrase in cases:
            with self.subTest(caption=caption):
                res = resolve(caption)
                self.assertIn(phrase.format(res.characters[owner]["hair"]), res.text)

    def test_a_pronoun_seven_words_back_is_outside_the_window(self) -> None:
        # Six words before the token count (the sixth is "His")...
        inside = resolve(
            "__ADAM__ and __ALICE__ talk. His one two three four five __HAIR__ is long."
        )
        adam = inside.characters["ADAM"]["hair"]
        self.assertIn(f"four five {adam} is long.", inside.text)
        # ...the seventh does not, so no pronoun decides and the nearest named
        # character (ALICE) owns the token, exactly as with no pronoun at all.
        outside = resolve(
            "__ADAM__ and __ALICE__ talk. "
            "His one two three four five six __HAIR__ is long."
        )
        alice = outside.characters["ALICE"]["hair"]
        self.assertIn(f"five six {alice} is long.", outside.text)

    def test_pronoun_from_the_previous_sentence_is_ignored(self) -> None:
        res = resolve(
            "__ALICE__ meets __ADAM__. Her coat is red. The __HAIR__ is long."
        )
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"The {adam} is long.", res.text)

    def test_pronoun_contraction_counts(self) -> None:
        # ADAM is the nearest named character; "she's" points at ALICE.
        res = resolve("__ALICE__ meets __ADAM__ and she's proud of the __HAIR__.")
        alice = res.characters["ALICE"]["hair"]
        self.assertIn(f"she's proud of the {alice}.", res.text)
        res2 = resolve("__ADAM__ meets __ALICE__ and he's proud of the __HAIR__.")
        adam = res2.characters["ADAM"]["hair"]
        self.assertIn(f"he's proud of the {adam}.", res2.text)
        # A contraction nearer the token beats an earlier plain pronoun.
        res3 = resolve(
            "__ALICE__ meets __ADAM__ and he says she's proud of the __HAIR__."
        )
        alice3 = res3.characters["ALICE"]["hair"]
        self.assertIn(f"she's proud of the {alice3}.", res3.text)

    def test_hair_with_a_masculine_pronoun_and_a_female_only_cast_is_bare(self) -> None:
        res = resolve("__ALICE__ watches while his __HAIR__ blows.")
        self.assertIn("while his hair blows.", res.text)
        # ALICE's own hair stays in her description.
        self.assertIn(res.characters["ALICE"]["hair"], res.text.split("watches")[0])

    def test_body_hair_prefixes_make_the_token_a_bare_word(self) -> None:
        for prefix in (
            "pubic",
            "body",
            "facial",
            "chest",
            "arm",
            "leg",
            "underarm",
            "armpit",
            "stomach",
        ):
            with self.subTest(prefix=prefix):
                res = resolve_forced(f"__ALICE__ shows fine {prefix} __HAIR__.")
                self.assertTrue(
                    res.text.endswith(f"shows fine {prefix} hair."), res.text
                )
                # The bare word does not count as spelling out her hair.
                self.assertIn("copper red hair and an athletic build", res.text)

    def test_body_hair_prefix_match_is_case_insensitive(self) -> None:
        res = resolve("__ALICE__ shows fine Body __HAIR__.")
        self.assertTrue(res.text.endswith("shows fine Body hair."), res.text)

    def test_fused_suffix_makes_the_token_a_bare_word(self) -> None:
        res = resolve_forced("__ALICE__ holds a __HAIR__brush.")
        self.assertEqual(res.text, cap1(ALICE_INLINE) + " holds a hairbrush.")

    def test_fused_vaginal_is_a_bare_word_even_with_a_list(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ attends a __VAGINA__l health talk.", library=library)
        self.assertIn("attends a vaginal health talk.", res.text)
        self.assertEqual(res.warnings, [])
        self.assertNotIn("vagina", res.characters["ALICE"])

    def test_missing_list_makes_the_token_a_bare_word_and_warns(self) -> None:
        res = resolve_forced("__ALICE__ covers her __BREASTS__ with a scarf.")
        self.assertEqual(
            res.text,
            cap1(ALICE_INLINE) + " covers her breasts with a scarf.",
        )
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("breasts", res.warnings[0])

    def test_empty_list_behaves_like_a_missing_one(self) -> None:
        library = make_library({"breasts": ()})
        res = resolve("__ALICE__ covers her __BREASTS__.", library=library)
        self.assertIn("covers her breasts.", res.text)
        self.assertTrue(any("breasts" in w for w in res.warnings))

    def test_a_list_supplies_the_value_and_the_draw_is_per_owner(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        caption = "__ALICE__ and __BELLA__ pose. __BELLA__ covers her __BREASTS__."
        res = resolve(caption, library=library)
        options = PLACEHOLDER_LISTS["breasts"]
        expected = options[draw(cast_key(caption), "BELLA", "breasts", 0, len(options))]
        self.assertEqual(res.characters["BELLA"]["breasts"], expected)
        self.assertIn(f"covers her {expected}.", res.text)
        # ALICE's token-only slot was never referenced, so it is not drawn.
        self.assertNotIn("breasts", res.characters["ALICE"])
        self.assertEqual(res.warnings, [])

    def test_male_token_binds_to_the_male_even_when_a_female_is_nearer(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        caption = "__ADAM__ smiles at __ALICE__ and the chart notes the __PENIS__ note."
        res = resolve(caption, library=library)
        options = PLACEHOLDER_LISTS["penis"]
        expected = options[draw(cast_key(caption), "ADAM", "penis", 0, len(options))]
        self.assertIn(f"the chart notes the {expected} note.", res.text)
        self.assertIn("penis", res.characters["ADAM"])
        self.assertNotIn("penis", res.characters["ALICE"])

    def test_token_with_no_character_of_its_gender_is_bare_and_warns(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ points at the __PENIS__ diagram.", library=library)
        self.assertIn("points at the penis diagram.", res.text)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("penis", res.warnings[0])
        res2 = resolve("__ADAM__ points at the __BREASTS__ diagram.", library=library)
        self.assertIn("points at the breasts diagram.", res2.text)

    def test_typed_caption_with_a_hair_token_gets_a_default_owner(self) -> None:
        res = resolve_forced("A woman brushes her __HAIR__ by the window.")
        self.assertEqual(res.text, "A woman brushes her copper red hair by the window.")
        self.assertEqual(list(res.characters), ["ALICE"])
        self.assertEqual(res.characters["ALICE"]["hair"], "copper red hair")
        self.assertEqual(res.warnings, [])

    def test_default_owner_for_a_masculine_pronoun_is_the_first_male(self) -> None:
        res = resolve_forced("A man brushes his __HAIR__.")
        self.assertEqual(res.text, "A man brushes his copper red hair.")
        self.assertEqual(list(res.characters), ["ADAM"])

    def test_default_owner_for_penis_is_the_first_male(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        caption = "A diagram labelled __PENIS__."
        res = resolve(caption, library=library)
        options = PLACEHOLDER_LISTS["penis"]
        expected = options[draw(cast_key(caption), "ADAM", "penis", 0, len(options))]
        self.assertEqual(res.text, f"A diagram labelled {expected}.")
        self.assertEqual(list(res.characters), ["ADAM"])

    def test_two_default_owners_get_distinct_hair(self) -> None:
        res = resolve("Her __HAIR__ is long. His __HAIR__ is short.")
        self.assertEqual(list(res.characters), ["ALICE", "ADAM"])
        alice = res.characters["ALICE"]["hair"]
        adam = res.characters["ADAM"]["hair"]
        self.assertNotEqual(alice, adam)
        self.assertEqual(res.text, f"Her {alice} is long. His {adam} is short.")

    def test_repeated_hair_tokens_for_one_owner_repeat_the_value(self) -> None:
        res = resolve("Her __HAIR__ is long. She combs her __HAIR__ daily.")
        hair = res.characters["ALICE"]["hair"]
        self.assertEqual(res.text, f"Her {hair} is long. She combs her {hair} daily.")

    def test_default_owner_does_not_appear_when_the_only_token_is_fused(self) -> None:
        res = resolve("She holds a __HAIR__brush.")
        self.assertEqual(res.text, "She holds a hairbrush.")
        self.assertEqual(res.characters, {})

    def test_missing_hair_list_makes_the_token_bare_and_the_handle_plain(self) -> None:
        library = make_library(drop=("hair",))
        res = resolve(
            "__ALICE__ combs her __HAIR__. Later __ALICE__ smiles.", library=library
        )
        self.assertIn("combs her hair.", res.text)
        self.assertIn("Later the woman smiles.", res.text)
        self.assertNotIn("hair", res.characters["ALICE"])
        self.assertTrue(any("hair" in w for w in res.warnings))

    def test_token_owner_defaults_use_the_configured_names(self) -> None:
        config = CharacterConfig(female_names=("ZOE",), male_names=("MAX",))
        library = make_library(config=config)
        res = resolve("Her __HAIR__ is long.", library=library)
        self.assertEqual(list(res.characters), ["ZOE"])


class DrawTests(unittest.TestCase):
    def test_same_inputs_give_identical_results(self) -> None:
        caption = "__ALICE__ meets __ADAM__. Her __HAIR__ is long. __ADAM__ nods."
        for style in IDENTITY_STYLES:
            first = resolve(caption, seed=77, style=style)
            second = resolve(caption, seed=77, style=style)
            self.assertEqual(first, second)

    def test_hair_is_distinct_across_the_cast_over_many_captions(self) -> None:
        for caption in takes("__ALICE__, __BELLA__ and __CLARA__ wave.", 300):
            chars = resolve(caption).characters
            hairs = [chars[n]["hair"] for n in ("ALICE", "BELLA", "CLARA")]
            self.assertEqual(len(set(hairs)), 3, (caption, hairs))

    def test_hair_is_distinct_across_genders_too(self) -> None:
        for caption in takes("__CLARA__ and __ADAM__ wave.", 100):
            chars = resolve(caption).characters
            self.assertNotEqual(chars["CLARA"]["hair"], chars["ADAM"]["hair"], caption)

    def test_too_short_a_hair_list_falls_back_to_noun_handles_and_warns(self) -> None:
        library = make_library({"hair": ("auburn hair", "jet-black hair")})
        caption = (
            "__ALICE__, __BELLA__ and __CLARA__ stand together. __ALICE__ smiles. "
            "__BELLA__ nods. __CLARA__ waves."
        )
        for take in takes(caption, 25):
            res = resolve(take, library=library)
            hair = {n: res.characters[n]["hair"] for n in ("ALICE", "BELLA", "CLARA")}
            self.assertEqual(len(set(hair.values())), 2, take)
            self.assertTrue(any("hair" in w for w in res.warnings), take)
            shared = {v for v in hair.values() if list(hair.values()).count(v) > 1}
            for name, verb in (
                ("ALICE", "smiles"),
                ("BELLA", "nods"),
                ("CLARA", "waves"),
            ):
                if hair[name] in shared:
                    handle = "The woman"
                else:
                    handle = (
                        "The " + hair[name][:-5].replace(" ", "-") + "-haired woman"
                    )
                self.assertIn(f"{handle} {verb}.", res.text, (take, name))

    def test_a_seven_person_cast_with_five_hair_values_never_raises(self) -> None:
        caption = (
            "__ALICE__ __BELLA__ __CLARA__ __DIANNA__ __EMMA__ __ADAM__ __BOB__ "
            "stand in a row."
        )
        for take in takes(caption, 25):
            res = resolve(take)
            self.assertEqual(len(res.characters), 7)
            self.assertTrue(any("hair" in w for w in res.warnings))

    def test_gendered_lists_override_the_plain_list_for_that_gender(self) -> None:
        library = make_library(
            {
                "hair": ("plain hair one", "plain hair two"),
                "hair.male": ("buzz-cut black hair",),
            }
        )
        for take in takes("__ALICE__ and __ADAM__ wave.", 30):
            chars = resolve(take, library=library)
            self.assertIn(
                chars.characters["ALICE"]["hair"], ("plain hair one", "plain hair two")
            )
            self.assertEqual(chars.characters["ADAM"]["hair"], "buzz-cut black hair")

    def test_body_lists_are_split_by_gender(self) -> None:
        for take in takes("__ALICE__ and __ADAM__ wave.", 30):
            chars = resolve(take).characters
            self.assertIn(chars["ALICE"]["body"], LISTS["body.female"])
            self.assertIn(chars["ADAM"]["body"], LISTS["body.male"])

    def test_a_slot_with_a_list_for_one_gender_only_is_omitted_for_the_other(
        self,
    ) -> None:
        library = make_library(drop=("body.male",))
        res = resolve("__ALICE__ and __ADAM__ wave.", library=library)
        self.assertIn("body", res.characters["ALICE"])
        self.assertNotIn("body", res.characters["ADAM"])
        self.assertTrue(any("body" in w for w in res.warnings))

    def test_a_missing_slot_list_is_omitted_and_warned_about_once(self) -> None:
        library = make_library(drop=("eyes",))
        res = resolve("__ALICE__ and __BELLA__ wave.", library=library)
        self.assertNotIn("eyes", res.text)
        self.assertEqual(sum("eyes" in w for w in res.warnings), 1)


class CastKeyTests(unittest.TestCase):
    """A caption's characters are a function of the caption text alone. Every
    draw is keyed on its sha256, never on the seed, so the seed can change (a
    new image of the same scene) without changing who is in it."""

    SLOTS = ("age", "ethnicity", "skin", "eyes", "face", "hair", "body")

    def cast(
        self, caption: str, seed: int = 101, style: str = "ref"
    ) -> Dict[str, Dict[str, str]]:
        return resolve(caption, seed=seed, style=style).characters

    def test_the_seed_does_not_change_the_cast(self) -> None:
        caption = "__ALICE__ meets __ADAM__. Her __HAIR__ is long. __ADAM__ nods."
        expected = resolve(caption, seed=0)
        for seed in (1, 2, 101, 4242, 2**31 - 1, -1, 10**40):
            with self.subTest(seed=seed):
                self.assertEqual(resolve(caption, seed=seed), expected)

    def test_a_different_caption_changes_the_cast(self) -> None:
        casts = {
            tuple(sorted(self.cast(f"__ALICE__ waves {n}.")["ALICE"].items()))
            for n in range(20)
        }
        self.assertGreater(len(casts), 10)

    def test_every_draw_is_keyed_on_the_sha256_of_the_caption(self) -> None:
        # The second caption is untidy on purpose: the text is hashed exactly as
        # given, not trimmed or normalised.
        for caption in (
            "__ALICE__ waves at __CLARA__.",
            "  __ALICE__   waves at __CLARA__.  \r\n\t",
        ):
            key = cast_key(caption)
            cast = self.cast(caption)
            self.assertEqual(list(cast["ALICE"]), list(self.SLOTS))
            for name in ("ALICE", "CLARA"):
                for slot in self.SLOTS:
                    if slot == "hair" and name != "ALICE":
                        continue  # a later character's hair is re-drawn on a clash
                    options = LISTS["body.female" if slot == "body" else slot]
                    expected = options[draw(key, name, slot, 0, len(options))]
                    with self.subTest(caption=caption, name=name, slot=slot):
                        self.assertEqual(cast[name][slot], expected)

    def test_a_default_owner_is_drawn_like_a_named_character(self) -> None:
        caption = "Her __HAIR__ is long."
        cast = self.cast(caption)
        self.assertEqual(list(cast), ["ALICE"])
        for slot in self.SLOTS:
            options = LISTS["body.female" if slot == "body" else slot]
            expected = options[draw(cast_key(caption), "ALICE", slot, 0, len(options))]
            with self.subTest(slot=slot):
                self.assertEqual(cast["ALICE"][slot], expected)

    def test_the_identity_style_is_not_part_of_the_key(self) -> None:
        caption = "__ALICE__ waves at __BELLA__. __ALICE__ smiles."
        casts = [self.cast(caption, style=style) for style in IDENTITY_STYLES]
        self.assertEqual(casts[0], casts[1])
        self.assertEqual(casts[0], casts[2])

    def test_the_seed_still_picks_a_plain_wildcard_but_not_the_cast(self) -> None:
        setting = ("a sunlit garden", "a foggy pier", "a quiet library")
        library = make_library({"setting": setting})
        caption = "__ALICE__ waits in __SETTING__."
        results = [resolve(caption, seed=seed, library=library) for seed in range(20)]
        self.assertEqual(len({str(r.characters) for r in results}), 1)
        self.assertGreater(len({r.text for r in results}), 1)

    def test_a_lone_surrogate_in_the_caption_does_not_raise(self) -> None:
        # Legal in a JSON string, so a request can carry one; the engine never
        # raises on data problems.
        caption = "__ALICE__ waves at a \ud800 sign."
        res = resolve(caption)
        self.assertIn("ALICE", res.characters)
        self.assertIn("\ud800 sign", res.text)
        self.assertEqual(res, resolve(caption))


class PlainWildcardTests(unittest.TestCase):
    SETTING = ("a sunlit garden", "a foggy pier", "a quiet library")

    def test_wildcard_with_a_list_is_seeded_and_consistent(self) -> None:
        library = make_library({"setting": self.SETTING})
        caption = "__ALICE__ waits in __SETTING__. She stays in __SETTING__ until dusk."
        for seed in (0, 1, 101, 999):
            expected = self.SETTING[
                int.from_bytes(
                    hashlib.sha256(f"{seed}|SETTING|wildcard|0".encode()).digest()[:8],
                    "big",
                )
                % len(self.SETTING)
            ]
            text = resolve(caption, seed=seed, library=library).text
            self.assertIn(f"waits in {expected}. She stays in {expected} until", text)

    def test_wildcard_value_is_independent_of_the_cast(self) -> None:
        library = make_library({"setting": self.SETTING})
        for seed in range(20):
            digest = hashlib.sha256(f"{seed}|SETTING|wildcard|0".encode()).digest()
            value = self.SETTING[int.from_bytes(digest[:8], "big") % 3]
            alone = resolve("__SETTING__", seed=seed, library=library).text
            with_cast = resolve(
                "__ALICE__ waves in __SETTING__.", seed=seed, library=library
            ).text
            self.assertEqual(alone, cap1(value))
            self.assertTrue(with_cast.endswith(f"waves in {value}."), seed)

    def test_wildcard_without_a_list_becomes_its_lowercase_word_and_warns(self) -> None:
        res = resolve("__ALICE__ waits in __SETTING__.")
        self.assertTrue(res.text.endswith("waits in setting."), res.text)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("setting", res.warnings[0])

    def test_wildcard_at_a_sentence_start_is_capitalised(self) -> None:
        library = make_library({"setting": self.SETTING})
        text = resolve("__SETTING__ is quiet.", seed=3, library=library).text
        self.assertTrue(text[0].isupper(), text)
        self.assertTrue(text.endswith(" is quiet."), text)

    def test_wildcard_that_is_also_a_slot_name_uses_the_plain_list(self) -> None:
        # ``skin`` is a draw slot but not a token slot: __SKIN__ is a wildcard.
        res = resolve("Her __SKIN__ glows.", seed=5)
        self.assertIn(" glows.", res.text)
        expected_options = LISTS["skin"]
        self.assertTrue(any(v in res.text for v in expected_options), res.text)
        self.assertEqual(res.characters, {})


class SentenceStartTests(unittest.TestCase):
    def test_capitalised_at_the_start_of_the_text(self) -> None:
        self.assertTrue(
            resolve_forced("__ALICE__ waves.").text.startswith("A 31-year-old")
        )

    def test_capitalised_after_sentence_punctuation(self) -> None:
        for lead in ("She waves. ", "Who? ", "Wow! ", "Well\u2026 "):
            with self.subTest(lead=lead):
                text = resolve_forced(lead + "__ALICE__ smiles.").text
                self.assertEqual(text, lead + cap1(ALICE_INLINE) + " smiles.")

    def test_lowercase_mid_sentence(self) -> None:
        for lead in ("Then ", "Scene: ", "Then, ", "Behind "):
            with self.subTest(lead=lead):
                text = resolve_forced(lead + "__ALICE__ smiles.").text
                self.assertEqual(text, lead + ALICE_INLINE + " smiles.")

    def test_capitalised_after_a_line_break(self) -> None:
        text = resolve_forced("Scene one\n__ALICE__ smiles.").text
        self.assertEqual(text, "Scene one\n" + cap1(ALICE_INLINE) + " smiles.")

    def test_capitalised_after_an_opening_quote(self) -> None:
        text = resolve_forced('"__ALICE__ smiles," he says.').text
        self.assertEqual(text, '"' + cap1(ALICE_INLINE) + ' smiles," he says.')
        text = resolve_forced('He says: "Look. __ALICE__ smiles."').text
        self.assertIn('"Look. A 31-year-old', text)

    def test_apostrophes_are_not_sentence_boundaries(self) -> None:
        text = resolve_forced("The girls' __HAIR__ shines.").text
        self.assertTrue(text.startswith("The girls' copper red hair shines"), text)

    def test_later_mentions_are_capitalised_at_a_sentence_start(self) -> None:
        caption = "__ALICE__ waves. __ALICE__ smiles."
        ref = resolve_forced(caption, style="ref").text
        noun = resolve_forced(caption, style="noun").text
        self.assertTrue(ref.endswith(" waves. The copper-red-haired woman smiles."))
        self.assertTrue(noun.endswith(" waves. The woman smiles."))

    def test_characteristic_and_bare_tokens_are_capitalised_at_a_sentence_start(
        self,
    ) -> None:
        self.assertEqual(
            resolve_forced("__HAIR__ falls.").text, "Copper red hair falls."
        )
        res = resolve_forced("__BREASTS__ are covered.")
        self.assertEqual(res.text, "Breasts are covered.")
        self.assertTrue(res.warnings)

    def test_mid_sentence_replacements_are_not_capitalised(self) -> None:
        text = resolve_forced(
            "__ALICE__ waves and __ALICE__ smiles.", style="noun"
        ).text
        self.assertTrue(text.endswith(" waves and the woman smiles."), text)


class EdgeInputTests(unittest.TestCase):
    """Review focus 2: awkward token neighbours, awkward seeds, no exceptions."""

    SEEDS = (0, 2**31 - 1, -1, -(2**31), 2**63, 10**40)

    def test_token_neighbours_never_raise_and_are_deterministic(self) -> None:
        captions = (
            "__ALICE__",
            "__ALICE__,",
            "__ALICE__.",
            "(__ALICE__)",
            '"__ALICE__"',
            "x__ALICE__y",
            "__ALICE__\u2019s",
            "__ALICE__'s",
            "__ALICE__\u2019",
            "__ALICE__\u2026",
            "__ALICE__\n",
            "\n__ALICE__",
            "__ALICE__ __BELLA__",
            "__ALICE__,__BELLA__",
            "__ALICE____BELLA__",
            "__HAIR__",
            "__HAIR__\u2019s",
            "__HAIR__brush",
            "her __HAIR__",
            "his __HAIR__.",
            "____BREASTS____",
            "____ALICE____ waves at ____BELLA____.",
        )
        for caption in captions:
            for seed in self.SEEDS:
                for style in IDENTITY_STYLES:
                    with self.subTest(caption=caption, seed=seed, style=style):
                        first = resolve(caption, seed=seed, style=style)
                        second = resolve(caption, seed=seed, style=style)
                        self.assertEqual(first, second)
                        self.assertNotIn("__ALICE__", first.text)
                        self.assertNotIn("__HAIR__", first.text)

    def test_curly_apostrophe_matches_the_straight_one(self) -> None:
        straight = resolve_forced("__ALICE__'s hat.").text
        curly = resolve_forced("__ALICE__\u2019s hat.").text
        self.assertEqual(curly, straight.replace("'", "\u2019"))

    def test_empty_and_whitespace_only_captions_are_unchanged(self) -> None:
        for caption in ("", " ", "   ", "\n", "\t \n ", "\u00a0"):
            for style in IDENTITY_STYLES:
                res = resolve(caption, style=style)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})
                self.assertEqual(res.warnings, [])

    def test_caption_with_no_tokens_is_unchanged(self) -> None:
        for caption in (
            "A red bicycle leaning against a brick wall at dusk.",
            "  Leading and trailing space.  \n",
            "Line one.\n\nLine two \u2014 with a dash, \u201cquotes\u201d and \u00e9\u00e8.",
        ):
            for style in IDENTITY_STYLES:
                res = resolve(caption, style=style)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})
                self.assertEqual(res.warnings, [])

    def test_extreme_seeds_pick_valid_values_and_leave_the_cast_alone(self) -> None:
        # A plain wildcard is all the seed still draws.
        setting = ("a sunlit garden", "a foggy pier", "a quiet library")
        library = make_library({"setting": setting})
        caption = "__ALICE__ waves in __SETTING__."
        casts = set()
        for seed in self.SEEDS:
            res = resolve(caption, seed=seed, library=library)
            self.assertTrue(any(f"waves in {v}." in res.text for v in setting), seed)
            self.assertEqual(res, resolve(caption, seed=seed, library=library))
            casts.add(str(res.characters))
        self.assertEqual(len(casts), 1)

    def test_negative_and_positive_seeds_are_different_seeds(self) -> None:
        # A long list, so two seeds meeting the same value by chance is unlikely.
        places = tuple(f"place {n}" for n in range(1000))
        library = make_library({"setting": places})
        texts = {
            resolve("__SETTING__", seed=s, library=library).text for s in (-5, 5, -6, 6)
        }
        self.assertEqual(len(texts), 4)

    def test_malformed_underscores_parse_like_the_well_formed_token(self) -> None:
        clean = resolve_forced("__ALICE__ waves. Her __HAIR__ is loose.").text
        for caption in (
            "____ALICE____ waves. Her __HAIR__ is loose.",
            "___ALICE__ waves. Her ___HAIR____ is loose.",
        ):
            with self.subTest(caption=caption):
                self.assertEqual(resolve_forced(caption).text, clean)

    def test_things_that_are_not_tokens_are_left_alone(self) -> None:
        for caption in (
            "_ALICE_ waves.",
            "__alice__ waves.",
            "__Alice__ waves.",
            "__ALICE_ waves.",
            "_ALICE__ waves.",
            "__1ALICE__ waves.",
            "____",
            "__",
            "_____ _____",
            "__ __",
            "snake_case_word and __init_ trailing",
        ):
            with self.subTest(caption=caption):
                res = resolve(caption)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})

    def test_malformed_body_token_with_a_list(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ covers her ____BREASTS____.", library=library)
        value = res.characters["ALICE"]["breasts"]
        self.assertIn(f"covers her {value}.", res.text)

    def test_unknown_style_falls_back_to_ref_with_a_warning(self) -> None:
        caption = "__ALICE__ waves. __ALICE__ smiles."
        res = resolve(caption, style="bogus")
        self.assertEqual(res.text, resolve(caption, style="ref").text)
        self.assertTrue(any("bogus" in w for w in res.warnings))

    def test_an_empty_library_still_resolves_names_to_a_bare_noun(self) -> None:
        library = Library(config=CONFIG, lists={})
        res = resolve("__ALICE__ waves at __ADAM__.", library=library)
        self.assertEqual(res.text, "A woman waves at a man.")
        self.assertEqual(res.characters, {"ALICE": {}, "ADAM": {}})
        self.assertTrue(any("hair" in w for w in res.warnings))

    def test_names_and_nouns_come_from_the_config(self) -> None:
        config = CharacterConfig(
            female_names=("ZOE",),
            male_names=("MAX",),
            noun_female="lady",
            noun_male="gentleman",
        )
        library = make_library(config=config)
        res = resolve("__ZOE__ greets __MAX__.", library=library)
        self.assertIn("lady", res.text)
        self.assertIn("gentleman", res.text)
        self.assertEqual(list(res.characters), ["ZOE", "MAX"])
        # A name that is no longer configured is just a wildcard.
        res2 = resolve("__ALICE__ waves.", library=library)
        self.assertEqual(res2.text, "Alice waves.")
        self.assertTrue(res2.warnings)

    def test_generated_text_never_adds_parentheses(self) -> None:
        captions = (
            "__ADAM__ and __CLARA__ walk along a beach. His short __HAIR__ is damp "
            "and her __HAIR__ is tied back. __ADAM__ carries a surfboard.",
            "A close-up of __ALICE__'s hands as she combs her __HAIR__.",
            "__ALICE__, __BELLA__ and __CLARA__ wave. __BELLA__ smiles.",
        )
        for caption in captions:
            for style in IDENTITY_STYLES:
                for take in takes(caption, 40):
                    text = resolve(take, style=style).text
                    self.assertNotIn("(", text)
                    self.assertNotIn(")", text)

    def test_many_tokens_are_handled_in_linear_time(self) -> None:
        caption = "__ALICE__ waves at the camera. " * 20000
        started = time.perf_counter()
        res = resolve_forced(caption)
        elapsed = time.perf_counter() - started
        self.assertEqual(res.text.count("waves at the camera."), 20000)
        self.assertEqual(res.text.count("The copper-red-haired woman waves"), 19999)
        self.assertLess(elapsed, 5.0)

    def test_a_huge_underscore_run_is_not_quadratic(self) -> None:
        caption = "_" * 300000 + " __ALICE__ waves."
        started = time.perf_counter()
        res = resolve_forced(caption)
        elapsed = time.perf_counter() - started
        self.assertTrue(res.text.endswith(" waves."))
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
