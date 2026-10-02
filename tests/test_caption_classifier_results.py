"""Tests for row selection and the resumable, versioned results file."""

from __future__ import annotations

import json
import os
import time

import pytest

from scripts.caption_classifier.results import (
    ResultsWriter,
    VersionConflict,
    caption_sha1,
    load_done,
    load_records,
    newest_results,
    open_run,
    results_path,
    select_rows,
)


def test_count_takes_the_first_rows():
    assert select_rows(10, 3, None, 0) == [0, 1, 2]
    assert select_rows(2, 5, None, 0) == [0, 1]
    assert select_rows(4, None, None, 0) == [0, 1, 2, 3]


def test_sample_is_sorted_unique_and_seeded():
    a = select_rows(1000, None, 50, 7)
    assert a == sorted(set(a)) and len(a) == 50
    assert a == select_rows(1000, None, 50, 7)
    assert a != select_rows(1000, None, 50, 8)
    assert select_rows(3, None, 10, 0) == [0, 1, 2]


def test_open_run_refuses_when_only_other_versions_exist(tmp_path):
    results_path(tmp_path, "aaaaaaaaaaaa").write_text("")
    with pytest.raises(VersionConflict, match="aaaaaaaaaaaa"):
        open_run(tmp_path, "bbbbbbbbbbbb", new_run=False)
    assert open_run(tmp_path, "bbbbbbbbbbbb", new_run=True) == results_path(
        tmp_path, "bbbbbbbbbbbb"
    )


def test_open_run_resumes_its_own_version(tmp_path):
    results_path(tmp_path, "aaaaaaaaaaaa").write_text("")
    results_path(tmp_path, "bbbbbbbbbbbb").write_text("")
    assert open_run(tmp_path, "bbbbbbbbbbbb", new_run=False).exists()


def test_load_done_skips_errors_and_a_half_written_last_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        json.dumps({"row_id": 0, "caption_sha1": "x", "status": "ok"})
        + "\n"
        + json.dumps({"row_id": 1, "caption_sha1": "y", "status": "error"})
        + "\n"
        + '{"row_id": 2, "caption_sha1": "z", "sta'
    )
    assert load_done(path) == {(0, "x")}
    assert load_done(tmp_path / "missing.jsonl") == set()


def test_writer_repairs_a_missing_final_newline(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"row_id": 0, "caption_sha1": "x", "status": "o')
    writer = ResultsWriter(path)
    writer.write({"row_id": 1, "caption_sha1": "y", "status": "ok"})
    writer.close()
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["row_id"] == 1
    assert load_done(path) == {(1, "y")}


def test_load_records_prefers_the_last_ok_record(tmp_path):
    path = tmp_path / "r.jsonl"
    rows = [
        {"row_id": 0, "caption_sha1": "x", "status": "error", "error": "boom"},
        {"row_id": 0, "caption_sha1": "x", "status": "ok", "n": 1},
        {"row_id": 0, "caption_sha1": "x", "status": "error", "error": "later"},
        {"row_id": 1, "caption_sha1": "y", "status": "error", "error": "e"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    recs = load_records(path)
    assert recs[0]["n"] == 1
    assert recs[1]["status"] == "error"


def test_newest_results_picks_the_latest_file(tmp_path):
    old = results_path(tmp_path, "aaaaaaaaaaaa")
    new = results_path(tmp_path, "bbbbbbbbbbbb")
    old.write_text("")
    new.write_text("")
    past = time.time() - 100
    os.utime(old, (past, past))
    assert newest_results(tmp_path) == new


def test_caption_sha1_is_hex_sha1():
    assert caption_sha1("abc") == "a9993e364706816aba3e25717850c26c9cd0d89d"
