"""Tests for the classification columns of the caption CSV and CaptionStore.find.

Every CSV is written here from hand-written rows -- never the real file.
"""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from metascan.core.t2i_captions import CaptionStore, Classification

BASE = ["Caption", "Aspect Ratio", "Erotic Score"]
CLASS = [
    "Caption SHA1",
    "Emotion",
    "Emotion Explicit",
    "Kiss",
    "Partner",
    "Act",
    "Act P",
    "Act Conflict",
    "Issues",
]


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def classified(caption: str, act: str = "doggy", digest: str = "") -> List[str]:
    return [
        caption,
        "1:1",
        "0.7",
        digest or sha(caption),
        "none",
        "0.1000",
        "0.8500",
        "male",
        act,
        "0.9000",
        "false",
        "extra_limb;gaze_conflict",
    ]


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "c.csv"

    def write(self, header: List[str], rows: List[List[str]]) -> CaptionStore:
        with self.path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        return CaptionStore(self.path)


class ClassificationTests(Case):
    def test_a_classified_row_carries_its_values(self) -> None:
        store = self.write(BASE + CLASS, [classified("__ALICE__ kneels.")])
        c = store.get(0).classification
        self.assertEqual(
            c,
            Classification(
                emotion="none",
                emotion_explicit=0.1,
                kiss=0.85,
                partner="male",
                act="doggy",
                act_p=0.9,
                act_conflict=False,
                issues=("extra_limb", "gaze_conflict"),
            ),
        )

    def test_blank_cells_mean_no_classification(self) -> None:
        store = self.write(BASE + CLASS, [["__ALICE__ sits.", "1:1", "0.2"] + [""] * 9])
        self.assertIsNone(store.get(0).classification)

    def test_a_caption_edited_after_the_merge_loses_its_classification(self) -> None:
        row = classified("__ALICE__ kneels.", digest=sha("__ALICE__ kneeled."))
        store = self.write(BASE + CLASS, [row])
        self.assertIsNone(store.get(0).classification)

    def test_an_unusable_number_means_no_classification(self) -> None:
        row = classified("__ALICE__ kneels.")
        row[len(BASE) + 2] = "lots"  # Emotion Explicit
        store = self.write(BASE + CLASS, [row])
        self.assertIsNone(store.get(0).classification)

    def test_a_csv_without_the_columns_works_as_before(self) -> None:
        store = self.write(BASE, [["__ALICE__ sits.", "1:1", "0.2"]])
        row = store.get(0)
        self.assertIsNone(row.classification)
        self.assertEqual(row.erotic_score, 0.2)
        self.assertNotIn("classification", row.to_dict())


class FindTests(Case):
    def test_find_returns_the_row_with_exactly_that_text(self) -> None:
        store = self.write(
            BASE + CLASS, [classified("A."), classified("B.", act="cowgirl")]
        )
        row = store.find("B.")
        assert row is not None
        self.assertEqual((row.id, row.classification.act), (1, "cowgirl"))
        self.assertIsNone(store.find("B. "))  # exact text only
        self.assertIsNone(store.find("C."))

    def test_duplicate_captions_find_the_first(self) -> None:
        store = self.write(
            BASE,
            [
                ["Same.", "1:1", "0.1"],
                ["Other.", "1:1", "0.1"],
                ["Same.", "1:1", "0.1"],
            ],
        )
        row = store.find("Same.")
        assert row is not None
        self.assertEqual(row.id, 0)

    def test_a_hash_prefix_collision_never_returns_other_text(self) -> None:
        store = self.write(BASE, [["Same.", "1:1", "0.1"]])
        store.find("Same.")  # build the index
        with mock.patch(
            "metascan.core.t2i_captions._caption_key", return_value=b"\x00" * 8
        ):
            index = store._current()
            assert index is not None
            index.by_hash[b"\x00" * 8] = 0
            self.assertIsNone(store.find("Different."))

    def test_find_follows_a_rewritten_file(self) -> None:
        store = self.write(BASE, [["Old.", "1:1", "0.1"]])
        self.assertIsNotNone(store.find("Old."))
        self.write(BASE, [["Newer caption.", "1:1", "0.1"]])
        self.assertIsNone(store.find("Old."))
        self.assertIsNotNone(store.find("Newer caption."))

    def test_find_on_a_missing_file_is_none(self) -> None:
        self.assertIsNone(CaptionStore(self.path).find("x"))
