"""Training labels, weights and pairs for the taste scorer, plus its tables."""

import numpy as np
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.taste_labels import (
    MIN_NEGATIVES,
    MIN_POSITIVES,
    TRAINING_EVENT_KINDS,
    build_label_set,
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


def _labels(db, now=T0, half_life=180):
    return build_label_set(db.taste_training_raw(), now, half_life)


def test_constants():
    assert MIN_POSITIVES == 20 and MIN_NEGATIVES == 20
    assert len(TRAINING_EVENT_KINDS) == 8


def test_decay_halves_at_half_life():
    assert decay_weight("2025-07-05T00:00:00+00:00", T0, 180) == pytest.approx(
        0.5, rel=1e-3
    )


def test_decay_tolerates_naive_and_future():
    assert decay_weight("2026-01-01T00:00:00", T0, 180) == pytest.approx(1.0)
    assert decay_weight("2026-02-01T00:00:00+00:00", T0, 180) == 1.0


def test_labels_from_verdicts_and_seen(db):
    for p in (
        "/l/fav.png",
        "/l/pass.png",
        "/l/seen.png",
        "/l/unseen.png",
        "/l/vid.mp4",
    ):
        db.save_media(_media(p))
    db.set_verdict("/l/fav.png", "favorite", now=T0)
    db.set_verdict("/l/pass.png", "pass", now=T0)
    db.mark_seen("/l/seen.png", now=T0)
    db.set_verdict("/l/vid.mp4", "favorite", now=T0)
    ls = _labels(db)
    assert {r.file_path: r.label for r in ls.rows} == {
        "/l/fav.png": 1,
        "/l/pass.png": 0,
        "/l/seen.png": 0,
    }
    assert (ls.n_pos, ls.n_neg) == (1, 2)


def test_score_shown_halves_weight(db):
    db.save_media(_media("/l/a.png"))
    db.save_media(_media("/l/b.png"))
    db.set_verdict("/l/a.png", "favorite", score_shown=0.8, now=T0)
    db.set_verdict("/l/b.png", "favorite", now=T0)
    w = {r.file_path: r.weight for r in _labels(db).rows}
    assert w["/l/a.png"] == pytest.approx(0.5)
    assert w["/l/b.png"] == pytest.approx(1.0)


def test_reconfirm_resets_clock(db):
    db.save_media(_media("/l/a.png"))
    db.set_verdict("/l/a.png", "favorite", now=T0 - timedelta(days=360))
    old = _labels(db).rows[0].weight
    assert old == pytest.approx(0.25, rel=1e-3)
    db.set_verdict("/l/a.png", "favorite", now=T0)
    assert _labels(db).rows[0].weight == pytest.approx(1.0)


def test_batch_pairs(db):
    for n in ("fav", "pass", "seen"):
        p = f"/l/{n}.png"
        db.save_media(_media(p))
        db.create_t2i_image(file_path=p, batch_id="b1")
    db.save_media(_media("/l/other.png"))
    db.create_t2i_image(file_path="/l/other.png", batch_id="b2")
    db.set_verdict("/l/fav.png", "favorite", now=T0)
    db.set_verdict("/l/pass.png", "pass", now=T0 - timedelta(days=180))
    db.mark_seen("/l/seen.png", now=T0)
    db.set_verdict("/l/other.png", "favorite", now=T0)
    ls = _labels(db)
    idx = {r.file_path: i for i, r in enumerate(ls.rows)}
    assert sorted((a, b) for a, b, _ in ls.pairs) == sorted(
        [
            (idx["/l/fav.png"], idx["/l/pass.png"]),
            (idx["/l/fav.png"], idx["/l/seen.png"]),
        ]
    )
    weights = {(a, b): w for a, b, w in ls.pairs}
    assert weights[(idx["/l/fav.png"], idx["/l/pass.png"])] == pytest.approx(
        0.5, rel=1e-3
    )
    assert weights[(idx["/l/fav.png"], idx["/l/seen.png"])] == pytest.approx(1.0)
    assert ls.rows[idx["/l/fav.png"]].batch_id == "b1"


def test_tombstone_rows_carry_embedding(db):
    vec = np.ones(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-large"))
    db.save_media(_media("/l/gone.png"))
    db.save_media(_media("/l/gone.mp4"))
    db.create_t2i_image(file_path="/l/gone.png", batch_id="b9")
    db.set_verdict("/l/gone.png", "favorite", now=T0)
    db.set_verdict("/l/gone.mp4", "favorite", now=T0)
    db.delete_media(Path("/l/gone.png"))
    db.delete_media(Path("/l/gone.mp4"))
    ls = _labels(db)
    assert len(ls.rows) == 1
    r = ls.rows[0]
    tomb_id = db.list_tombstones()[0]["id"]
    assert r.key == f"tombstone:{tomb_id}"
    assert r.file_path == "/l/gone.png"
    assert r.label == 1 and r.batch_id == "b9"
    assert r.embedding == vec.tobytes() and r.embedding_model == "clip-large"


def test_live_rows_have_no_embedding(db):
    db.save_media(_media("/l/a.png"))
    db.set_verdict("/l/a.png", "favorite", now=T0)
    r = _labels(db).rows[0]
    assert r.key == "/l/a.png" and r.embedding is None


def test_categories_on_rows(db):
    db.save_media(_media("/l/a.png"))
    db.set_verdict("/l/a.png", "favorite", now=T0)
    cat = db.create_category("moody")
    db.add_media_categories(["/l/a.png"], cat.id, "viewer")
    assert _labels(db).rows[0].categories == ["moody"]


def test_missing_for_training():
    assert missing_for_training(5, 30) == {"positives": 15, "negatives": 0}
    assert missing_for_training(20, 20) is None


def test_event_count_since(db):
    db.save_media(_media("/l/a.png"))
    assert db.count_training_events_since(None) == (0, None)
    db.mark_seen("/l/a.png", now=T0)
    assert db.count_training_events_since(None) == (0, None)
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    db.set_verdict("/l/a.png", "favorite", now=t1)
    db.set_reason("/l/a.png", "light", True, now=t2)
    count, newest = db.count_training_events_since(None)
    assert count == 2 and newest == t2.isoformat()
    assert db.count_training_events_since(t1.isoformat()) == (1, t2.isoformat())
    assert db.count_training_events_since(t2.isoformat()) == (0, None)


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
    )
    row.update(over)
    return row


def test_replace_scores_and_active_model(db):
    assert db.get_active_taste_model() is None
    assert db.get_taste_scores() == (None, {})
    db.save_media(_media("/l/a.png"))
    db.save_media(_media("/l/b.png"))
    m1 = db.record_taste_model(_model_row())
    m2 = db.record_taste_model(_model_row(n_pos=21))
    assert m2 != m1
    active = db.get_active_taste_model()
    assert active["id"] == m2 and active["categories"] == ["moody"]
    models = db.list_taste_models()
    assert [m["id"] for m in models] == [m2, m1]
    assert [m["status"] for m in models] == ["active", "retired"]

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
