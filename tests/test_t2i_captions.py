"""Tests for the t2i caption store (spec section 4).

Every CSV here is written by the test itself from hand-written rows with
known properties -- never the real caption file. The Review Focus 1 tests
(``RecordRobustnessTests``) pin quote-balanced records: an embedded newline,
CRLF line endings, a UTF-8 BOM and trailing blank lines all index correctly.
"""

from __future__ import annotations

import csv
import io
import json
import os
import random
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from metascan.core.t2i_captions import (
    CaptionFilterError,
    CaptionPicker,
    CaptionRow,
    CaptionStore,
)

COLUMNS = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
]

# id: caption | aspect | nudity | quality | erotic | porn | males | females | clothing
ROWS: List[List[str]] = [
    # 0
    [
        "__ALICE__ reads a book by a window.",
        "3:2",
        "none",
        "0.90",
        "0.05",
        "0.00",
        "0",
        "1",
        "['dress', 'scarf']",
    ],
    # 1
    [
        "__ALICE__ and __ADAM__ walk on a beach.",
        "2:3",
        "none",
        "0.80",
        "0.10",
        "0.00",
        "1",
        "1",
        "['shirt', 'shorts', 'dress']",
    ],
    # 2
    [
        "__BELLA__ poses in a corset.",
        "2:3",
        "partial",
        "0.70",
        "0.55",
        "0.10",
        "0",
        "1",
        "['corset']",
    ],
    # 3
    ["__CLARA__ sleeps.", "1:1", "full", "0.60", "0.90", "0.80", "0", "1", "[]"],
    # 4
    [
        "__ALICE__, __BELLA__ and __CLARA__ dance.",
        "16:9",
        "partial",
        "0.95",
        "0.40",
        "0.05",
        "0",
        "3",
        "['dress']",
    ],
    # 5
    [
        "__ADAM__ and __BOB__ argue.",
        "4:3",
        "none",
        "0.50",
        "0.00",
        "0.00",
        "2",
        "0",
        "['tie', 'suit']",
    ],
    # 6
    ["A red bicycle.", "3:2", "none", "0.30", "0.00", "0.00", "0", "0", "[]"],
    # 7
    [
        "__ALICE__ wears a tie and a corset.",
        "3:2",
        "partial",
        "0.85",
        "0.60",
        "0.20",
        "0",
        "1",
        "['tie', 'corset']",
    ],
]

ALL_IDS = list(range(len(ROWS)))


def encode_csv(
    rows: Sequence[Sequence[str]],
    header: Sequence[str] = COLUMNS,
    eol: str = "\n",
    bom: bool = False,
    tail: Optional[str] = None,
) -> bytes:
    """A CSV file's bytes, quoted the way Python's csv writer quotes it.

    ``tail`` replaces the final line terminator (``""`` drops it; extra
    terminators add trailing blank lines).
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=eol)
    writer.writerow(header)
    writer.writerows(rows)
    text = buffer.getvalue()
    if tail is not None:
        text = text[: -len(eol)] + tail
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "captions.csv"

    def write(self, data: bytes) -> None:
        self.path.write_bytes(data)

    def store(
        self, rows: Optional[Sequence[Sequence[str]]] = None, **kwargs: Any
    ) -> CaptionStore:
        self.write(encode_csv(ROWS if rows is None else rows, **kwargs))
        return CaptionStore(self.path)

    def later(self) -> None:
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    @staticmethod
    def ids(store: CaptionStore, flt: Optional[Mapping[str, Any]]) -> List[int]:
        """Every id a filter matches, found by exhausting one picker cycle."""
        picker = store.picker(flt, random.Random(0))
        return sorted(picker.next().id for _ in range(picker.pool_size))


class BasicsTests(StoreCase):
    def test_index_reports_total_and_availability(self) -> None:
        store = self.store()
        self.assertTrue(store.available())
        self.assertIsNone(store.error())
        self.assertEqual(store.total(), len(ROWS))

    def test_get_reads_each_row_lazily_and_exactly(self) -> None:
        store = self.store()
        for row_id, fields in enumerate(ROWS):
            with self.subTest(row_id=row_id):
                row = store.get(row_id)
                self.assertIsInstance(row, CaptionRow)
                self.assertEqual(row.id, row_id)
                self.assertEqual(row.caption, fields[0])
                self.assertEqual(row.aspect_ratio, fields[1])
                self.assertEqual(row.nudity, fields[2])
                self.assertEqual(row.artistic_quality, float(fields[3]))
                self.assertEqual(row.erotic_score, float(fields[4]))
                self.assertEqual(row.pornographic_score, float(fields[5]))
                self.assertEqual(row.males, int(fields[6]))
                self.assertEqual(row.females, int(fields[7]))

    def test_get_raises_keyerror_for_an_absent_id(self) -> None:
        store = self.store()
        for bad in (-1, len(ROWS), 10**9):
            with self.subTest(bad=bad):
                with self.assertRaises(KeyError):
                    store.get(bad)

    def test_row_to_dict_is_json_ready_with_clothing_as_a_list(self) -> None:
        row = self.store().get(1)
        data = row.to_dict()
        self.assertEqual(
            list(data),
            [
                "id",
                "caption",
                "aspect_ratio",
                "nudity",
                "artistic_quality",
                "erotic_score",
                "pornographic_score",
                "males",
                "females",
                "clothing",
            ],
        )
        self.assertEqual(data["clothing"], ["shirt", "shorts", "dress"])
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_rows_are_frozen(self) -> None:
        row = self.store().get(0)
        with self.assertRaises(Exception):
            row.caption = "changed"  # type: ignore[misc]

    def test_header_names_ignore_case_and_spacing(self) -> None:
        header = ["caption", " ASPECT   ratio ", "NUDITY", "males"]
        rows = [["Hello there.", "3:2", "none", "2"], ["Second.", "1:1", "full", "0"]]
        store = self.store(rows, header=header)
        self.assertTrue(store.available())
        self.assertEqual(store.get(0).males, 2)
        self.assertEqual(self.ids(store, {"nudity": ["full"]}), [1])


class FilterTests(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.s = self.store()

    def test_no_filter_matches_everything(self) -> None:
        for flt in (None, {}, {"nudity": None}):
            with self.subTest(flt=flt):
                self.assertEqual(self.s.count(flt), len(ROWS))
                self.assertEqual(self.ids(self.s, flt), ALL_IDS)

    def test_nudity_is_any_of_and_case_insensitive(self) -> None:
        self.assertEqual(self.ids(self.s, {"nudity": ["none"]}), [0, 1, 5, 6])
        self.assertEqual(
            self.ids(self.s, {"nudity": ["partial", "full"]}), [2, 3, 4, 7]
        )
        self.assertEqual(self.ids(self.s, {"nudity": ["NONE"]}), [0, 1, 5, 6])
        self.assertEqual(self.s.count({"nudity": ["nonexistent"]}), 0)

    def test_artistic_quality_range_is_inclusive(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"artistic_quality": {"min": 0.8, "max": 0.95}}),
            [0, 1, 4, 7],
        )
        self.assertEqual(self.ids(self.s, {"artistic_quality": {"min": 0.9}}), [0, 4])
        self.assertEqual(self.ids(self.s, {"artistic_quality": {"max": 0.5}}), [5, 6])

    def test_erotic_score_range(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"erotic_score": {"min": 0.5, "max": 1.0}}), [2, 3, 7]
        )
        # 0.05 is stored as float32; the boundary must still be inclusive.
        self.assertEqual(self.ids(self.s, {"erotic_score": {"max": 0.05}}), [0, 5, 6])

    def test_pornographic_score_range(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"pornographic_score": {"min": 0.1}}), [2, 3, 7]
        )
        self.assertEqual(
            self.ids(self.s, {"pornographic_score": {"max": 0.0}}), [0, 1, 5, 6]
        )

    def test_males_and_females_int_ranges(self) -> None:
        self.assertEqual(self.ids(self.s, {"males": {"min": 1}}), [1, 5])
        self.assertEqual(self.ids(self.s, {"males": {"max": 0}}), [0, 2, 3, 4, 6, 7])
        self.assertEqual(self.ids(self.s, {"males": {"min": 2, "max": 2}}), [5])
        self.assertEqual(self.ids(self.s, {"females": {"min": 3}}), [4])
        self.assertEqual(
            self.ids(self.s, {"females": {"min": 1, "max": 1}}), [0, 1, 2, 3, 7]
        )
        self.assertEqual(self.ids(self.s, {"females": {"max": 0}}), [5, 6])

    def test_aspect_ratios_is_any_of(self) -> None:
        self.assertEqual(self.ids(self.s, {"aspect_ratios": ["3:2"]}), [0, 6, 7])
        self.assertEqual(
            self.ids(self.s, {"aspect_ratios": ["2:3", "16:9"]}), [1, 2, 4]
        )

    def test_clothing_any_and_none(self) -> None:
        self.assertEqual(self.ids(self.s, {"clothing_any": ["corset"]}), [2, 7])
        self.assertEqual(
            self.ids(self.s, {"clothing_any": ["dress", "suit"]}), [0, 1, 4, 5]
        )
        self.assertEqual(
            self.ids(self.s, {"clothing_none": ["tie"]}), [0, 1, 2, 3, 4, 6]
        )
        self.assertEqual(
            self.ids(self.s, {"clothing_any": ["corset"], "clothing_none": ["tie"]}),
            [2],
        )
        self.assertEqual(self.s.count({"clothing_any": ["nothing-like-this"]}), 0)

    def test_keys_combine_with_and(self) -> None:
        flt = {
            "nudity": ["partial"],
            "clothing_any": ["corset"],
            "females": {"min": 1},
            "erotic_score": {"min": 0.58},
        }
        self.assertEqual(self.ids(self.s, flt), [7])

    def test_empty_lists_are_no_constraint(self) -> None:
        flt = {
            "nudity": [],
            "aspect_ratios": [],
            "clothing_any": [],
            "clothing_none": [],
        }
        self.assertEqual(self.s.count(flt), len(ROWS))

    def test_count_matches_the_ids_and_is_a_plain_int(self) -> None:
        count = self.s.count({"nudity": ["partial"]})
        self.assertEqual(count, 3)
        self.assertIs(type(count), int)

    def test_unknown_filter_key_is_an_error_everywhere(self) -> None:
        for call in (
            lambda: self.s.count({"mood": ["happy"]}),
            lambda: self.s.picker({"mood": ["happy"]}, random.Random(0)),
        ):
            with self.assertRaises(CaptionFilterError) as ctx:
                call()
            self.assertIn("mood", str(ctx.exception))

    def test_bad_filter_values_are_errors(self) -> None:
        bad = [
            ["none"],  # not a mapping
            {"nudity": "none"},
            {"nudity": [1]},
            {"aspect_ratios": "3:2"},
            {"clothing_any": [None]},
            {"artistic_quality": 0.5},
            {"artistic_quality": {"min": "high"}},
            {"artistic_quality": {"min": True}},
            {"artistic_quality": {"min": float("nan")}},
            {"artistic_quality": {"lo": 0.1}},
            {"artistic_quality": {"min": 0.9, "max": 0.1}},
            {"males": {"min": 1.5}},
            {"females": [1, 2]},
        ]
        for flt in bad:
            with self.subTest(flt=flt):
                with self.assertRaises(CaptionFilterError):
                    self.s.count(flt)  # type: ignore[arg-type]

    def test_null_bounds_are_ignored(self) -> None:
        flt = {"artistic_quality": {"min": None, "max": 0.5}}
        self.assertEqual(self.ids(self.s, flt), [5, 6])

    def test_out_of_range_bounds_do_not_overflow(self) -> None:
        self.assertEqual(self.s.count({"males": {"min": -5, "max": 300}}), len(ROWS))
        self.assertEqual(
            self.s.count({"artistic_quality": {"min": -1e40, "max": 1e40}}), len(ROWS)
        )
        self.assertEqual(self.s.count({"males": {"min": 300}}), 0)

    def test_zero_matches_is_an_error_for_the_picker_but_not_for_count(self) -> None:
        flt = {"nudity": ["full"], "males": {"min": 2}}
        self.assertEqual(self.s.count(flt), 0)
        with self.assertRaises(CaptionFilterError):
            self.s.picker(flt, random.Random(0))


class PickerTests(StoreCase):
    def test_pool_is_shuffled_without_replacement_then_reshuffled(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(7))
        self.assertIsInstance(picker, CaptionPicker)
        self.assertEqual(picker.pool_size, len(ROWS))
        first = [picker.next().id for _ in range(len(ROWS))]
        second = [picker.next().id for _ in range(len(ROWS))]
        self.assertEqual(sorted(first), ALL_IDS)
        self.assertEqual(sorted(second), ALL_IDS)

    def test_picker_honours_the_filter_pool(self) -> None:
        store = self.store()
        picker = store.picker({"nudity": ["none"]}, random.Random(1))
        self.assertEqual(picker.pool_size, 4)
        seen = {picker.next().id for _ in range(4)}
        self.assertEqual(seen, {0, 1, 5, 6})

    def test_same_rng_seed_gives_the_same_sequence(self) -> None:
        store = self.store()
        a = store.picker(None, random.Random(99))
        b = store.picker(None, random.Random(99))
        self.assertEqual(
            [a.next().id for _ in range(20)], [b.next().id for _ in range(20)]
        )

    def test_every_row_can_come_first(self) -> None:
        store = self.store()
        firsts = {store.picker(None, random.Random(s)).next().id for s in range(400)}
        self.assertEqual(firsts, set(ALL_IDS))

    def test_no_immediate_repeat_across_the_reshuffle(self) -> None:
        store = self.store()
        for seed in range(60):
            picker = store.picker({"aspect_ratios": ["2:3"]}, random.Random(seed))
            self.assertEqual(picker.pool_size, 2)
            ids = [picker.next().id for _ in range(30)]
            for before, after in zip(ids, ids[1:]):
                self.assertNotEqual(before, after, (seed, ids))

    def test_a_pool_of_one_repeats_that_row(self) -> None:
        store = self.store()
        picker = store.picker({"aspect_ratios": ["16:9"]}, random.Random(3))
        self.assertEqual(picker.pool_size, 1)
        self.assertEqual({picker.next().id for _ in range(5)}, {4})

    def test_picker_returns_full_rows(self) -> None:
        store = self.store()
        picker = store.picker({"aspect_ratios": ["16:9"]}, random.Random(3))
        row = picker.next()
        self.assertEqual(row.caption, ROWS[4][0])
        self.assertEqual(row.clothing, ("dress",))

    def test_picker_survives_the_file_changing_mid_run(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(5))
        picker.next()
        new_rows = [
            [f"NEW caption {i}.", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"]
            for i in range(4)
        ]
        self.write(encode_csv(new_rows))
        self.later()
        picked = [picker.next() for _ in range(8)]
        self.assertTrue(all(row.caption.startswith("NEW caption") for row in picked))
        self.assertEqual(picker.pool_size, 4)

    def test_picker_errors_cleanly_when_the_file_disappears(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(5))
        self.path.unlink()
        with self.assertRaises(CaptionFilterError):
            picker.next()


class ReloadAndAvailabilityTests(StoreCase):
    def test_reload_after_a_size_change(self) -> None:
        store = self.store(ROWS[:3])
        self.assertEqual(store.total(), 3)
        self.write(encode_csv(ROWS[:5]))
        self.assertEqual(store.total(), 5)
        self.assertEqual(store.get(4).caption, ROWS[4][0])

    def test_reload_after_a_mtime_only_change(self) -> None:
        store = self.store(ROWS[:2])
        self.assertEqual(store.get(0).caption, ROWS[0][0])
        swapped = [list(ROWS[0]), list(ROWS[1])]
        swapped[0][0] = swapped[0][0].upper()  # same byte length
        self.write(encode_csv(swapped))
        self.later()
        self.assertEqual(store.get(0).caption, ROWS[0][0].upper())

    def test_an_unchanged_file_keeps_its_index(self) -> None:
        store = self.store()
        store.total()
        index = store._index  # noqa: SLF001 - identity check on the cached index
        store.total()
        store.count(None)
        self.assertIs(store._index, index)

    def test_missing_file_is_unavailable_and_appears_later(self) -> None:
        store = CaptionStore(self.path)
        self.assertFalse(store.available())
        self.assertIn("not found", store.error() or "")
        self.assertEqual(store.total(), 0)
        self.assertEqual(store.count(None), 0)
        self.assertEqual(store.meta(), {"total": 0, "columns": []})
        with self.assertRaises(CaptionFilterError):
            store.picker(None, random.Random(0))
        with self.assertRaises(KeyError):
            store.get(0)
        self.write(encode_csv(ROWS))
        self.assertTrue(store.available())
        self.assertEqual(store.total(), len(ROWS))

    def test_missing_required_column_makes_the_store_unavailable(self) -> None:
        for header, missing in (
            (["Caption", "Nudity"], "Aspect Ratio"),
            (["Aspect Ratio", "Nudity"], "Caption"),
        ):
            with self.subTest(header=header):
                store = self.store([["x", "y"]], header=header)
                self.assertFalse(store.available())
                self.assertIn(missing, store.error() or "")
                self.assertEqual(store.total(), 0)
                with self.assertRaises(CaptionFilterError):
                    store.picker(None, random.Random(0))

    def test_header_only_and_empty_files_are_unavailable(self) -> None:
        for data in (encode_csv([]), b"", b"\n\n\n"):
            with self.subTest(data=data):
                self.write(data)
                store = CaptionStore(self.path)
                self.assertFalse(store.available())
                self.assertTrue(store.error())

    def test_invalid_csv_is_unavailable_with_a_line_number(self) -> None:
        self.write(b"Caption,Aspect Ratio\nfine,3:2\nbroken,1:1\rmore,4:3\n")
        store = CaptionStore(self.path)
        self.assertFalse(store.available())
        self.assertIn("line", store.error() or "")

    def test_missing_optional_columns_drop_their_filters(self) -> None:
        header = ["Caption", "Aspect Ratio", "Males"]
        store = self.store([["A.", "3:2", "1"], ["B.", "1:1", "0"]], header=header)
        keys = [column["key"] for column in store.meta()["columns"]]
        self.assertEqual(keys, ["males", "aspect_ratios"])
        with self.assertRaises(CaptionFilterError) as ctx:
            store.count({"nudity": ["none"]})
        self.assertIn("Nudity", str(ctx.exception))
        for key, value in (
            ("erotic_score", {"min": 0}),
            ("artistic_quality", {"min": 0}),
            ("pornographic_score", {"min": 0}),
            ("females", {"min": 0}),
            ("clothing_any", ["x"]),
            ("clothing_none", ["x"]),
        ):
            with self.subTest(key=key):
                with self.assertRaises(CaptionFilterError):
                    store.count({key: value})
        self.assertEqual(store.count({"males": {"min": 1}}), 1)

    def test_concurrent_counts_are_consistent(self) -> None:
        store = self.store()
        results: List[int] = []
        errors: List[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(30):
                    results.append(store.count({"nudity": ["partial"]}))
            except BaseException as exc:  # pragma: no cover - would fail the test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {3})


class MetaTests(StoreCase):
    def test_meta_lists_every_present_column_in_order(self) -> None:
        meta = self.store().meta()
        self.assertEqual(meta["total"], len(ROWS))
        self.assertEqual(
            [(c["key"], c["label"], c["type"]) for c in meta["columns"]],
            [
                ("nudity", "Nudity", "choice"),
                ("artistic_quality", "Artistic Quality", "range"),
                ("erotic_score", "Erotic Score", "range"),
                ("pornographic_score", "Pornographic Score", "range"),
                ("males", "Males", "int_range"),
                ("females", "Females", "int_range"),
                ("aspect_ratios", "Aspect Ratio", "choice"),
                ("clothing", "Clothing", "tags"),
            ],
        )

    def test_choice_options_carry_counts(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            columns["nudity"]["options"],
            [
                {"value": "none", "count": 4},
                {"value": "partial", "count": 3},
                {"value": "full", "count": 1},
            ],
        )
        self.assertEqual(
            [(o["value"], o["count"]) for o in columns["aspect_ratios"]["options"]],
            [("3:2", 3), ("2:3", 2), ("16:9", 1), ("1:1", 1), ("4:3", 1)],
        )

    def test_clothing_options_are_by_count_descending(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            [(o["value"], o["count"]) for o in columns["clothing"]["options"]],
            [
                ("dress", 3),
                ("corset", 2),
                ("tie", 2),
                ("scarf", 1),
                ("shirt", 1),
                ("shorts", 1),
                ("suit", 1),
            ],
        )

    def test_numeric_columns_report_data_ranges(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            columns["artistic_quality"],
            {
                "key": "artistic_quality",
                "label": "Artistic Quality",
                "type": "range",
                "min": 0.3,
                "max": 0.95,
                "step": 0.05,
            },
        )
        self.assertEqual(
            (columns["erotic_score"]["min"], columns["erotic_score"]["max"]), (0.0, 0.9)
        )
        self.assertEqual(
            columns["males"],
            {"key": "males", "label": "Males", "type": "int_range", "min": 0, "max": 2},
        )
        self.assertEqual((columns["females"]["min"], columns["females"]["max"]), (0, 3))

    def test_meta_is_plain_json(self) -> None:
        meta = self.store().meta()
        self.assertEqual(json.loads(json.dumps(meta)), meta)


class ClothingParsingTests(StoreCase):
    def test_list_strings_parse_and_malformed_ones_are_empty(self) -> None:
        cases = [
            ("['a', 'b']", ("a", "b")),
            ('["x y", "z"]', ("x y", "z")),
            ("[]", ()),
            ("", ()),
            ("['unterminated", ()),
            ("not a list at all", ()),
            ("'single'", ()),
            ("[1, 'ok', None, '  ', 'ok']", ("ok",)),
            ("['dup', 'dup', 'other']", ("dup", "other")),
            ("{'a': 1}", ()),
            ("[" * 60 + "'x'" + "]" * 60, ()),
        ]
        rows = [
            [f"caption {i}", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", raw]
            for i, (raw, _) in enumerate(cases)
        ]
        store = self.store(rows)
        self.assertEqual(store.total(), len(cases))
        for i, (raw, expected) in enumerate(cases):
            with self.subTest(raw=raw):
                self.assertEqual(store.get(i).clothing, expected)

    def test_malformed_rows_stay_in_the_pool_and_match_clothing_none(self) -> None:
        rows = [
            ["ok", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "['tie']"],
            ["bad", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "['tie'"],
        ]
        store = self.store(rows)
        self.assertEqual(self.ids(store, {"clothing_none": ["tie"]}), [1])
        self.assertEqual(self.ids(store, {"clothing_any": ["tie"]}), [0])


class MessyValueTests(StoreCase):
    def test_short_rows_and_junk_numbers_become_none(self) -> None:
        lines = [
            ",".join(COLUMNS),
            "Only caption and ratio,3:2",
            "Junk,1:1,none,abc,inf,nan,x,-4,[]",
            "Big,2:3,full,0.5,0.5,0.5,999,7,[]",
        ]
        self.write(("\n".join(lines) + "\n").encode("utf-8"))
        store = CaptionStore(self.path)
        self.assertEqual(store.total(), 3)
        short = store.get(0)
        self.assertEqual(short.aspect_ratio, "3:2")
        self.assertIsNone(short.nudity)
        self.assertIsNone(short.artistic_quality)
        self.assertIsNone(short.males)
        self.assertEqual(short.clothing, ())
        junk = store.get(1)
        self.assertEqual(
            (
                junk.artistic_quality,
                junk.erotic_score,
                junk.pornographic_score,
                junk.males,
                junk.females,
            ),
            (None, None, None, None, None),
        )
        big = store.get(2)
        self.assertEqual((big.males, big.females), (None, 7))
        # Rows without a value never match a range that constrains that column.
        self.assertEqual(self.ids(store, {"artistic_quality": {"min": 0.0}}), [2])
        self.assertEqual(store.count({"males": {"min": 0}}), 0)

    def test_blank_and_whitespace_captions_are_not_indexed(self) -> None:
        rows = [
            ["First.", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["   ", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["Second.", "1:1", "full", "0.5", "0.1", "0.0", "0", "1", "[]"],
        ]
        store = self.store(rows)
        self.assertEqual(store.total(), 2)
        self.assertEqual(
            [store.get(i).caption for i in range(2)], ["First.", "Second."]
        )


class RecordRobustnessTests(StoreCase):
    """Review focus 1: records are quote-balanced, not physical lines."""

    NEWLINE_CAPTION = (
        'Line one\nLine two, with a comma and "quotes" and a trailing newline\n'
    )
    CRLF_CAPTION = "First line\r\nSecond line\r\nThird, with a comma"
    UNICODE_CAPTION = "Caf\u00e9 \u4e2d\u6587 \U0001f600 after multibyte text"

    def rows_around(self, caption: str) -> List[List[str]]:
        middle = [
            caption,
            "3:2",
            "partial",
            "0.42",
            "0.25",
            "0.75",
            "2",
            "1",
            "['coat']",
        ]
        return [list(ROWS[0]), list(ROWS[1]), middle, list(ROWS[2]), list(ROWS[3])]

    def check_round_trip(self, store: CaptionStore, rows: List[List[str]]) -> None:
        self.assertTrue(store.available(), store.error())
        self.assertEqual(store.total(), len(rows))
        for i, fields in enumerate(rows):
            with self.subTest(row=i):
                row = store.get(i)
                self.assertEqual(row.caption, fields[0])
                self.assertEqual(row.aspect_ratio, fields[1])
                self.assertEqual(row.nudity, fields[2])
                self.assertEqual(row.males, int(fields[6]))
                self.assertEqual(row.females, int(fields[7]))

    def test_an_embedded_newline_does_not_split_the_record(self) -> None:
        rows = self.rows_around(self.NEWLINE_CAPTION)
        self.check_round_trip(self.store(rows), rows)
        store = self.store(rows)
        # Filters see the rows after the multi-line record at the right ids.
        self.assertEqual(self.ids(store, {"nudity": ["partial"]}), [2, 3])
        self.assertEqual(self.ids(store, {"clothing_any": ["coat"]}), [2])

    def test_crlf_line_endings(self) -> None:
        rows = [list(r) for r in ROWS]
        store = self.store(rows, eol="\r\n")
        self.check_round_trip(store, rows)
        self.assertFalse(
            any(store.get(i).caption.endswith("\r") for i in range(len(rows)))
        )
        self.assertEqual(self.ids(store, {"aspect_ratios": ["3:2"]}), [0, 6, 7])

    def test_crlf_file_with_a_crlf_newline_inside_a_quoted_caption(self) -> None:
        rows = self.rows_around(self.CRLF_CAPTION)
        self.check_round_trip(self.store(rows, eol="\r\n"), rows)

    def test_utf8_bom_is_ignored(self) -> None:
        rows = [list(r) for r in ROWS]
        store = self.store(rows, bom=True)
        self.check_round_trip(store, rows)
        self.assertTrue(store.get(0).caption.startswith("__ALICE__"))
        self.assertEqual(self.ids(store, {"nudity": ["full"]}), [3])

    def test_trailing_blank_lines_add_no_rows(self) -> None:
        rows = [list(r) for r in ROWS]
        for tail in ("\n\n", "\n\n\n\n", "\r\n\r\n"):
            with self.subTest(tail=tail):
                eol = "\r\n" if "\r" in tail else "\n"
                self.check_round_trip(self.store(rows, eol=eol, tail=eol + tail), rows)

    def test_the_last_record_needs_no_final_newline(self) -> None:
        rows = [list(r) for r in ROWS]
        self.check_round_trip(self.store(rows, tail=""), rows)

    def test_blank_lines_between_records_are_skipped(self) -> None:
        rows = [list(r) for r in ROWS[:4]]
        data = encode_csv(rows).decode("utf-8").split("\n")
        data.insert(2, "")
        data.insert(4, "")
        self.write("\n".join(data).encode("utf-8"))
        self.check_round_trip(CaptionStore(self.path), rows)

    def test_multibyte_text_does_not_disturb_byte_offsets(self) -> None:
        rows = self.rows_around(self.UNICODE_CAPTION)
        self.check_round_trip(self.store(rows), rows)

    def test_doubled_quotes_and_commas_round_trip(self) -> None:
        caption = 'She said "hello", then "goodbye", then left, quietly.'
        rows = self.rows_around(caption)
        self.check_round_trip(self.store(rows), rows)

    def test_every_hazard_at_once(self) -> None:
        rows = [
            list(ROWS[0]),
            self.rows_around(self.NEWLINE_CAPTION)[2],
            list(ROWS[1]),
            self.rows_around(self.CRLF_CAPTION)[2],
            self.rows_around(self.UNICODE_CAPTION)[2],
            list(ROWS[2]),
        ]
        store = self.store(rows, eol="\r\n", bom=True, tail="\r\n\r\n\r\n")
        self.check_round_trip(store, rows)
        self.assertEqual(self.ids(store, {"clothing_any": ["coat"]}), [1, 3, 4])
        picker = store.picker(None, random.Random(4))
        self.assertEqual(
            sorted(picker.next().id for _ in range(picker.pool_size)), list(range(6))
        )

    def test_a_stray_quote_in_an_unquoted_field_is_literal(self) -> None:
        lines = [
            ",".join(COLUMNS),
            'A 5" tall statue,3:2,none,0.5,0.1,0.0,0,0,[]',
            "Next row,1:1,full,0.5,0.1,0.0,0,0,[]",
        ]
        self.write(("\n".join(lines) + "\n").encode("utf-8"))
        store = CaptionStore(self.path)
        self.assertEqual(store.total(), 2)
        self.assertEqual(store.get(0).caption, 'A 5" tall statue')
        self.assertEqual(store.get(1).caption, "Next row")

    def test_a_large_file_indexes_every_record_and_reads_them_back(self) -> None:
        rows: List[List[str]] = []
        for i in range(30000):
            caption = f"Generated caption {i}."
            if i % 997 == 0:
                caption = f"Generated caption {i},\nwith a second line."
            rows.append(
                [
                    caption,
                    "3:2" if i % 2 else "2:3",
                    "none",
                    "0.5",
                    "0.1",
                    "0.0",
                    str(i % 3),
                    "1",
                    "['x']",
                ]
            )
        store = self.store(rows, eol="\r\n")
        self.assertEqual(store.total(), 30000)
        for i in (0, 1, 996, 997, 998, 1994, 15000, 29999):
            self.assertEqual(store.get(i).caption, rows[i][0], i)
        self.assertEqual(store.count({"aspect_ratios": ["3:2"]}), 15000)
        self.assertEqual(store.count({"males": {"min": 2}}), 10000)


if __name__ == "__main__":
    unittest.main()
