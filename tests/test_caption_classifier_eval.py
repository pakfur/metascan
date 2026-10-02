"""Tests for the fixture file and the evaluation scoring (no model involved)."""

from __future__ import annotations

import json

import pytest

from scripts.caption_classifier import eval as ev


def _dist(letters, pick, p):
    out = {k: 0.0 for k in letters}
    out[pick] = p
    rest = [k for k in letters if k != pick]
    out[rest[0]] = round(1 - p, 4)
    return out


def _rec(partner="A", kiss="N", emotion="A", act="A", p=0.9, issues=()):
    return {
        "status": "ok",
        "partner": _dist("ABCD", partner, p),
        "kiss": _dist("YN", kiss, p),
        "emotion": _dist("ABC", emotion, p),
        "act_gated": _dist("ABCDEFGHIJKLMNOPQRS", act, p),
        "issues": [{"type": t} for t in issues],
    }


def test_shipped_fixtures_are_valid():
    fixtures = ev.load_fixtures(ev.FIXTURES)
    assert len(fixtures) >= 40
    ids = [fx["id"] for fx in fixtures]
    assert len(ids) == len(set(ids))
    acts = {fx["expect"]["act"] for fx in fixtures}
    assert acts == set("ABCDEFGHIJKLMNOPQRS")


def test_invalid_fixture_is_rejected(tmp_path):
    bad = [
        {
            "id": "x",
            "males": 0,
            "females": 1,
            "nudity": "none",
            "erotic": 0.1,
            "porn": 0.0,
            "caption": "c",
            "expect": {
                "partner": "A",
                "kiss": "N",
                "emotion": "A",
                "act": "L",
                "issues": [],
            },
        }
    ]
    path = tmp_path / "f.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="x"):
        ev.load_fixtures(path)


def test_score_counts_accuracy_recall_false_alarms_and_calibration():
    fixtures = [
        {
            "id": "a",
            "expect": {
                "partner": "A",
                "kiss": "N",
                "emotion": "A",
                "act": "A",
                "issues": [],
            },
        },
        {
            "id": "b",
            "expect": {
                "partner": "A",
                "kiss": "N",
                "emotion": "C",
                "act": "L",
                "issues": ["extra_limb"],
            },
        },
        {
            "id": "c",
            "expect": {
                "partner": "A",
                "kiss": "N",
                "emotion": "A",
                "act": "A",
                "issues": [],
            },
        },
    ]
    records = {
        0: _rec(),
        1: _rec(emotion="A", act="M", p=0.6, issues=["extra_limb", "gaze_conflict"]),
        2: _rec(issues=["gaze_conflict"]),
    }
    report = ev.score(fixtures, records)
    assert report["accuracy"]["partner"] == (3, 3)
    assert report["accuracy"]["emotion"] == (2, 3)
    assert report["accuracy"]["act"] == (2, 3)
    assert report["confusion"][("doggy", "cowgirl")] == 1
    assert report["issue_recall"] == (1, 1)
    assert report["issue_false_alarms"] == (1, 2)
    assert report["errors"] == []
    bucket = dict((b[0], (b[1], b[2])) for b in report["calibration"])
    assert bucket["0.5-0.7"] == (4, 2)


def test_error_records_are_listed_not_scored():
    fixtures = [
        {
            "id": "a",
            "expect": {
                "partner": "A",
                "kiss": "N",
                "emotion": "A",
                "act": "A",
                "issues": [],
            },
        }
    ]
    report = ev.score(fixtures, {0: {"status": "error", "error": "HTTP 400"}})
    assert report["errors"] == ["a: HTTP 400"]
    assert report["accuracy"]["act"] == (0, 0)
    assert "a: HTTP 400" in ev.format_report(report)
