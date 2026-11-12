"""Tests for the t2i wildcard loader (spec sections 3.6 and 3.7).

The guard tests write their own small list files with hand-written lines.
The last classes load a copy of the starter files that ship in
``data/t2i_captions/`` (``characters.yml`` and ``person_appearance.yaml``;
never the real caption CSV, never user lists).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import yaml

from metascan.core import t2i_wildcards
from metascan.core.t2i_characters import CharacterConfig, Library, resolve_caption
from metascan.core.t2i_wildcards import (
    MINOR_TERMS,
    LibraryCache,
    default_library_config,
    load_library,
)

SHIPPED_DIR = Path(__file__).resolve().parents[1] / "data" / "t2i_captions"
STARTER_FILES = ("characters.yml", "person_appearance.yaml")
APPEARANCE = "person_appearance.yaml"
# The per-slot lists person_appearance.yaml replaced: they must not come back.
RETIRED_LISTS = (
    "age.txt",
    "ethnicity.txt",
    "skin.txt",
    "eyes.txt",
    "face.txt",
    "hair.txt",
    "body.female.txt",
    "body.male.txt",
)


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def later(self, name: str) -> None:
        """Push a file's mtime forward so a same-size rewrite is visible."""
        path = self.dir / name
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


class ConstantsTests(unittest.TestCase):
    def test_minor_terms_are_the_spec_deny_list(self) -> None:
        self.assertEqual(
            MINOR_TERMS,
            (
                "teen",
                "teenage",
                "teenager",
                "underage",
                "minor",
                "child",
                "kid",
                "preteen",
                "juvenile",
                "schoolgirl",
                "schoolboy",
                "loli",
                "shota",
                "under 18",
                "under eighteen",
            ),
        )

    def test_default_library_config_is_the_engine_default(self) -> None:
        self.assertEqual(default_library_config(), CharacterConfig())


class ListFileTests(TempDirCase):
    def test_lists_are_keyed_by_lowercase_file_stem(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        self.write("Eyes.txt", "green eyes\n")
        self.write("body.female.txt", "a slim build\n")
        self.write("body.male.txt", "a lean build\n")
        self.write("setting.txt", "a garden\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(
            sorted(library.lists),
            ["body.female", "body.male", "eyes", "hair", "setting"],
        )
        self.assertEqual(library.lists["body.female"], ("a slim build",))
        self.assertEqual(warnings, [])

    def test_blank_lines_comments_bom_crlf_and_dedupe(self) -> None:
        (self.dir / "hair.txt").write_bytes(
            b"\xef\xbb\xbf# starter list\r\n"
            b"auburn hair\r\n"
            b"\r\n"
            b"  Jet-Black    Hair  \r\n"
            b"AUBURN HAIR\r\n"
            b"   # an indented comment\r\n"
            b"jet-black hair\r\n"
            b"black hair"
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["hair"], ("auburn hair", "Jet-Black Hair", "black hair")
        )
        self.assertEqual(warnings, [])

    def test_file_order_is_preserved(self) -> None:
        self.write("eyes.txt", "zebra eyes\nalpha eyes\nmiddle eyes\n")
        library, _ = load_library(self.dir)
        self.assertEqual(
            library.lists["eyes"], ("zebra eyes", "alpha eyes", "middle eyes")
        )

    def test_bad_file_names_and_non_list_files_are_handled(self) -> None:
        self.write("Eye Color.txt", "green eyes\n")
        self.write("notes.md", "not a list\n")
        (self.dir / "folder.txt").mkdir()
        self.write("hair.txt", "auburn hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(sorted(library.lists), ["hair"])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("Eye Color.txt: "))

    def test_undecodable_file_is_reported_not_raised(self) -> None:
        (self.dir / "hair.txt").write_bytes(b"auburn hair\n\xff\xfe\x00bad\n")
        library, warnings = load_library(self.dir)
        self.assertNotIn("hair", library.lists)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("hair.txt: "))

    def test_a_file_with_no_usable_lines_is_reported(self) -> None:
        self.write("eyes.txt", "# nothing here yet\n\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["eyes"], ())
        self.assertEqual(warnings, ["eyes.txt: no usable lines"])

    def test_empty_directory_and_missing_directory(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(warnings, [])
        library, warnings = load_library(self.dir / "does-not-exist")
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("not found", warnings[0])


class AdultOnlyGuardTests(TempDirCase):
    def test_age_lines_with_an_integer_under_18_are_rejected(self) -> None:
        self.write(
            "age.txt",
            "27-year-old\n17-year-old\n18-year-old\n5-year-old\nin her 20s\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("27-year-old", "18-year-old", "in her 20s")
        )
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("age.txt:2: "), warnings[0])
        self.assertTrue(warnings[1].startswith("age.txt:4: "), warnings[1])
        self.assertIn("under 18", warnings[0])

    def test_any_integer_on_an_age_line_counts(self) -> None:
        self.write("age.txt", "30-year-old, 12 freckles\n30-year-old\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["age"], ("30-year-old",))
        self.assertTrue(warnings[0].startswith("age.txt:1: "))

    def test_gendered_age_files_are_guarded_too(self) -> None:
        self.write("age.female.txt", "16-year-old\n25-year-old\n")
        self.write("age.male.txt", "14-year-old\n26-year-old\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["age.female"], ("25-year-old",))
        self.assertEqual(library.lists["age.male"], ("26-year-old",))
        self.assertEqual(len(warnings), 2)

    def test_leading_zeros_and_huge_numbers_do_not_raise(self) -> None:
        self.write(
            "age.txt",
            "0018-year-old\n00017-year-old\n" + "9" * 6000 + "-year-old\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("0018-year-old", "9" * 6000 + "-year-old")
        )
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("age.txt:2: "))

    def test_spelled_out_ages_under_18_are_rejected_in_age_lists(self) -> None:
        self.write(
            "age.txt",
            "seventeen-year-old\nSixteen year old\nthirteen-year-old\n"
            "twenty-one-year-old\neighteen-year-old\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("twenty-one-year-old", "eighteen-year-old")
        )
        self.assertEqual(len(warnings), 3)

    def test_small_numbers_are_fine_outside_the_age_lists(self) -> None:
        self.write("hair.txt", "3-strand braided hair\nauburn hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["hair"], ("3-strand braided hair", "auburn hair")
        )
        self.assertEqual(warnings, [])

    def test_each_minor_term_is_rejected_in_any_list_case_insensitively(self) -> None:
        for term in MINOR_TERMS:
            for variant in (term, term.upper(), term.title()):
                with self.subTest(term=term, variant=variant):
                    self.write(
                        "hair.txt", f"auburn hair\nlong {variant} example hair\n"
                    )
                    library, warnings = load_library(self.dir)
                    self.assertEqual(library.lists["hair"], ("auburn hair",))
                    self.assertEqual(len(warnings), 1)
                    self.assertTrue(warnings[0].startswith("hair.txt:2: "), warnings[0])

    def test_minor_terms_are_rejected_in_every_kind_of_list(self) -> None:
        for name in ("eyes.txt", "body.female.txt", "setting.txt", "age.txt"):
            with self.subTest(name=name):
                self.write(name, "a fine value\nsomething teen something\n")
                library, warnings = load_library(self.dir)
                key = name[:-4]
                self.assertEqual(library.lists[key], ("a fine value",))
                self.assertTrue(
                    any(w.startswith(f"{name}:2: ") for w in warnings), warnings
                )

    def test_plural_and_irregular_forms_are_rejected(self) -> None:
        for phrase in (
            "in her teens",
            "two kids",
            "several minors",
            "the children",
            "some schoolgirls",
            "juveniles",
            "under-18 look",
        ):
            with self.subTest(phrase=phrase):
                self.write("face.txt", f"a soft face\n{phrase}\n")
                library, _ = load_library(self.dir)
                self.assertEqual(library.lists["face"], ("a soft face",))

    def test_words_that_merely_contain_a_term_are_accepted(self) -> None:
        values = (
            "canteen green eyes",
            "kidney red hair",
            "minority language",
            "kidskin gloves",
            "in between shades",
            "nineteen freckles",
            "sixteen braids",
        )
        self.write("eyes.txt", "\n".join(values) + "\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["eyes"], values)
        self.assertEqual(warnings, [])

    def test_the_warning_names_the_term_but_not_the_whole_line(self) -> None:
        self.write("hair.txt", "auburn hair\nsecret words teen more secret words\n")
        _, warnings = load_library(self.dir)
        self.assertIn("teen", warnings[0])
        self.assertNotIn("secret", warnings[0])

    def test_rejections_are_logged_at_warning_level(self) -> None:
        self.write("hair.txt", "auburn hair\nteen hair\n")
        with self.assertLogs("metascan.core.t2i_wildcards", level="WARNING") as logs:
            load_library(self.dir)
        self.assertTrue(any("hair.txt:2" in line for line in logs.output), logs.output)

    def test_parentheses_are_rejected_in_every_list(self) -> None:
        self.write("hair.txt", "auburn hair\nauburn hair (dyed)\n(auburn:1.2) hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["hair"], ("auburn hair",))
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("hair.txt:2: "))
        self.assertIn("parenthes", warnings[0])

    def test_line_numbers_count_every_physical_line_in_crlf_files(self) -> None:
        (self.dir / "hair.txt").write_bytes(b"one hair\r\n\r\n# c\r\ntwo teen hair\r\n")
        _, warnings = load_library(self.dir)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("hair.txt:4: "), warnings[0])

    # -- the guard is a screen for ordinary phrasing, not a whitelist of
    # -- the spec's exact words (whole-branch review, finding 1)

    def test_every_spelled_out_age_under_18_is_rejected_in_age_lists(self) -> None:
        rejected = (
            "twelve-year-old",
            "a ten year old",
            "nine years old",
            "eleven",
            "Zero years old",
            "seven-year-old",
            "seventeen",
            "one year old",
        )
        accepted = (
            "eighteen-year-old",
            "nineteen",
            "twenty-one-year-old",
            "twenty one years old",
            "thirty-six-year-old",
            "forty five years old",
            "ninety-year-old",
            "twenty-seven-year-old",
        )
        self.write("age.txt", "\n".join(rejected + accepted) + "\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["age"], accepted)
        self.assertEqual(len(warnings), len(rejected), warnings)
        self.assertTrue(all("age under 18" in w for w in warnings), warnings)

    def test_minor_word_families_are_rejected_in_every_list(self) -> None:
        values = (
            "a teenaged figure",
            "lolita style",
            "a lolicon look",
            "shotacon",
            "childlike face",
            "a childish grin",
            "school girl look",
            "school-boy haircut",
            "an adolescent build",
            "prepubescent frame",
            "pubescent",
            "a tween look",
            "toddler proportions",
            "an infant face",
            "jailbait",
            "under18",
            "under age",
            "a kiddo grin",
            "little girl energy",
            "young boy features",
            "pre-teen",
            "pre teen",
        )
        for name in ("face.txt", "body.female.txt"):
            with self.subTest(name=name):
                self.write(name, "a fine value\n" + "\n".join(values) + "\n")
                library, warnings = load_library(self.dir)
                self.assertEqual(library.lists[name[:-4]], ("a fine value",))
                self.assertEqual(len(warnings), len(values), warnings)
                (self.dir / name).unlink()  # one file per pass

    def test_an_age_under_18_is_rejected_in_every_list(self) -> None:
        rejected = (
            "a 14-year-old body",
            "a 9 yo build",
            "15 years old look",
            "aged 12 frame",
            "body at age 16",
            "twelve-year-old",
            "a thirteen year old",
        )
        accepted = (
            "an 18-year-old look",
            "a 25 years old look",
            "14-karat gold hair",
            "3-strand braided hair",
            "a twenty-one-year-old look",
            "in between shades",
        )
        self.write("body.txt", "\n".join(rejected + accepted) + "\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["body"], accepted)
        self.assertEqual(len(warnings), len(rejected), warnings)


class CharactersYmlTests(TempDirCase):
    def test_missing_file_means_defaults_and_no_warning(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(warnings, [])

    def test_empty_or_comment_only_file_means_defaults(self) -> None:
        for text in ("", "# just a comment\n", "\n\n"):
            with self.subTest(text=text):
                self.write("characters.yml", text)
                library, warnings = load_library(self.dir)
                self.assertEqual(library.config, CharacterConfig())
                self.assertEqual(warnings, [])

    def test_malformed_yaml_means_defaults_and_one_warning(self) -> None:
        self.write("characters.yml", "names: [ALICE\nnouns: : :\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("characters.yml: "), warnings[0])

    def test_a_top_level_list_is_rejected(self) -> None:
        self.write("characters.yml", "- ALICE\n- BELLA\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(len(warnings), 1)

    def test_valid_overrides_are_applied(self) -> None:
        self.write(
            "characters.yml",
            "names:\n  female: [zoe, Mia]\n  male: [Max]\n"
            "nouns: {female: lady, male: gentleman}\n"
            "token_slots: [hair, chest]\n"
            "token_gender: {chest: male}\n"
            "body_hair_prefixes: [Pubic, Facial]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(
            library.config,
            CharacterConfig(
                female_names=("ZOE", "MIA"),
                male_names=("MAX",),
                noun_female="lady",
                noun_male="gentleman",
                token_slots=("hair", "chest"),
                token_gender=(("chest", "male"),),
                body_hair_prefixes=("pubic", "facial"),
            ),
        )

    def test_partial_file_keeps_defaults_for_the_rest(self) -> None:
        self.write("characters.yml", "nouns: {female: lady}\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(library.config, CharacterConfig(noun_female="lady"))

    def test_bad_fields_warn_and_keep_the_other_fields(self) -> None:
        self.write(
            "characters.yml",
            "names: 5\nnouns: {female: lady, other: x}\ntoken_slots: []\n"
            "token_gender: {breasts: neither, penis: male}\nmystery: 1\n",
        )
        library, warnings = load_library(self.dir)
        config = library.config
        self.assertEqual(config.female_names, CharacterConfig().female_names)
        self.assertEqual(config.noun_female, "lady")
        self.assertEqual(config.token_slots, CharacterConfig().token_slots)
        self.assertEqual(config.token_gender, (("penis", "male"),))
        text = "\n".join(warnings)
        for needle in ("names", "nouns.other", "token_slots", "breasts", "mystery"):
            self.assertIn(needle, text)

    def test_names_are_uppercased_validated_and_deduplicated(self) -> None:
        self.write(
            "characters.yml",
            "names:\n  female: [alice, ALICE, 'bad name', 7, yes, Bella2]\n"
            "  male: [Alice, bob]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config.female_names, ("ALICE", "BELLA2"))
        self.assertEqual(library.config.male_names, ("BOB",))
        text = "\n".join(warnings)
        self.assertIn("bad name", text)
        self.assertIn("7", text)
        self.assertIn("ALICE", text)  # the female/male clash

    def test_nouns_go_through_the_adult_guard(self) -> None:
        self.write(
            "characters.yml",
            "nouns:\n  female: teen girl\n  male: 'man (tall)'\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config.noun_female, CharacterConfig().noun_female)
        self.assertEqual(library.config.noun_male, CharacterConfig().noun_male)
        text = "\n".join(warnings)
        self.assertIn("nouns.female", text)
        self.assertIn("nouns.male", text)
        self.assertIn("minor term", text)
        self.assertIn("parenthes", text)

    def test_names_go_through_the_adult_guard(self) -> None:
        self.write(
            "characters.yml", "names:\n  female: [ZOE, LOLITA, KIDDO]\n  male: [MAX]\n"
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config.female_names, ("ZOE",))
        self.assertEqual(library.config.male_names, ("MAX",))
        text = "\n".join(warnings)
        self.assertIn("LOLITA", text)
        self.assertIn("KIDDO", text)

    def test_the_old_layout_keys_say_where_they_moved(self) -> None:
        self.write(
            "characters.yml",
            "slots: [age, hair]\nintro:\n  head: [age]\n  with: [hair]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(len(warnings), 2)
        for warning in warnings:
            self.assertIn("moved to person_appearance.yaml", warning)

    def test_the_config_reaches_the_engine(self) -> None:
        self.write("characters.yml", "names: {female: [ZOE], male: [MAX]}\n")
        self.write("hair.txt", "auburn hair\n")
        library, _ = load_library(self.dir)
        result = resolve_caption("__ZOE__ waves.", 5, "ref", library)
        self.assertEqual(list(result.characters), ["ZOE"])
        self.assertTrue(result.text.startswith("A woman"))


class LibraryCacheTests(TempDirCase):
    def test_first_get_loads_and_unchanged_files_are_not_reloaded(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        first, _ = cache.get()
        second, _ = cache.get()
        self.assertEqual(first.lists["hair"], ("auburn hair",))
        self.assertIs(first, second)

    def test_reloads_after_a_size_change(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("hair.txt", "auburn hair\njet-black hair\n")
        library, _ = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair", "jet-black hair"))

    def test_reloads_after_a_mtime_only_change(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("hair.txt", "copper hair\n")  # same size, different text
        self.later("hair.txt")
        library, _ = cache.get()
        self.assertEqual(library.lists["hair"], ("copper hair",))

    def test_reloads_after_a_new_file_appears_and_after_one_is_removed(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("eyes.txt", "green eyes\n")
        library, _ = cache.get()
        self.assertEqual(sorted(library.lists), ["eyes", "hair"])
        (self.dir / "eyes.txt").unlink()
        library, _ = cache.get()
        self.assertEqual(sorted(library.lists), ["hair"])

    def test_reloads_after_characters_yml_changes(self) -> None:
        cache = LibraryCache(self.dir)
        library, _ = cache.get()
        self.assertEqual(library.config, CharacterConfig())
        self.write("characters.yml", "nouns: {female: lady}\n")
        library, _ = cache.get()
        self.assertEqual(library.config.noun_female, "lady")

    def test_missing_directory_is_a_warning_not_an_error(self) -> None:
        cache = LibraryCache(self.dir / "later")
        library, warnings = cache.get()
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(len(warnings), 1)
        (self.dir / "later").mkdir()
        (self.dir / "later" / "hair.txt").write_text("auburn hair\n", encoding="utf-8")
        library, warnings = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair",))
        self.assertEqual(warnings, [])

    def test_get_never_raises_and_keeps_the_last_good_library(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        good, _ = cache.get()
        self.write("hair.txt", "auburn hair\nblack hair\n")
        with mock.patch.object(
            t2i_wildcards, "load_library", side_effect=RuntimeError("boom")
        ):
            library, warnings = cache.get()
        self.assertIs(library, good)
        self.assertTrue(any("boom" in w for w in warnings))
        # The failed reload is retried on the next call.
        library, warnings = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair", "black hair"))
        self.assertEqual(warnings, [])

    def test_returned_warnings_are_copies(self) -> None:
        self.write("hair.txt", "auburn hair\nteen hair\n")
        cache = LibraryCache(self.dir)
        _, warnings = cache.get()
        self.assertEqual(len(warnings), 1)
        warnings.append("scribble")
        _, again = cache.get()
        self.assertEqual(len(again), 1)

    def test_concurrent_gets_are_safe(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        results: List[Library] = []
        errors: List[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(40):
                    results.append(cache.get()[0])
            except BaseException as exc:  # pragma: no cover - would fail the test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 320)
        self.assertEqual(len({id(library) for library in results}), 1)


def shipped_appearance() -> Dict[str, Any]:
    data = yaml.safe_load((SHIPPED_DIR / APPEARANCE).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def many_captions(count: int) -> List[str]:
    return [
        f"__ALICE__, __BELLA__, __CLARA__ and __ADAM__ pose by stall {i}."
        for i in range(count)
    ]


class AppearanceFileTests(TempDirCase):
    """person_appearance.yaml: loading, the adult-only screen and reloads.
    Each test writes a copy of the shipped file, edited where it says so."""

    def write_appearance(self, data: Dict[str, Any]) -> None:
        self.write(APPEARANCE, yaml.safe_dump(data, sort_keys=False))

    def test_a_missing_file_is_no_load_warning_but_a_resolve_warning(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertIsNone(library.appearance)
        self.assertEqual(warnings, [])
        result = resolve_caption("__ALICE__ waves.", 1, "ref", library)
        self.assertEqual(result.text, "A woman waves.")
        self.assertTrue(any(APPEARANCE in w for w in result.warnings))

    def test_malformed_or_invalid_yaml_is_one_warning_and_no_people(self) -> None:
        broken = shipped_appearance()
        broken["attributes"]["hair_color"]["mult"] = None
        broken["attributes"]["eye_color"]["values"]["blue"]["mult"] = {
            "nonsense": {"x": 2}
        }
        for text in ("attributes: [", yaml.safe_dump(broken)):
            with self.subTest(text=text[:20]):
                self.write(APPEARANCE, text)
                library, warnings = load_library(self.dir)
                self.assertIsNone(library.appearance)
                self.assertEqual(len(warnings), 1, warnings)
                self.assertTrue(warnings[0].startswith(APPEARANCE), warnings[0])

    def test_a_rejected_text_is_dropped_and_a_value_left_without_text_is_never_drawn(
        self,
    ) -> None:
        data = shipped_appearance()
        body = data["attributes"]["body_type"]["values"]
        body["slim"] = {"weight": 10_000, "text": ["a teenaged figure", "a slim build"]}
        body["average"] = {"weight": 10_000, "text": "a schoolgirl figure"}
        self.write_appearance(data)
        library, warnings = load_library(self.dir)
        self.assertIsNotNone(library.appearance)
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(all("rejected" in w for w in warnings))
        for caption in many_captions(60):
            result = resolve_caption(caption, 1, "ref", library)
            self.assertNotRegex(result.text.lower(), r"teen|school")
            self.assertIn("a slim build", result.text)

    def test_an_age_range_under_18_disables_its_value(self) -> None:
        data = shipped_appearance()
        ages = data["attributes"]["apparent_age"]["values"]
        ages["early_20s"] = {
            "weight": 10_000,
            "text": "early twenties",
            "years": [16, 22],
        }
        self.write_appearance(data)
        library, warnings = load_library(self.dir)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("age under 18", warnings[0])
        for caption in many_captions(60):
            for drawn in resolve_caption(
                caption, 1, "ref", library
            ).characters.values():
                self.assertGreaterEqual(int(drawn["age"][:2]), 21, drawn["age"])

    def test_a_compose_or_engine_text_that_fails_the_screen_refuses_the_file(
        self,
    ) -> None:
        for where in ("compose", "engine"):
            data = shipped_appearance()
            if where == "compose":
                data["compose"]["eyes"] = [{"text": "{eye_color} childlike eyes"}]
            else:
                data["engine"]["slots"]["eyes"] = "{eyes} (detailed)"
            with self.subTest(where=where):
                self.write_appearance(data)
                library, warnings = load_library(self.dir)
                self.assertIsNone(library.appearance)
                self.assertEqual(len(warnings), 1, warnings)

    def test_the_cache_keeps_the_last_good_appearance_when_an_edit_breaks_it(
        self,
    ) -> None:
        self.write_appearance(shipped_appearance())
        cache = LibraryCache(self.dir)
        good, _ = cache.get()
        self.assertIsNotNone(good.appearance)
        self.write(APPEARANCE, "attributes: [")
        library, warnings = cache.get()
        self.assertIs(library.appearance, good.appearance)
        self.assertTrue(any("keeping the last version" in w for w in warnings))
        # Fixed again: the new file is read.
        data = shipped_appearance()
        data["engine"]["head"] = ["ethnicity"]
        self.write_appearance(data)
        library, warnings = cache.get()
        self.assertEqual(warnings, [])
        self.assertEqual(library.appearance.engine.head, ("ethnicity",))


class GuardEndToEndTests(TempDirCase):
    """Ordinary careless edits must not reach a resolved caption.

    The starters plus one bad text each in the appearance's ages and body
    types and the ``characters.yml`` noun: every one is reported, none is
    ever drawn.
    """

    def test_no_minor_cue_survives_into_a_resolved_caption(self) -> None:
        data = shipped_appearance()
        data["attributes"]["apparent_age"]["values"]["kid"] = {
            "weight": 10_000,
            "text": "early twenties",
            "years": [12, 14],
        }
        data["attributes"]["body_type"]["values"]["lean"]["text"] = "a teenaged figure"
        data["attributes"]["body_type"]["values"]["lean"]["weight"] = 10_000
        self.write(APPEARANCE, yaml.safe_dump(data, sort_keys=False))
        self.write("characters.yml", "nouns:\n  female: 'teen girl (petite)'\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(len(warnings), 3, warnings)
        for caption in many_captions(100):
            for style in ("ref", "noun", "name"):
                text = resolve_caption(caption, 1, style, library).text.lower()
                self.assertNotRegex(
                    text, r"twelve|teen|\b1[0-7]-year|[()]", (caption, style, text)
                )


class ShippedStarterTests(TempDirCase):
    """The tracked starter data must load cleanly and satisfy the content rules."""

    def setUp(self) -> None:
        super().setUp()
        for name in STARTER_FILES:
            shutil.copy(SHIPPED_DIR / name, self.dir / name)

    def test_the_starter_files_exist_and_the_old_lists_are_gone(self) -> None:
        for name in STARTER_FILES:
            self.assertTrue((SHIPPED_DIR / name).is_file(), name)
        for name in RETIRED_LISTS:
            self.assertFalse((SHIPPED_DIR / name).exists(), name)

    def test_load_library_reports_no_warnings(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(library.config, default_library_config())
        self.assertIsNotNone(library.appearance)
        self.assertEqual(dict(library.lists), {})

    def test_every_age_is_adult(self) -> None:
        values = shipped_appearance()["attributes"]["apparent_age"]["values"]
        for vid, value in values.items():
            with self.subTest(value=vid):
                self.assertGreaterEqual(min(value["years"]), 21)

    def test_drawn_slots_are_plain_adult_phrases(self) -> None:
        library, _ = load_library(self.dir)
        banned = re.compile(
            r"\b(breast|breasts|vagina|vulva|penis|genital|genitals|nipple|nipples"
            r"|anus|buttock|buttocks|nude|naked)\b",
            re.IGNORECASE,
        )
        for caption in many_captions(150):
            result = resolve_caption(caption, 1, "ref", library)
            for name, drawn in result.characters.items():
                for slot, value in drawn.items():
                    with self.subTest(caption=caption, name=name, slot=slot):
                        self.assertEqual(value, value.strip())
                        self.assertNotRegex(value, r"[();:.{}]|__")
                        self.assertIsNone(banned.search(value), value)
                        self.assertIsNone(t2i_wildcards._reject_reason(slot, value))
                self.assertRegex(drawn["age"], r"^\d\d-year-old$")
                if "hair" in drawn:
                    self.assertTrue(drawn["hair"].endswith(" hair"), drawn["hair"])

    def test_a_name_fixes_the_gender_it_draws(self) -> None:
        library, _ = load_library(self.dir)
        for caption in many_captions(40):
            result = resolve_caption(caption, 1, "ref", library)
            for name in ("ALICE", "BELLA", "CLARA"):
                self.assertEqual(result.attributes[name]["gender"], "woman")
            self.assertEqual(result.attributes["ADAM"]["gender"], "man")

    def test_resolve_caption_smoke_against_the_starters(self) -> None:
        library, _ = load_library(self.dir)
        result = resolve_caption(
            "__ALICE__ meets __ADAM__. Her long __HAIR__ is loose.", 7, "ref", library
        )
        self.assertEqual(result.warnings, [])
        self.assertIn("-year-old", result.text)
        self.assertNotIn("__", result.text)
        self.assertNotIn("{", result.text)
        alice = result.characters["ALICE"]
        self.assertIn(f"Her long {alice['hair']} is loose.", result.text)
        # Her hair is spelled out, so her drawn style is not described too.
        if "hairstyle" in alice:
            self.assertNotIn(alice["hairstyle"], result.text)

    def test_the_cast_keeps_its_hair_apart(self) -> None:
        library, _ = load_library(self.dir)
        clashes = 0
        captions = many_captions(300)
        for caption in captions:
            result = resolve_caption(caption, 1, "ref", library)
            hair = [d.get("hair") for d in result.characters.values() if d.get("hair")]
            clashes += len(hair) != len(set(hair))
            self.assertNotIn("(", result.text)
        self.assertLessEqual(clashes, len(captions) // 50)

    def test_the_cast_depends_on_the_caption_alone(self) -> None:
        library, _ = load_library(self.dir)
        caption = "__ALICE__ meets __ADAM__ in a cafe."
        first = resolve_caption(caption, 0, "ref", library)
        for seed in (1, 99, 2**31 - 1):
            again = resolve_caption(caption, seed, "ref", library)
            self.assertEqual(again.characters, first.characters)
            self.assertEqual(again.attributes, first.attributes)
            self.assertEqual(again.text, first.text)
        other = resolve_caption(caption + " ", 0, "ref", library)
        self.assertNotEqual(other.characters, first.characters)


if __name__ == "__main__":
    unittest.main()
