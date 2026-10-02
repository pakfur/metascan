"""Tests for t2i caption directions: snippet loading and build_direction."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

from metascan.core.t2i_captions import CaptionRow, Classification
from metascan.core.t2i_directions import (
    Direction,
    DirectionSettings,
    SnippetCache,
    build_direction,
    load_snippets,
)

SNIPPETS: Dict[str, Tuple[str, ...]] = {
    "act.doggy": ("she is on all fours and he takes her from behind",),
    "act.doggy.pov": ("seen from his point of view, she is on all fours before him",),
    "kissing": ("they kiss deeply",),
    "emotion": ("a soft unguarded smile", "a calm, open expression"),
    "emotion.sensual": ("a flushed, heavy-lidded look of pleasure",),
}
S = DirectionSettings()


def row(
    *,
    act: str = "none-artistic",
    act_p: float = 0.0,
    conflict: bool = False,
    kiss: float = 0.0,
    explicit: float = 1.0,
    partner: str = "none",
    erotic: Optional[float] = 0.1,
    classified: bool = True,
) -> CaptionRow:
    c = Classification(
        emotion="explicit",
        emotion_explicit=explicit,
        kiss=kiss,
        partner=partner,
        act=act,
        act_p=act_p,
        act_conflict=conflict,
        issues=(),
    )
    return CaptionRow(
        id=0,
        caption="x",
        aspect_ratio="1:1",
        nudity=None,
        artistic_quality=None,
        erotic_score=erotic,
        pornographic_score=None,
        males=0,
        females=1,
        clothing=(),
        classification=c if classified else None,
    )


def build(
    r: CaptionRow,
    seed: int = 1,
    mode: str = "uncensored",
    settings: DirectionSettings = S,
    snippets=SNIPPETS,
) -> Optional[Direction]:
    return build_direction(r, seed, mode, settings, snippets)


class BuildTests(unittest.TestCase):
    def test_nothing_without_a_row_a_classification_or_when_disabled(self) -> None:
        self.assertIsNone(build_direction(None, 1, "uncensored", S, SNIPPETS))
        self.assertIsNone(build(row(classified=False)))
        self.assertIsNone(
            build(row(explicit=0.0), settings=DirectionSettings(enabled=False))
        )

    def test_below_every_threshold_gives_an_empty_direction(self) -> None:
        d = build(row(act="doggy", act_p=0.79, kiss=0.79, explicit=0.31))
        assert d is not None
        self.assertEqual((d.text, d.parts, d.warnings), ("", (), ()))

    def test_each_threshold_fires_at_its_boundary(self) -> None:
        d = build(row(act="doggy", act_p=0.80, kiss=0.80, explicit=0.30))
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy", "kissing", "emotion"))

    def test_implicit_emotion_counts_as_missing(self) -> None:
        # emotion "implicit" has a low explicit probability: the snippet fires.
        d = build(row(explicit=0.05))
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))

    def test_parts_become_capitalised_sentences_in_order(self) -> None:
        d = build(
            row(act="doggy", act_p=0.9, kiss=0.9, explicit=0.0, erotic=0.1), seed=3
        )
        assert d is not None
        sentences = d.text.split(". ")
        self.assertEqual(
            sentences[0], "She is on all fours and he takes her from behind"
        )
        self.assertEqual(sentences[1], "They kiss deeply")
        self.assertTrue(d.text.endswith("."))

    def test_a_conflicting_act_is_skipped_unless_configured(self) -> None:
        r = row(act="doggy", act_p=0.9, conflict=True)
        self.assertEqual(build(r).parts, ())
        keep = DirectionSettings(skip_act_on_conflict=False)
        self.assertEqual(build(r, settings=keep).parts, ("act:doggy",))

    def test_none_artistic_and_unclear_never_direct(self) -> None:
        for act in ("none-artistic", "unclear"):
            self.assertEqual(build(row(act=act, act_p=1.0)).parts, ())

    def test_an_uncounted_partner_prefers_the_pov_list(self) -> None:
        d = build(row(act="doggy", act_p=0.9, partner="male"))
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy:pov",))
        self.assertIn("point of view", d.text)
        no_pov = {k: v for k, v in SNIPPETS.items() if k != "act.doggy.pov"}
        d = build(row(act="doggy", act_p=0.9, partner="male"), snippets=no_pov)
        assert d is not None
        self.assertEqual(d.parts, ("act:doggy",))

    def test_high_erotic_score_uses_the_sensual_list_and_falls_back(self) -> None:
        d = build(row(explicit=0.0, erotic=0.6))
        assert d is not None
        self.assertEqual(d.parts, ("emotion:sensual",))
        no_sensual = {k: v for k, v in SNIPPETS.items() if k != "emotion.sensual"}
        d = build(row(explicit=0.0, erotic=0.6), snippets=no_sensual)
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))

    def test_sfw_drops_act_and_kissing_and_the_sensual_list(self) -> None:
        d = build(
            row(act="doggy", act_p=0.99, kiss=0.99, explicit=0.0, erotic=0.9),
            mode="sfw",
        )
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))
        self.assertNotIn("behind", d.text)

    def test_the_snippet_follows_the_seed(self) -> None:
        texts = {build(row(explicit=0.0), seed=s).text for s in range(40)}
        self.assertEqual(len(texts), 2)  # both emotion lines get used
        self.assertEqual(
            build(row(explicit=0.0), seed=7).text, build(row(explicit=0.0), seed=7).text
        )

    def test_a_missing_list_is_a_warning_not_an_error(self) -> None:
        d = build(row(act="cowgirl", act_p=0.9, explicit=0.0))
        assert d is not None
        self.assertEqual(d.parts, ("emotion",))
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("act.cowgirl.txt", d.warnings[0])


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_lines_are_screened_and_parentheses_rejected(self) -> None:
        (self.dir / "emotion.txt").write_text(
            "# comment\n\na warm smile\na schoolgirl grin\na grin (wide)\n",
            encoding="utf-8",
        )
        snippets, warnings = load_snippets(self.dir)
        self.assertEqual(snippets["emotion"], ("a warm smile",))
        self.assertEqual(len(warnings), 2)

    def test_the_cache_reloads_on_change_and_never_raises(self) -> None:
        path = self.dir / "kissing.txt"
        path.write_text("they kiss\n", encoding="utf-8")
        cache = SnippetCache(self.dir)
        self.assertEqual(cache.get()[0]["kissing"], ("they kiss",))
        path.write_text("they kiss softly\n", encoding="utf-8")
        later = time.time() + 5
        os.utime(path, (later, later))
        self.assertEqual(cache.get()[0]["kissing"], ("they kiss softly",))
        missing = SnippetCache(self.dir / "nope")
        self.assertEqual(missing.get(), ({}, []))
