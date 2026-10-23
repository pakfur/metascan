"""Favorite rate per writer per 14-day window, the collapse flag, and the
``/api/taste/writer-stats`` route (synthetic data, isolated temp DB)."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.taste_labels import collapse_warning, window_starts

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
STARTS = [
    "2026-08-01",
    "2026-08-15",
    "2026-08-29",
    "2026-09-12",
    "2026-09-26",
    "2026-10-10",
]
LATEST = "2026-10-10 09:00:00"
PREVIOUS = "2026-10-01 09:00:00"


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _gen(
    db: DatabaseManager,
    path: str,
    writer: Optional[str],
    created_at: str,
    outcome: Optional[str] = None,
) -> int:
    """A T2I generation at ``created_at`` with ``outcome`` in
    {None, 'seen', 'favorite', 'pass'}."""
    db.save_media(_media(path))
    image_id = db.create_t2i_image(file_path=path, writer_id=writer)
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?", (created_at, image_id)
        )
        conn.commit()
    _judge(db, path, outcome)
    return image_id


def _judge(db: DatabaseManager, path: str, outcome: Optional[str]) -> None:
    if outcome == "seen":
        db.mark_seen(path, NOW)
    elif outcome in ("favorite", "pass"):
        db.set_verdict(path, outcome, None, NOW)


def _video(
    db: DatabaseManager, path: str, writer: str, created_at: str, outcome: str
) -> None:
    db.save_media(_media("/g/src.png"))
    db.save_media(_media(path))
    vid = db.create_i2v_video(
        source_path="/g/src.png", file_path=path, writer_id=writer
    )
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE i2v_videos SET created_at = ? WHERE id = ?", (created_at, vid)
        )
        conn.commit()
    _judge(db, path, outcome)


def _by(rows: List[Dict[str, Any]], kind: str, writer: str) -> Dict[str, Any]:
    picked = [r for r in rows if r["kind"] == kind and r["writer_id"] == writer]
    return {r["window_start"]: r for r in picked}


def _cell(row: Dict[str, Any]) -> tuple:
    return (row["n_seen"], row["n_fav"], row["rate"])


def test_window_starts_count_back_from_today():
    assert window_starts(NOW, 14, 6) == STARTS
    # The latest window always ends today (inclusive).
    assert window_starts(datetime(2026, 10, 10, tzinfo=timezone.utc), 14, 1) == [
        "2026-09-27"
    ]


def test_rates_per_writer_and_window(db):
    _gen(db, "/g/a1.png", "base", LATEST, "favorite")
    _gen(db, "/g/a2.png", "base", LATEST, "pass")
    _gen(db, "/g/a3.png", "base", LATEST, "seen")
    _gen(db, "/g/a4.png", "base", "2026-10-23T08:00:00+00:00", "seen")
    # Window boundary: midnight UTC on the 10th opens the latest window.
    _gen(db, "/g/b0.png", "base", "2026-10-10 00:00:00", "seen")
    _gen(db, "/g/b1.png", "base", PREVIOUS, "favorite")
    _gen(db, "/g/b2.png", "base", "2026-10-09 23:59:59", "favorite")
    _gen(db, "/g/c1.png", "base+taste", LATEST, "favorite")
    # Deleted after being judged: the tombstone keeps it in the stats
    # (otherwise deleting passes would inflate the rate).
    _gen(db, "/g/c2.png", "base+taste", LATEST, "pass")
    db.delete_media(Path("/g/c2.png"))
    c3 = _gen(db, "/g/c3.png", "base+taste", LATEST, "seen")
    db.delete_t2i_image(c3)
    _gen(db, "/g/d1.png", None, LATEST, "favorite")
    _video(db, "/g/v1.mp4", "base", LATEST, "favorite")

    rows = db.writer_window_stats(14, 6, now=NOW)
    assert {(r["kind"], r["writer_id"]) for r in rows} == {
        ("t2i", "base"),
        ("t2i", "base+taste"),
        ("t2i", "unknown"),
        ("i2v", "base"),
    }
    base = _by(rows, "t2i", "base")
    assert sorted(base) == STARTS
    assert _cell(base["2026-10-10"]) == (5, 1, 0.2)
    assert _cell(base["2026-09-26"]) == (2, 2, 1.0)
    assert _cell(base["2026-08-01"]) == (0, 0, None)
    taste = _by(rows, "t2i", "base+taste")
    assert _cell(taste["2026-10-10"]) == (3, 1, pytest.approx(1 / 3))
    assert _cell(_by(rows, "t2i", "unknown")["2026-10-10"]) == (1, 1, 1.0)
    # I2V is counted on its own, never folded into the T2I rate.
    assert _cell(_by(rows, "i2v", "base")["2026-10-10"]) == (1, 1, 1.0)


def test_unseen_excluded(db):
    _gen(db, "/g/s1.png", "base", LATEST, "seen")
    _gen(db, "/g/u1.png", "base", LATEST, None)
    _gen(db, "/g/u2.png", "base", LATEST, None)
    # A writer whose output was never looked at: listed, but no rate.
    _gen(db, "/g/f1.png", "fallback", LATEST, None)
    # Older than the six windows: not counted anywhere.
    _gen(db, "/g/old.png", "manual", "2026-07-01 12:00:00", "favorite")
    # A favorite on a library image (not a generation) is not a writer stat.
    db.save_media(_media("/lib/x.png"))
    db.set_verdict("/lib/x.png", "favorite", None, NOW)

    rows = db.writer_window_stats(14, 6, now=NOW)
    assert {r["writer_id"] for r in rows} == {"base", "fallback"}
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (1, 0, 0.0)
    fallback = _by(rows, "t2i", "fallback")
    assert len(fallback) == 6
    assert all(_cell(r) == (0, 0, None) for r in fallback.values())


def _div(*values: Optional[float]) -> List[Dict[str, Any]]:
    return [
        {"window_start": s, "n": 0 if v is None else 5, "mean_distance": v}
        for s, v in zip(STARTS[-len(values) :], values)
    ]


def _rates(*pairs: tuple) -> List[Dict[str, Any]]:
    """Per-window (n_seen, n_fav) for one T2I writer, oldest first."""
    out = []
    for s, (n_seen, n_fav) in zip(STARTS[-len(pairs) :], pairs):
        out.append(
            {
                "kind": "t2i",
                "writer_id": "base",
                "window_start": s,
                "n_seen": n_seen,
                "n_fav": n_fav,
                "rate": n_fav / n_seen if n_seen else None,
            }
        )
    return out


def test_collapse_flag_condition():
    rising = _rates((10, 3), (10, 5))
    falling = _rates((10, 5), (10, 3))
    # Latest 0.30 < 0.75 x median(0.5) = 0.375, rate rising -> flag.
    assert collapse_warning(_div(0.5, 0.5, 0.6, 0.4, 0.5, 0.30), rising) is True
    # Same drop, rate falling -> no flag (the writer is just getting worse).
    assert collapse_warning(_div(0.5, 0.5, 0.6, 0.4, 0.5, 0.30), falling) is False
    # Exactly at 0.75 x median is not "below".
    assert collapse_warning(_div(0.5, 0.5, 0.5, 0.5, 0.5, 0.375), rising) is False
    # Only the previous up-to-5 windows count toward the median: with the
    # oldest 0.1 included it would be 0.3 and 0.36 would not flag.
    seven = [
        {"window_start": f"2026-0{m}-01", "n": 5, "mean_distance": v}
        for m, v in zip(range(3, 10), (0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.36))
    ]
    assert collapse_warning(seven, rising) is True
    # Not enough history, or no latest value -> no flag.
    assert collapse_warning(_div(0.3), rising) is False
    assert collapse_warning(_div(0.5, 0.5, None), rising) is False
    assert collapse_warning([], rising) is False
    assert collapse_warning(_div(0.5, 0.5, 0.2), []) is False
    assert collapse_warning(_div(0.5, 0.5, 0.2), _rates((10, 3))) is False
    # Windows with no value (n < 2) are skipped in the median.
    assert collapse_warning(_div(0.5, None, 0.5, 0.2), rising) is True


def test_collapse_rate_pools_t2i_writers_only():
    rows = _rates((10, 5), (10, 4))
    # A second writer lifts the pooled latest rate above the previous one.
    for r in _rates((0, 0), (10, 10)):
        rows.append({**r, "writer_id": "base+taste"})
    assert collapse_warning(_div(0.5, 0.2), rows) is True
    # I2V rows never move the T2I rate.
    i2v = [{**r, "kind": "i2v"} for r in _rates((10, 0), (10, 10))]
    assert collapse_warning(_div(0.5, 0.2), _rates((10, 5), (10, 4)) + i2v) is False


@pytest.fixture
def client(db, tmp_path):
    os.environ.setdefault("METASCAN_API_KEY", "")
    from backend.api import taste
    from backend.services.taste_service import TasteService

    class _Runner:
        training = False

    service = TasteService(
        db, _Runner(), get_config=lambda: {}, broadcast=lambda *a: None
    )
    taste.set_taste_service(service)
    app = FastAPI()
    app.include_router(taste.router)
    with TestClient(app) as tc:
        yield tc
    taste.set_taste_service(None)


def test_writer_stats_route(client, db):
    _gen(db, "/g/a1.png", "base", LATEST, "favorite")
    rows = [{"window_start": s, "n": 3, "mean_distance": 0.5} for s in STARTS[:-1]] + [
        {"window_start": STARTS[-1], "n": 3, "mean_distance": 0.2}
    ]
    db.record_taste_diversity(rows, "2026-10-23T12:00:00+00:00")
    with patch("backend.services.taste_service._utcnow", return_value=NOW):
        body = client.get("/api/taste/writer-stats").json()
    assert set(body) == {"window_starts", "windows", "diversity", "collapse_warning"}
    assert body["window_starts"] == STARTS
    base = _by(body["windows"], "t2i", "base")
    assert _cell(base["2026-10-10"]) == (1, 1, 1.0)
    assert [d["mean_distance"] for d in body["diversity"]] == [0.5] * 5 + [0.2]
    # Diversity collapsed, but there is no earlier rate to compare with.
    assert body["collapse_warning"] is False


def test_latest_diversity_is_the_newest_run(db):
    db.record_taste_diversity(
        [{"window_start": "2026-09-01", "n": 4, "mean_distance": 0.4}],
        "2026-09-14T00:00:00+00:00",
    )
    assert db.latest_taste_diversity() == [
        {"window_start": "2026-09-01", "n": 4, "mean_distance": 0.4}
    ]
    db.record_taste_diversity(
        [
            {"window_start": "2026-09-02", "n": 1, "mean_distance": None},
            {"window_start": "2026-09-16", "n": 3, "mean_distance": 0.3},
        ],
        "2026-09-29T00:00:00+00:00",
    )
    assert db.latest_taste_diversity() == [
        {"window_start": "2026-09-02", "n": 1, "mean_distance": None},
        {"window_start": "2026-09-16", "n": 3, "mean_distance": 0.3},
    ]
