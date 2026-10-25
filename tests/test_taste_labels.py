"""Training labels, weights and pairs for the taste scorer, plus its tables.

Only a grade is a label (✓ pass -> 1, ✗ fail -> 0): ★ favorites, views
and categories never are."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.taste_labels import (
    MIN_NEGATIVES,
    MIN_POSITIVES,
    TRAINING_EVENT_KINDS,
    build_label_set,
    count_labels,
    decay_weight,
    missing_for_training,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


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


def _labels(db, now=T0, half_life=180, vector_model=None):
    return build_label_set(db.taste_training_raw(vector_model), now, half_life)


# (event, score_shown, created_at)
LegacyEvent = Tuple[str, Optional[float], str]


def _migrated(
    db,
    path: str,
    grade: str,
    events: List[LegacyEvent],
    at: datetime = T0,
    favorite: bool = False,
) -> None:
    """A rating as the v6 migration leaves it: the judgment row's grade set
    from the old verdict (a favorite -> ✓, the old reject "pass" -> ✗), its
    timestamps and the legacy events untouched, and no grade_* event."""
    db.save_media(_media(path))
    ts = at.isoformat()
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media SET is_favorite = ? WHERE file_path = ?",
            (1 if favorite else 0, path),
        )
        conn.execute(
            "INSERT INTO media_judgments (file_path, passed, grade, reasons, "
            "seen_at, judged_at, confirmed_at) VALUES (?, ?, ?, '[]', ?, ?, ?)",
            (path, 1 if grade == "fail" else 0, grade, ts, ts, ts),
        )
        conn.executemany(
            "INSERT INTO judgment_events (file_path, event, score_shown, "
            "created_at) VALUES (?, ?, ?, ?)",
            [(path, event, score, when) for event, score, when in events],
        )
        conn.commit()


def test_constants():
    assert MIN_POSITIVES == 20 and MIN_NEGATIVES == 20
    assert TRAINING_EVENT_KINDS == (
        "grade_pass",
        "grade_fail",
        "grade_clear",
        "reason_on",
        "reason_off",
        "category_on",
        "category_off",
    )


def test_decay_halves_at_half_life():
    assert decay_weight("2025-07-05T00:00:00+00:00", T0, 180) == pytest.approx(
        0.5, rel=1e-3
    )


def test_decay_tolerates_naive_and_future():
    assert decay_weight("2026-01-01T00:00:00", T0, 180) == pytest.approx(1.0)
    assert decay_weight("2026-02-01T00:00:00+00:00", T0, 180) == 1.0


@pytest.mark.parametrize("deleted", [False, True], ids=["live", "deleted"])
def test_only_grades_are_labels(db, deleted):
    names = (
        "star",
        "seen",
        "star_seen",
        "cleared",
        "ok",
        "bad",
        "star_bad",
        "unseen",
    )
    p = {n: f"/l/{n}.png" for n in names}
    for path in [*p.values(), "/l/vid.mp4"]:
        db.save_media(_media(path))
    db.set_favorite_state(p["star"], True, now=T0)
    db.mark_seen(p["seen"], now=T0, score_shown=0.4)
    db.set_favorite_state(p["star_seen"], True, now=T0)
    db.mark_seen(p["star_seen"], now=T0)
    db.set_grade(p["cleared"], "pass", now=T0)
    db.set_grade(p["cleared"], None, now=T0)
    db.set_grade(p["ok"], "pass", now=T0)
    db.set_grade(p["bad"], "fail", now=T0)
    db.set_favorite_state(p["star_bad"], True, now=T0)
    db.set_grade(p["star_bad"], "fail", now=T0)
    db.set_grade("/l/vid.mp4", "pass", now=T0)  # videos are never labels
    if deleted:
        for path in [*p.values(), "/l/vid.mp4"]:
            db.delete_media(Path(path))
    ls = _labels(db)
    assert {r.file_path: r.label for r in ls.rows} == {
        p["ok"]: 1,
        p["bad"]: 0,
        p["star_bad"]: 0,
    }
    assert (ls.n_pos, ls.n_neg) == (1, 2)
    assert count_labels(db.taste_label_counts_raw(), T0) == (1, 2)


def test_score_shown_halves_weight(db):
    for path in ("/l/a.png", "/l/b.png", "/l/c.png"):
        db.save_media(_media(path))
    db.set_grade("/l/a.png", "pass", score_shown=0.8, now=T0)
    db.set_grade("/l/b.png", "pass", now=T0)
    db.set_grade("/l/c.png", "fail", score_shown=0.2, now=T0)
    w = {r.file_path: r.weight for r in _labels(db).rows}
    assert w["/l/a.png"] == pytest.approx(0.5)
    assert w["/l/b.png"] == pytest.approx(1.0)
    assert w["/l/c.png"] == pytest.approx(0.5)


def test_regrading_resets_the_clock(db):
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", now=T0 - timedelta(days=360))
    old = _labels(db).rows[0].weight
    assert old == pytest.approx(0.25, rel=1e-3)
    db.set_grade("/l/a.png", "pass", now=T0)
    assert _labels(db).rows[0].weight == pytest.approx(1.0)


def test_seen_score_never_weights_a_grade(db):
    db.save_media(_media("/l/a.png"))
    db.mark_seen("/l/a.png", now=T0, score_shown=0.9)
    db.set_grade("/l/a.png", "pass", now=T0)  # no score on the grade
    (row,) = _labels(db).rows
    assert row.label == 1 and row.weight == pytest.approx(1.0)


def test_batch_pairs_are_pass_vs_fail(db):
    for n in ("ok", "bad", "seen", "star"):
        path = f"/l/{n}.png"
        db.save_media(_media(path))
        db.create_t2i_image(file_path=path, batch_id="b1")
    db.save_media(_media("/l/other.png"))
    db.create_t2i_image(file_path="/l/other.png", batch_id="b2")
    db.set_grade("/l/ok.png", "pass", now=T0)
    db.set_grade("/l/bad.png", "fail", now=T0 - timedelta(days=180))
    db.mark_seen("/l/seen.png", now=T0)
    db.set_favorite_state("/l/star.png", True, now=T0)
    db.set_grade("/l/other.png", "pass", now=T0)
    ls = _labels(db)
    idx = {r.file_path: i for i, r in enumerate(ls.rows)}
    assert set(idx) == {"/l/ok.png", "/l/bad.png", "/l/other.png"}
    assert [(a, b) for a, b, _ in ls.pairs] == [(idx["/l/ok.png"], idx["/l/bad.png"])]
    assert ls.pairs[0][2] == pytest.approx(0.5, rel=1e-3)
    assert ls.rows[idx["/l/ok.png"]].batch_id == "b1"


def test_tombstone_rows_carry_embedding(db):
    vec = np.ones(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-large"))
    db.save_media(_media("/l/gone.png"))
    db.save_media(_media("/l/gone.mp4"))
    db.create_t2i_image(file_path="/l/gone.png", batch_id="b9")
    db.set_grade("/l/gone.png", "pass", now=T0)
    db.set_grade("/l/gone.mp4", "pass", now=T0)
    db.delete_media(Path("/l/gone.png"))
    db.delete_media(Path("/l/gone.mp4"))
    # Rows carry the vector stored for the index's CLIP model only.
    assert _labels(db).rows[0].embedding is None
    assert _labels(db, vector_model="clip-small").rows[0].embedding is None
    ls = _labels(db, vector_model="clip-large")
    assert len(ls.rows) == 1
    r = ls.rows[0]
    tomb_id = db.list_tombstones()[0]["id"]
    assert r.key == f"tombstone:{tomb_id}" and r.tombstone is True
    assert r.file_path == "/l/gone.png"
    assert r.label == 1 and r.batch_id == "b9"
    assert r.embedding == vec.tobytes() and r.embedding_model == "clip-large"


def test_live_rows_have_no_embedding(db):
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", now=T0)
    r = _labels(db).rows[0]
    assert r.key == "/l/a.png" and r.embedding is None and r.tombstone is False


def test_categories_on_rows(db):
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", now=T0)
    cat = db.create_category("moody")
    db.add_media_categories(["/l/a.png"], cat.id, "viewer")
    assert _labels(db).rows[0].categories == ["moody"]


def test_missing_for_training():
    assert missing_for_training(5, 30) == {"positives": 15, "negatives": 0}
    assert missing_for_training(20, 20) is None


# ---- events that count toward a retrain ------------------------------------


def test_event_count_since(db):
    db.save_media(_media("/l/a.png"))
    assert db.count_training_events_since(None) == (0, None)
    db.mark_seen("/l/a.png", now=T0)
    assert db.count_training_events_since(None) == (0, None)
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    db.set_grade("/l/a.png", "pass", now=t1)
    db.set_reason("/l/a.png", "light", True, now=t2)
    count, newest = db.count_training_events_since(None)
    assert count == 2 and newest == t2.isoformat()
    assert db.count_training_events_since(t1.isoformat()) == (1, t2.isoformat())
    assert db.count_training_events_since(t2.isoformat()) == (0, None)


def test_every_grade_reason_and_category_event_counts(db):
    db.save_media(_media("/l/a.png"))
    cat = db.create_category("moody")
    db.set_grade("/l/a.png", "pass", now=T0)
    db.set_reason("/l/a.png", "light", True, now=T0)
    db.set_reason("/l/a.png", "light", False, now=T0)
    db.add_media_categories(["/l/a.png"], cat.id, "viewer")
    db.remove_media_categories(["/l/a.png"], cat.id)
    db.set_grade("/l/a.png", "fail", now=T0)
    db.set_grade("/l/a.png", None, now=T0)
    kinds = [e["event"] for e in db.list_judgment_events("/l/a.png")]
    assert sorted(set(kinds)) == sorted(TRAINING_EVENT_KINDS)
    assert db.count_training_events_since(None)[0] == len(kinds) == 7


def test_favorite_view_and_legacy_events_are_not_counted(db):
    db.save_media(_media("/l/a.png"))
    for i in range(30):
        db.set_favorite_state("/l/a.png", i % 2 == 0, now=T0 + timedelta(seconds=i))
    db.mark_seen("/l/a.png", now=T0)
    # Pre-grades events left in the log mean nothing to a grade model.
    with db.lock, db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO judgment_events (file_path, event, created_at) "
            "VALUES (?, ?, ?)",
            [("/l/a.png", e, T0.isoformat()) for e in ("pass", "unpass", "reconfirm")],
        )
        conn.commit()
    assert db.count_training_events_since(None) == (0, None)


def test_video_events_are_not_counted(db):
    # The scorer is images only, so a video's events never make it due.
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "fail", now=T0)
    later = T0 + timedelta(minutes=1)
    cat = db.create_category("motion")
    for path in ("/l/v.mp4", "/l/v.webm", "/l/v.MOV"):
        db.save_media(_media(path))
        db.set_grade(path, "pass", now=later)
        db.set_reason(path, "motion", True, now=later)
    db.add_media_categories(["/l/v.mp4"], cat.id, "viewer")
    assert db.count_training_events_since(None) == (1, T0.isoformat())
    assert db.count_training_events_since(T0.isoformat()) == (0, None)


# ---- models and scores -----------------------------------------------------


def _model_row(**over):
    row = dict(
        created_at="2026-01-01T00:00:00+00:00",
        labels_as_of="2026-01-01T00:00:00+00:00",
        embedding_model="clip-large",
        dim=32,
        categories=["moody"],
        n_pos=20,
        n_neg=20,
        n_pairs=5,
        holdout_auc=0.7,
        half_life_days=180.0,
        params_path="/x/p.npz",
        labels_version=2,
    )
    row.update(over)
    return row


def test_replace_scores_and_active_model(db):
    assert db.get_active_taste_model() is None
    assert db.get_taste_scores() == (None, {})
    db.save_media(_media("/l/a.png"))
    db.save_media(_media("/l/b.png"))
    m1 = db.record_taste_model(_model_row(labels_version=1))
    m2 = db.record_taste_model(_model_row(n_pos=21))
    assert m2 != m1
    active = db.get_active_taste_model()
    assert active["id"] == m2 and active["categories"] == ["moody"]
    assert active["labels_version"] == 2
    models = db.list_taste_models()
    assert [m["id"] for m in models] == [m2, m1]
    assert [m["status"] for m in models] == ["active", "retired"]
    assert [m["labels_version"] for m in models] == [2, 1]

    n = db.replace_taste_scores(
        m2, {"/l/a.png": 0.9, "/l/b.png": 0.1, "/l/zz.png": 0.5}
    )
    assert n == 2
    assert db.get_taste_scores() == (m2, {"/l/a.png": 0.9, "/l/b.png": 0.1})
    assert db.get_taste_scores(["/l/a.png"]) == (m2, {"/l/a.png": 0.9})
    db.replace_taste_scores(m2, {"/l/b.png": 0.3})
    assert db.get_taste_scores() == (m2, {"/l/b.png": 0.3})
    db.delete_media(Path("/l/b.png"))
    assert db.get_taste_scores() == (None, {})


def test_a_model_row_must_say_its_labels_version(db):
    row = _model_row()
    del row["labels_version"]
    with pytest.raises(KeyError):
        db.record_taste_model(row)


# ---- tombstones --------------------------------------------------------------


def _tomb(i, path="/l/a.png", **over):
    d = dict(
        id=i,
        file_path=path,
        grade="pass",
        judged_at="2026-01-01T00:00:00+00:00",
        confirmed_at="2026-01-01T00:00:00+00:00",
        embedding=None,
        embedding_model=None,
        categories=[],
        batch_id="b1",
        grade_score_shown=None,
    )
    d.update(over)
    return d


def _live(path="/l/a.png", **over):
    d = dict(
        file_path=path,
        grade="pass",
        judged_at=None,
        confirmed_at="2026-01-01T00:00:00+00:00",
        grade_score_shown=None,
        categories=[],
        t2i_batch_id="b1",
    )
    d.update(over)
    return d


def test_live_row_beats_tombstone_of_same_path():
    raw = {"media": [_live(grade="fail")], "tombstones": [_tomb(1)]}
    ls = build_label_set(raw, T0, 180)
    assert [(r.key, r.label) for r in ls.rows] == [("/l/a.png", 0)]
    assert ls.pairs == []


def test_a_label_row_must_say_whether_it_is_a_tombstone():
    """The worker reads a vector by path only for a row marked live; a
    default would mark every row of a new source live. So no label row is
    built without the flag."""
    from metascan.core.taste_labels import LabelRow, _label_row

    with pytest.raises(TypeError):
        LabelRow(key="/l/a.png", file_path="/l/a.png", label=1, weight=1.0)
    with pytest.raises(TypeError):
        _label_row(_live(), "/l/a.png", T0, 180)
    row, skipped = _label_row(_tomb(4), "tombstone:4", T0, 180, tombstone=True)
    assert row is not None and row.tombstone is True and not skipped


def test_an_ungraded_live_row_hides_no_tombstone():
    raw = {"media": [_live(grade=None)], "tombstones": [_tomb(1)]}
    ls = build_label_set(raw, T0, 180)
    assert [(r.key, r.label, r.tombstone) for r in ls.rows] == [
        ("tombstone:1", 1, True)
    ]


def test_newest_tombstone_per_path_only():
    raw = {
        "media": [],
        "tombstones": [_tomb(1), _tomb(3, grade="fail"), _tomb(2)],
    }
    ls = build_label_set(raw, T0, 180)
    assert [(r.key, r.label) for r in ls.rows] == [("tombstone:3", 0)]


def test_a_newer_ungraded_tombstone_is_skipped():
    raw = {
        "media": [],
        "tombstones": [_tomb(1, grade="fail"), _tomb(2, grade=None)],
    }
    ls = build_label_set(raw, T0, 180)
    assert [(r.key, r.label) for r in ls.rows] == [("tombstone:1", 0)]


def test_an_ungraded_tombstone_hides_no_older_grade(db):
    # Viewing a re-added copy and deleting it again must not drop the grade
    # the path was deleted with before.
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "fail", now=T0)
    db.delete_media(Path("/l/a.png"))
    db.save_media(_media("/l/a.png"))
    db.mark_seen("/l/a.png", now=T0)
    db.delete_media(Path("/l/a.png"))
    first, second = db.list_tombstones()
    assert first["grade"] == "fail" and second["grade"] is None
    ls = _labels(db)
    assert [(r.key, r.label) for r in ls.rows] == [(f"tombstone:{first['id']}", 0)]
    assert count_labels(db.taste_label_counts_raw(), T0) == (0, 1)


def test_no_self_pairs_after_rescan(db):
    db.save_media(_media("/l/a.png"))
    db.create_t2i_image(file_path="/l/a.png", batch_id="b1")
    db.set_grade("/l/a.png", "pass", now=T0)
    db.delete_media(Path("/l/a.png"))
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "fail", now=T0)
    ls = _labels(db)
    assert [r.label for r in ls.rows] == [0] and ls.pairs == []


def test_tombstone_ignores_events_after_deletion(db):
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", now=T0)
    db.delete_media(Path("/l/a.png"))
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", score_shown=0.9)  # the new file, later
    raw = db.taste_training_raw()
    assert raw["tombstones"][0]["grade_score_shown"] is None
    assert raw["media"][0]["grade_score_shown"] == 0.9


def test_unparseable_and_empty_timestamps_skipped_with_warning(caplog):
    raw = {
        "media": [
            _live("/l/x.png", confirmed_at=None),
            _live("/l/y.png", confirmed_at="not-a-date"),
            _live("/l/ok.png"),
            _live("/l/judged.png", confirmed_at=None, judged_at="2026-01-01"),
        ],
        "tombstones": [_tomb(1, "/l/z.png", confirmed_at="nope")],
    }
    with caplog.at_level("WARNING", logger="metascan.core.taste_labels"):
        ls = build_label_set(raw, T0, 180)
    assert [r.file_path for r in ls.rows] == ["/l/ok.png", "/l/judged.png"]
    assert "skipped 3" in caplog.text


def test_no_warning_when_nothing_skipped(caplog):
    with caplog.at_level("WARNING", logger="metascan.core.taste_labels"):
        build_label_set({"media": [_live()], "tombstones": []}, T0, 180)
    assert caplog.text == ""


def test_a_deleted_grade_keeps_its_judged_at(db):
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "fail", now=T0)
    db.set_grade("/l/a.png", "fail", now=T0 + timedelta(days=3))  # confirmed
    db.delete_media(Path("/l/a.png"))
    (tomb,) = db.list_tombstones()
    assert tomb["judged_at"] == T0.isoformat()
    assert tomb["confirmed_at"] == (T0 + timedelta(days=3)).isoformat()


# ---- migrated grades: the historical score_shown halving ---------------------


def test_migrated_grade_keeps_historical_score_shown_halving(db):
    j = T0.isoformat()
    before = (T0 - timedelta(days=1)).isoformat()
    # ✓ from a favorite given while a score was on screen, and one without.
    _migrated(db, "/l/fav_shown.png", "pass", [("favorite", 0.71, j)], favorite=True)
    _migrated(db, "/l/fav_plain.png", "pass", [("favorite", None, j)], favorite=True)
    # ✗ from the old reject ("pass"): its unfavorite + pass carried the score.
    _migrated(
        db,
        "/l/rej_shown.png",
        "fail",
        [("favorite", None, before), ("unfavorite", 0.4, j), ("pass", 0.4, j)],
    )
    # A reject whose own pass event had no score: an older scored favorite
    # never halves a ✗, and a scored legacy "pass" never halves a ✓.
    _migrated(
        db,
        "/l/rej_plain.png",
        "fail",
        [("favorite", 0.9, before), ("unfavorite", None, j), ("pass", None, j)],
    )
    _migrated(
        db,
        "/l/fav_after_reject.png",
        "pass",
        [("pass", 0.3, before), ("unpass", 0.3, j), ("favorite", None, j)],
        favorite=True,
    )
    raw = db.taste_training_raw()
    assert {m["file_path"]: m["grade_score_shown"] for m in raw["media"]} == {
        "/l/fav_shown.png": 0.71,
        "/l/fav_plain.png": None,
        "/l/rej_shown.png": 0.4,
        "/l/rej_plain.png": None,
        "/l/fav_after_reject.png": None,
    }
    w = {r.file_path: r.weight for r in build_label_set(raw, T0, 180).rows}
    assert w == pytest.approx(
        {
            "/l/fav_shown.png": 0.5,
            "/l/fav_plain.png": 1.0,
            "/l/rej_shown.png": 0.5,
            "/l/rej_plain.png": 1.0,
            "/l/fav_after_reject.png": 1.0,
        }
    )


@pytest.mark.parametrize("deleted", [False, True], ids=["live", "deleted"])
def test_unstar_and_restar_keep_a_migrated_pass_weight(db, deleted):
    # Post-v6 favorite / unfavorite events are view events: un-starring and
    # re-starring a migrated ✓ (then pressing P on it again, which only
    # restarts the clock) must not move its score_shown halving.
    _migrated(
        db, "/l/a.png", "pass", [("favorite", 0.71, T0.isoformat())], favorite=True
    )
    (before,) = _labels(db).rows
    assert before.weight == pytest.approx(0.5)
    db.set_favorite_state("/l/a.png", False, now=T0 + timedelta(minutes=1))
    db.set_favorite_state("/l/a.png", True, now=T0 + timedelta(minutes=2))
    db.set_grade("/l/a.png", "pass", now=T0 + timedelta(minutes=3))
    if deleted:
        db.delete_media(Path("/l/a.png"))
    (row,) = _labels(db, now=T0 + timedelta(minutes=3)).rows
    assert row.tombstone is deleted
    assert row.label == 1 and row.weight == pytest.approx(0.5)


def test_a_new_grade_event_replaces_the_legacy_score(db):
    _migrated(
        db,
        "/l/a.png",
        "pass",
        [
            ("pass", 0.4, (T0 - timedelta(days=2)).isoformat()),
            ("favorite", 0.71, T0.isoformat()),
        ],
        favorite=True,
    )
    t1 = T0 + timedelta(minutes=1)
    db.set_grade("/l/a.png", "fail", now=t1)  # no score on screen
    (row,) = _labels(db, now=t1).rows
    assert row.label == 0 and row.weight == pytest.approx(1.0)
    db.set_grade("/l/a.png", "pass", score_shown=0.3, now=t1)
    (row,) = _labels(db, now=t1).rows
    assert row.label == 1 and row.weight == pytest.approx(0.5)
