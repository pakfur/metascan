"""Pass rate per writer per 14-day window, ✓ diversity, the collapse flag,
and the ``/api/taste/writer-stats`` route (synthetic data, isolated temp DB).

Only a grade is an outcome: the rate is ✓ pass / (✓ pass + ✗ fail) among
GRADED generations. ★ favorites and views count for nothing."""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import Grade
from metascan.core.media import Media
from metascan.core.taste_labels import collapse_warning, window_starts
from metascan.core.taste_runner import _diversity_task

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


def _rate(
    db: DatabaseManager,
    path: str,
    grade: Optional[Grade],
    favorite: bool = False,
    seen: bool = False,
) -> None:
    """A view, ★ and a grade, each set on its own (they are independent)."""
    if seen:
        assert db.mark_seen(path, NOW) is not None
    if favorite:
        assert db.set_favorite(Path(path), True)
    if grade is not None:
        assert db.set_grade(path, grade, None, NOW) is not None


def _gen(
    db: DatabaseManager,
    path: str,
    writer: Optional[str],
    created_at: str,
    grade: Optional[Grade] = None,
    *,
    favorite: bool = False,
    seen: bool = False,
    writer_model: Optional[str] = None,
) -> int:
    """A T2I generation at ``created_at``, graded ``grade`` (✓ ``'pass'`` /
    ✗ ``'fail'`` / None), ★ and viewed as asked."""
    db.save_media(_media(path))
    image_id = db.create_t2i_image(
        file_path=path, writer_id=writer, writer_model=writer_model
    )
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?", (created_at, image_id)
        )
        conn.commit()
    _rate(db, path, grade, favorite, seen)
    return image_id


def _video(
    db: DatabaseManager,
    path: str,
    writer: str,
    created_at: str,
    grade: Optional[Grade] = None,
    *,
    favorite: bool = False,
    seen: bool = False,
) -> int:
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
    _rate(db, path, grade, favorite, seen)
    return vid


def _by(rows: List[Dict[str, Any]], kind: str, writer: str) -> Dict[str, Any]:
    picked = [r for r in rows if r["kind"] == kind and r["writer_id"] == writer]
    return {r["window_start"]: r for r in picked}


def _cell(row: Dict[str, Any]) -> tuple:
    return (row["n_graded"], row["n_pass"], row["rate"])


def test_window_starts_count_back_from_today():
    assert window_starts(NOW, 14, 6) == STARTS
    # The latest window always ends today (inclusive).
    assert window_starts(datetime(2026, 10, 10, tzinfo=timezone.utc), 14, 1) == [
        "2026-09-27"
    ]


@pytest.mark.parametrize("deleted", [False, True], ids=["live", "deleted"])
def test_rate_is_pass_over_graded(db, deleted):
    """``n_graded`` counts ✓ and ✗, ``n_pass`` the ✓: a view or a ★ alone
    is neither, ★ + ✗ is a fail and ★ + ✓ a pass. A deleted generation
    counts by its tombstone's grade, exactly as it did live."""
    images = [
        _gen(db, "/g/pass.png", "base", LATEST, "pass"),
        _gen(db, "/g/seen_pass.png", "base", LATEST, "pass", seen=True),
        _gen(db, "/g/star_pass.png", "base", LATEST, "pass", favorite=True),
        _gen(db, "/g/fail.png", "base", LATEST, "fail"),
        _gen(db, "/g/star_fail.png", "base", LATEST, "fail", favorite=True),
        _gen(db, "/g/star.png", "base", LATEST, favorite=True),
        _gen(db, "/g/seen.png", "base", LATEST, seen=True),
        _gen(db, "/g/star_seen.png", "base", LATEST, favorite=True, seen=True),
        _gen(db, "/g/untouched.png", "base", LATEST),
        # Graded, then the grade cleared: no grade.
        _gen(db, "/g/cleared.png", "base", LATEST, "pass"),
    ]
    assert db.set_grade("/g/cleared.png", None, None, NOW) is not None
    videos = [
        _video(db, "/g/v_pass.mp4", "base", LATEST, "pass"),
        _video(db, "/g/v_fail.mp4", "base", LATEST, "fail"),
        _video(db, "/g/v_star_fail.mp4", "base", LATEST, "fail", favorite=True),
        _video(db, "/g/v_star.mp4", "base", LATEST, favorite=True),
        _video(db, "/g/v_seen.mp4", "base", LATEST, seen=True),
    ]
    if deleted:
        for image_id in images:
            assert db.delete_t2i_image(image_id)[0]
        for video_id in videos:
            assert db.delete_i2v_video(video_id)[0]
        assert {g["tombstone"] for g in db.generation_outcomes()} == {True}

    rows = db.writer_window_stats(14, 6, now=NOW)
    # ✓, ✓ + seen, ★ + ✓, ✗ and ★ + ✗: 3 passes of 5 graded.
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (5, 3, 0.6)
    # Clips the same way, on their own: ✓, ✗ and ★ + ✗.
    assert _cell(_by(rows, "i2v", "base")["2026-10-10"]) == (
        3,
        1,
        pytest.approx(1 / 3),
    )


def test_rates_per_writer_and_window(db):
    _gen(db, "/g/a1.png", "base", LATEST, "pass")
    _gen(db, "/g/a2.png", "base", LATEST, "fail")
    _gen(db, "/g/a3.png", "base", LATEST, "fail")
    _gen(db, "/g/a4.png", "base", "2026-10-23T08:00:00+00:00", "fail")
    # Window boundary: midnight UTC on the 10th opens the latest window.
    _gen(db, "/g/b0.png", "base", "2026-10-10 00:00:00", "fail")
    _gen(db, "/g/b1.png", "base", PREVIOUS, "pass")
    _gen(db, "/g/b2.png", "base", "2026-10-09 23:59:59", "pass")
    _gen(db, "/g/c1.png", "base+taste", LATEST, "pass")
    # Deleted after being graded: the tombstone keeps it in the stats
    # (otherwise deleting failures would inflate the rate).
    _gen(db, "/g/c2.png", "base+taste", LATEST, "fail")
    db.delete_media(Path("/g/c2.png"))
    c3 = _gen(db, "/g/c3.png", "base+taste", LATEST, "fail")
    db.delete_t2i_image(c3)
    _gen(db, "/g/d1.png", None, LATEST, "pass")
    _video(db, "/g/v1.mp4", "base", LATEST, "pass")

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


def test_ungraded_excluded(db):
    _gen(db, "/g/s1.png", "base", LATEST, "fail")
    _gen(db, "/g/u1.png", "base", LATEST, seen=True)
    _gen(db, "/g/u2.png", "base", LATEST)
    # A writer whose output was never graded (only starred and viewed):
    # listed, but no rate.
    _gen(db, "/g/f1.png", "fallback", LATEST, favorite=True, seen=True)
    # Older than the six windows: not counted anywhere.
    _gen(db, "/g/old.png", "manual", "2026-07-01 12:00:00", "pass")
    # A ✓ on a library image (not a generation) is not a writer stat.
    db.save_media(_media("/lib/x.png"))
    _rate(db, "/lib/x.png", "pass")

    rows = db.writer_window_stats(14, 6, now=NOW)
    assert {r["writer_id"] for r in rows} == {"base", "fallback"}
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (1, 0, 0.0)
    fallback = _by(rows, "t2i", "fallback")
    assert len(fallback) == 6
    assert all(_cell(r) == (0, 0, None) for r in fallback.values())


def test_diversity_uses_passed_generations(db):
    """The worker's diversity input is every ✓ T2I generation of the six
    windows, live or deleted (a deleted one brings only its tombstone's
    vector): ★, views, ✗ and cleared grades choose nothing; nor do clips,
    scorecard images or generations outside the windows."""
    _gen(db, "/g/pass.png", "base", LATEST, "pass")
    _gen(db, "/g/star_pass.png", "base", LATEST, "pass", favorite=True)
    _gen(db, "/g/prev_pass.png", "base+taste", PREVIOUS, "pass", seen=True)
    _gen(db, "/g/star.png", "base", LATEST, favorite=True)
    _gen(db, "/g/star_fail.png", "base", LATEST, "fail", favorite=True)
    _gen(db, "/g/fail.png", "base", LATEST, "fail")
    _gen(db, "/g/seen.png", "base", LATEST, seen=True)
    _gen(db, "/g/cleared.png", "base", LATEST, "pass")
    assert db.set_grade("/g/cleared.png", None, None, NOW) is not None
    _gen(db, "/g/old_pass.png", "base", "2026-07-01 12:00:00", "pass")
    _video(db, "/g/v_pass.mp4", "base", LATEST, "pass")
    card = _gen(db, "/g/card_pass.png", "adapter:7", LATEST, "pass")
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET scorecard_id = 'card' WHERE id = ?", (card,)
        )
        conn.commit()
    vec = np.arange(32, dtype=np.float32)
    gone = _gen(db, "/g/gone_pass.png", "base", LATEST, "pass")
    gone_star = _gen(db, "/g/gone_star.png", "base", LATEST, favorite=True)
    db.mark_embedded(["/g/gone_pass.png", "/g/gone_star.png"], "small")
    db.set_embedding_lookup(lambda p: (vec, "small", float("inf")))
    assert db.delete_t2i_image(gone)[0]
    assert db.delete_t2i_image(gone_star)[0]
    db.set_embedding_lookup(None)

    task = _diversity_task(db, NOW, "small")
    assert task["window_starts"] == STARTS
    items = {i["file_path"]: i for i in task["items"]}
    assert sorted(items) == [
        "/g/gone_pass.png",
        "/g/pass.png",
        "/g/prev_pass.png",
        "/g/star_pass.png",
    ]
    assert items["/g/prev_pass.png"]["window_start"] == "2026-09-26"
    assert {items[p]["window_start"] for p in items if p != "/g/prev_pass.png"} == {
        "2026-10-10"
    }
    # A live item may be looked up by its path; a deleted one carries its
    # own vector and is marked, so the worker never reads it by path.
    live = items["/g/pass.png"]
    assert live["tombstone"] is False and live["embedding"] is None
    deleted = items["/g/gone_pass.png"]
    assert deleted["tombstone"] is True and deleted["embedding_model"] == "small"
    assert base64.b64decode(deleted["embedding"]) == vec.tobytes()
    # Only the vector for the index's CLIP model rides along.
    other = {i["file_path"]: i for i in _diversity_task(db, NOW, "large")["items"]}[
        "/g/gone_pass.png"
    ]
    assert other["embedding"] is None and other["embedding_model"] is None


def _div(*values: Optional[float]) -> List[Dict[str, Any]]:
    return [
        {"window_start": s, "n": 0 if v is None else 5, "mean_distance": v}
        for s, v in zip(STARTS[-len(values) :], values)
    ]


def _rates(*pairs: tuple) -> List[Dict[str, Any]]:
    """Per-window (n_graded, n_pass) for one T2I writer, oldest first."""
    out = []
    for s, (n_graded, n_pass) in zip(STARTS[-len(pairs) :], pairs):
        out.append(
            {
                "kind": "t2i",
                "writer_id": "base",
                "window_start": s,
                "n_graded": n_graded,
                "n_pass": n_pass,
                "rate": n_pass / n_graded if n_graded else None,
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
        {"window_start": w, "n": 5, "mean_distance": v}
        for w, v in zip(window_starts(NOW, 14, 7), (0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.36))
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


def test_collapse_needs_five_passes_in_the_latest_window():
    """Two or three ✓ images give a mean distance, but far too noisy to
    call a collapse: the latest window needs n >= 5 ✓ images with vectors."""
    rising = _rates((10, 3), (10, 5))
    collapsed = _div(0.5, 0.5, 0.6, 0.4, 0.5, 0.30)
    for n, flag in ((2, False), (4, False), (5, True), (9, True)):
        latest = {**collapsed[-1], "n": n}
        assert collapse_warning(collapsed[:-1] + [latest], rising) is flag, n
    # Earlier windows' n does not gate the flag.
    thin_history = [{**d, "n": 2} for d in collapsed[:-1]] + [collapsed[-1]]
    assert collapse_warning(thin_history, rising) is True


def _shift(div: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    """``div`` as a training run ``days`` days ago would have stored it."""
    out = []
    for d in div:
        start = datetime.fromisoformat(d["window_start"]) - timedelta(days=days)
        out.append({**d, "window_start": start.date().isoformat()})
    return out


def test_collapse_needs_aligned_windows():
    rising = _rates((10, 3), (10, 5))
    collapsed = _div(0.5, 0.5, 0.6, 0.4, 0.5, 0.30)
    # Diversity from a run a few days ago still covers the latest window.
    assert collapse_warning(_shift(collapsed, 3), rising) is True
    assert collapse_warning(_shift(collapsed, 13), rising) is True
    # Training idle for a whole window or more: the latest diversity is
    # about a different period than the latest rate -> never flag.
    assert collapse_warning(_shift(collapsed, 14), rising) is False
    assert collapse_warning(_shift(collapsed, 30), rising) is False
    # Diversity newer than the rates is just as unaligned.
    assert collapse_warning(_shift(collapsed, -14), rising) is False
    assert collapse_warning(_shift(collapsed, 20), rising, window_days=28) is True


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
def client(db, tmp_path, monkeypatch):
    monkeypatch.setenv("METASCAN_API_KEY", "")
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


def _writer_stats(client) -> Dict[str, Any]:
    with patch("backend.services.taste_service._utcnow", return_value=NOW):
        body: Dict[str, Any] = client.get("/api/taste/writer-stats").json()
    return body


def test_writer_stats_route(client, db):
    _gen(db, "/g/a1.png", "base", LATEST, "pass")
    rows = [{"window_start": s, "n": 3, "mean_distance": 0.5} for s in STARTS[:-1]] + [
        {"window_start": STARTS[-1], "n": 3, "mean_distance": 0.2}
    ]
    db.record_taste_diversity(rows, "2026-10-23T12:00:00+00:00")
    body = _writer_stats(client)
    assert set(body) == {
        "window_days",
        "window_starts",
        "windows",
        "diversity",
        "collapse_warning",
        "scorecards",
    }
    assert body["window_days"] == 14
    assert body["window_starts"] == STARTS
    base = _by(body["windows"], "t2i", "base")
    assert set(base["2026-10-10"]) == {
        "kind",
        "writer_id",
        "writer_model",
        "window_start",
        "n_graded",
        "n_pass",
        "rate",
    }
    assert _cell(base["2026-10-10"]) == (1, 1, 1.0)
    assert [d["mean_distance"] for d in body["diversity"]] == [0.5] * 5 + [0.2]
    assert {d["computed_at"] for d in body["diversity"]} == {
        "2026-10-23T12:00:00+00:00"
    }
    # Diversity collapsed, but there is no earlier rate to compare with.
    assert body["collapse_warning"] is False
    assert body["scorecards"] == []


def test_collapse_reads_pass_rate(client, db):
    """The collapse flag compares the pooled T2I PASS rate (✓ of graded).
    Here it rises 1/4 -> 2/4 while the favorite rate among seen generations
    falls 4/8 -> 0/8: only grades may decide."""
    db.record_taste_diversity(
        _div(0.5, 0.5, 0.6, 0.4, 0.5, 0.30), "2026-10-23T12:00:00+00:00"
    )
    _gen(db, "/g/p_pass.png", "base", PREVIOUS, "pass")
    for i in range(3):
        _gen(db, f"/g/p_fail{i}.png", "base", PREVIOUS, "fail")
    for i in range(4):
        _gen(db, f"/g/p_star{i}.png", "base", PREVIOUS, favorite=True, seen=True)
    for i in range(2):
        _gen(db, f"/g/l_pass{i}.png", "base+taste", LATEST, "pass")
        _gen(db, f"/g/l_fail{i}.png", "base+taste", LATEST, "fail")
    for i in range(4):
        _gen(db, f"/g/l_seen{i}.png", "base", LATEST, seen=True)
    assert _writer_stats(client)["collapse_warning"] is True
    # ★ on the earlier ✗ makes them no pass: the rate still rises.
    for i in range(3):
        assert db.set_favorite(Path(f"/g/p_fail{i}.png"), True)
    assert _writer_stats(client)["collapse_warning"] is True
    # The latest ✓ failed after all: the pass rate falls, no flag.
    for i in range(2):
        assert db.set_grade(f"/g/l_pass{i}.png", "fail", None, NOW) is not None
    assert _writer_stats(client)["collapse_warning"] is False


def test_latest_diversity_is_the_newest_run(db):
    db.record_taste_diversity(
        [{"window_start": "2026-09-01", "n": 4, "mean_distance": 0.4}],
        "2026-09-14T00:00:00+00:00",
    )
    first = "2026-09-14T00:00:00+00:00"
    assert db.latest_taste_diversity() == [
        {
            "window_start": "2026-09-01",
            "n": 4,
            "mean_distance": 0.4,
            "computed_at": first,
        }
    ]
    db.record_taste_diversity(
        [
            {"window_start": "2026-09-02", "n": 1, "mean_distance": None},
            {"window_start": "2026-09-16", "n": 3, "mean_distance": 0.3},
        ],
        "2026-09-29T00:00:00+00:00",
    )
    second = "2026-09-29T00:00:00+00:00"
    assert db.latest_taste_diversity() == [
        {
            "window_start": "2026-09-02",
            "n": 1,
            "mean_distance": None,
            "computed_at": second,
        },
        {
            "window_start": "2026-09-16",
            "n": 3,
            "mean_distance": 0.3,
            "computed_at": second,
        },
    ]


def test_readded_path_counted_once(db):
    # Graded, deleted (tombstone), then the file came back: the live row
    # wins and the generation counts once, with its new grade.
    _gen(db, "/g/r1.png", "base", LATEST, "pass")
    db.delete_media(Path("/g/r1.png"))
    db.save_media(_media("/g/r1.png"))
    # Until it is graded again the live copy has no grade, so it counts
    # nowhere (the writer dataset reads generations the same way).
    rows = db.writer_window_stats(14, 6, now=NOW)
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (0, 0, None)
    _rate(db, "/g/r1.png", "fail")
    rows = db.writer_window_stats(14, 6, now=NOW)
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (1, 0, 0.0)


def _tombstone(
    db: DatabaseManager, path: str, lineage: str, grade: Grade = "pass"
) -> None:
    """A deleted ``grade`` item at ``path`` whose tombstone carries the raw
    ``lineage`` text (possibly malformed)."""
    db.save_media(_media(path))
    _rate(db, path, grade)
    assert db.delete_media(Path(path))
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media_tombstones SET lineage = ? WHERE id = "
            "(SELECT MAX(id) FROM media_tombstones WHERE file_path = ?)",
            (lineage, path),
        )
        conn.commit()


def test_malformed_lineage_is_skipped(db, caplog):
    _gen(db, "/g/ok.png", "base", LATEST, "fail")
    _tombstone(db, "/g/bad1.png", "not json")
    _tombstone(db, "/g/bad2.png", "[1, 2]")
    _tombstone(db, "/g/bad3.png", '{"t2i": "nope"}')
    _tombstone(db, "/g/bad4.png", '{"t2i": {"writer_id": "base"}}')
    _tombstone(
        db,
        "/g/good.png",
        '{"t2i": {"writer_id": "base", "created_at": "%s"}}' % LATEST,
    )
    rows = db.writer_window_stats(14, 6, now=NOW)
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (2, 1, 0.5)
    with caplog.at_level("DEBUG", logger="metascan.core.database_sqlite"):
        every = db.generation_outcomes()
    assert sorted(g["file_path"] for g in every) == ["/g/good.png", "/g/ok.png"]
    assert "malformed tombstone lineage" in caplog.text


def test_tombstones_filtered_by_since_in_sql(db):
    _tombstone(
        db, "/g/old.png", '{"t2i": {"writer_id": "base", "created_at": "2026-01-01"}}'
    )
    _tombstone(
        db, "/g/new.png", '{"i2v": {"writer_id": "base", "created_at": "%s"}}' % LATEST
    )
    paths = [g["file_path"] for g in db.generation_outcomes(since="2026-08-01")]
    assert paths == ["/g/new.png"]


def test_rates_are_per_writer_model(db):
    """Writer lineage includes the model: a ``base`` draft from another
    model never mixes into the 8B baseline, live or deleted."""
    _gen(db, "/g/m1.png", "base", LATEST, "pass", writer_model="qwen3vl-8b")
    _gen(db, "/g/m2.png", "base", LATEST, "fail", writer_model="qwen3vl-8b")
    _gen(db, "/g/m3.png", "base", LATEST, "pass", writer_model="qwen3vl-4b")
    _gen(db, "/g/m4.png", "base", LATEST, "fail", writer_model="qwen3vl-4b")
    db.delete_media(Path("/g/m4.png"))  # the tombstone keeps its model
    _gen(db, "/g/m5.png", "base", LATEST, "fail")  # older row: no model
    db.save_media(_media("/g/src.png"))
    db.save_media(_media("/g/v.mp4"))
    db.create_i2v_video(
        source_path="/g/src.png",
        file_path="/g/v.mp4",
        writer_id="base",
        writer_model="qwen3vl-8b",
    )
    rows = db.writer_window_stats(14, 6, now=NOW)
    keyed = {
        (r["kind"], r["writer_id"], r["writer_model"])
        for r in rows
        if r["window_start"] == "2026-10-10"
    }
    assert ("t2i", "base", "qwen3vl-8b") in keyed
    assert ("t2i", "base", "qwen3vl-4b") in keyed
    assert ("t2i", "base", None) in keyed

    def cell(model: Optional[str]) -> tuple:
        (row,) = [
            r
            for r in rows
            if r["kind"] == "t2i"
            and r["writer_model"] == model
            and r["window_start"] == "2026-10-10"
        ]
        return _cell(row)

    assert cell("qwen3vl-8b") == (2, 1, 0.5)
    assert cell("qwen3vl-4b") == (2, 1, 0.5)
    assert cell(None) == (1, 0, 0.0)
    outcomes = {o["file_path"]: o for o in db.generation_outcomes()}
    assert outcomes["/g/m4.png"]["writer_model"] == "qwen3vl-4b"
    assert outcomes["/g/v.mp4"]["writer_model"] == "qwen3vl-8b"


def test_scorecard_images_are_not_writer_stats(db):
    # Scorecard renders have their own results rows (and are blind while
    # ungraded): never counted in the writer dashboard, live or deleted.
    _gen(db, "/g/ok.png", "base", LATEST, "fail")
    for i in range(2):
        path = f"/g/sc{i}.png"
        image_id = _gen(db, path, "adapter:7", LATEST, "pass")
        with db.lock, db._get_connection() as conn:
            conn.execute(
                "UPDATE t2i_images SET scorecard_id = 'card' WHERE id = ?",
                (image_id,),
            )
            conn.commit()
    db.delete_media(Path("/g/sc1.png"))  # tombstone lineage carries it
    _tombstone(
        db,
        "/g/sc2.png",
        '{"t2i": {"writer_id": "base", "created_at": "%s", "scorecard_id": "c"}}'
        % LATEST,
    )
    paths = sorted(g["file_path"] for g in db.generation_outcomes())
    assert paths == ["/g/ok.png"]
    rows = db.writer_window_stats(14, 6, now=NOW)
    assert {r["writer_id"] for r in rows} == {"base"}
    assert _cell(_by(rows, "t2i", "base")["2026-10-10"]) == (1, 0, 0.0)
