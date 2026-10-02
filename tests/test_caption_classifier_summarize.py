"""Tests for flattening results into classifications.csv."""

from __future__ import annotations

import csv
import json

from scripts.caption_classifier import summarize


def _ok(row_id):
    act = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    act.update({"L": 0.6, "M": 0.3, "A": 0.1})
    return {
        "row_id": row_id,
        "caption_sha1": "h%d" % row_id,
        "status": "ok",
        "partner": {"A": 0.1, "B": 0.9, "C": 0.0, "D": 0.0},
        "kiss": {"Y": 0.25, "N": 0.75},
        "emotion": {"A": 0.7, "B": 0.2, "C": 0.1},
        "act_raw": dict(act, I=0.0),
        "act_gated": act,
        "act_gate_conflict": False,
        "issues": [
            {
                "type": "extra_limb",
                "quote_a": "a",
                "quote_b": "b",
                "quote_verified": True,
            }
        ],
    }


def test_columns_are_in_the_documented_order():
    assert summarize.COLUMNS == [
        "row_id",
        "caption_sha1",
        "status",
        "partner",
        "partner_p",
        "kiss_p",
        "emotion",
        "p_emotion_none",
        "p_emotion_implicit",
        "p_emotion_explicit",
        "act_1",
        "act_1_p",
        "act_2",
        "act_2_p",
        "act_3",
        "act_3_p",
        "act_raw_top",
        "act_raw_top_p",
        "act_gate_conflict",
        "issue_types",
        "issues",
        "error",
    ]


def test_ok_record_is_flattened_with_names():
    row = summarize.summarize_record(_ok(4))
    assert row["partner"] == "male" and row["partner_p"] == "0.9"
    assert row["kiss_p"] == "0.25"
    assert row["emotion"] == "none" and row["p_emotion_none"] == "0.7"
    assert (row["act_1"], row["act_1_p"]) == ("doggy", "0.6")
    assert (row["act_2"], row["act_3"]) == ("cowgirl", "none-artistic")
    assert row["act_raw_top"] == "doggy" and row["act_gate_conflict"] == "false"
    assert row["issue_types"] == "extra_limb"
    assert json.loads(row["issues"])[0]["quote_verified"] is True


def test_zero_probability_acts_leave_the_slot_blank():
    rec = _ok(0)
    rec["act_gated"] = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    rec["act_gated"]["A"] = 1.0
    row = summarize.summarize_record(rec)
    assert row["act_1"] == "none-artistic"
    assert row["act_2"] == "" and row["act_2_p"] == ""


def test_error_record_is_kept_and_marked(tmp_path):
    records = {
        1: {"row_id": 1, "caption_sha1": "x", "status": "error", "error": "HTTP 400"},
        0: _ok(0),
    }
    out = tmp_path / "classifications.csv"
    assert summarize.write_csv(records, out) == 2
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["row_id"] for r in rows] == ["0", "1"]
    assert rows[1]["status"] == "error" and rows[1]["error"] == "HTTP 400"
    assert rows[1]["act_1"] == ""


def test_main_reads_the_newest_results_file(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "results-aaaaaaaaaaaa.jsonl").write_text(json.dumps(_ok(0)) + "\n")
    assert summarize.main(["--out", str(out_dir)]) == 0
    assert (out_dir / "classifications.csv").exists()
