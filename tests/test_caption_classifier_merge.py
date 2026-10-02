"""Tests for merging classifier results into the captions CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.caption_classifier import merge
from scripts.caption_classifier.results import caption_sha1

HEADER = ["Caption", "Aspect Ratio", "Erotic Score"]
CAPTIONS = ["__ALICE__ smiles.", "__BELLA__ kneels.", "__ALICE__ reads."]


def _write_csv(path: Path, rows, header=HEADER):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _rec(row_id, caption, act="L", status="ok"):
    act_dist = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    act_dist[act] = 0.9
    act_dist["A"] = round(act_dist["A"] + 0.1, 4)
    return {
        "row_id": row_id,
        "caption_sha1": caption_sha1(caption),
        "status": status,
        "partner": {"A": 0.2, "B": 0.8, "C": 0.0, "D": 0.0},
        "kiss": {"Y": 0.85, "N": 0.15},
        "emotion": {"A": 0.6, "B": 0.3, "C": 0.1},
        "act_raw": act_dist,
        "act_gated": act_dist,
        "act_gate_conflict": False,
        "issues": [{"type": "extra_limb"}, {"type": "gaze_conflict"}],
    }


def _read(path: Path):
    return list(csv.DictReader(path.open(encoding="utf-8")))


def test_cells_use_names_and_four_decimals():
    cells = merge.classification_cells(_rec(0, CAPTIONS[0]))
    assert cells == {
        "Caption SHA1": caption_sha1(CAPTIONS[0]),
        "Emotion": "none",
        "Emotion Explicit": "0.1000",
        "Kiss": "0.8500",
        "Partner": "male",
        "Act": "doggy",
        "Act P": "0.9000",
        "Act Conflict": "false",
        "Issues": "extra_limb;gaze_conflict",
    }


def test_columns_are_filled_only_where_the_caption_still_matches(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    records = {
        0: _rec(0, CAPTIONS[0]),
        1: _rec(1, "an older caption text"),  # edited since classification
        2: {**_rec(2, CAPTIONS[2]), "status": "error", "error": "HTTP 400"},
    }
    dest = tmp_path / "m.csv"
    stats = merge.merge_csv(src, records, dest)
    rows = _read(dest)
    assert list(rows[0])[: len(HEADER)] == HEADER
    assert list(rows[0])[len(HEADER) :] == merge.CLASS_COLUMNS
    assert [r["Caption"] for r in rows] == CAPTIONS
    assert rows[0]["Act"] == "doggy"
    assert rows[1]["Act"] == "" and rows[1]["Caption SHA1"] == ""
    assert rows[2]["Act"] == ""
    assert (stats.filled, stats.stale, stats.errors, stats.unclassified) == (1, 1, 1, 0)


def test_row_ids_skip_blank_captions_like_the_caption_store(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(
        src,
        [
            [CAPTIONS[0], "1:1", "0.5"],
            ["   ", "1:1", "0.5"],
            [CAPTIONS[1], "1:1", "0.5"],
        ],
    )
    records = {0: _rec(0, CAPTIONS[0]), 1: _rec(1, CAPTIONS[1])}
    dest = tmp_path / "m.csv"
    stats = merge.merge_csv(src, records, dest)
    rows = _read(dest)
    assert [r["Act"] for r in rows] == ["doggy", "", "doggy"]
    assert stats.blank == 1 and stats.filled == 2


def test_merging_again_replaces_the_columns(tmp_path):
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    first = tmp_path / "m1.csv"
    merge.merge_csv(src, {0: _rec(0, CAPTIONS[0])}, first)
    second = tmp_path / "m2.csv"
    merge.merge_csv(first, {0: _rec(0, CAPTIONS[0], act="M")}, second)
    with second.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == HEADER + merge.CLASS_COLUMNS
    assert _read(second)[0]["Act"] == "cowgirl"


def test_replace_keeps_a_backup_and_swaps_the_file(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "results-aaaaaaaaaaaa.jsonl").write_text(
        json.dumps(_rec(0, CAPTIONS[0])) + "\n"
    )
    src = tmp_path / "c.csv"
    _write_csv(src, [[c, "1:1", "0.5"] for c in CAPTIONS])
    original = src.read_bytes()
    assert merge.main(["--csv", str(src), "--out", str(out)]) == 0
    assert src.read_bytes() == original  # without --replace nothing is swapped
    assert (tmp_path / "c.merged.csv").exists()
    assert merge.main(["--csv", str(src), "--out", str(out), "--replace"]) == 0
    backups = list(tmp_path.glob("c.csv.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert _read(src)[0]["Act"] == "doggy"
    assert not (tmp_path / "c.merged.csv").exists()
