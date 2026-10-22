"""Full clean & rescan must not destroy taste data (final-review item 1).

``truncate_all_data`` deletes every media row, which cascades away
``media_judgments`` and ``media_categories``. The scan route snapshots the
taste state first (``export_taste_state``) and puts it back verbatim after
the rescan (``restore_taste_state``) -- original timestamps, no new events --
and tombstones every snapshot path that did not come back.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
T1 = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


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


def _rows(db, sql):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def _seed(db):
    """fav (+reasons, category), passed (+reasons, 2 categories), seen-only,
    untouched, and a favorite that will not come back."""
    for p in ("/l/fav.png", "/l/pass.png", "/l/seen.png", "/l/plain.png"):
        db.save_media(_media(p))
    db.save_media(_media("/l/gone.png"))
    anime = db.create_category("Anime")
    scifi = db.create_category("Sci-Fi")

    db.set_verdict("/l/fav.png", "favorite", now=T0)
    db.set_reason("/l/fav.png", "light", True, now=T0)
    db.set_reason("/l/fav.png", "realism", True, now=T0)
    db.add_media_categories(["/l/fav.png"], anime.id, "viewer")

    db.set_verdict("/l/pass.png", "pass", now=T1)
    db.set_reason("/l/pass.png", "composition", True, now=T1)
    db.add_media_categories(["/l/pass.png"], anime.id, "bulk")
    db.add_media_categories(["/l/pass.png"], scifi.id, "viewer")

    db.mark_seen("/l/seen.png", now=T0)

    db.set_verdict("/l/gone.png", "favorite", now=T1)
    db.set_reason("/l/gone.png", "emotion", True, now=T1)
    db.add_media_categories(["/l/gone.png"], scifi.id, "viewer")
    return anime, scifi


def _state(db):
    return (
        _rows(db, "SELECT * FROM media_judgments ORDER BY file_path"),
        _rows(db, "SELECT * FROM media_categories ORDER BY file_path, category_id"),
        _rows(db, "SELECT file_path FROM media WHERE is_favorite = 1 ORDER BY 1"),
    )


def test_truncate_keeps_event_log_and_categories(db):
    _seed(db)
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")
    cats = _rows(db, "SELECT * FROM categories ORDER BY id")
    assert db.truncate_all_data() is True
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    assert _rows(db, "SELECT * FROM categories ORDER BY id") == cats
    # ...while the cascading tables are gone -- which is why we snapshot.
    assert _rows(db, "SELECT * FROM media_judgments") == []
    assert _rows(db, "SELECT * FROM media_categories") == []


def test_snapshot_truncate_rescan_restore_roundtrip(db):
    _seed(db)
    judgments, links, favs = _state(db)
    before_gone = [j for j in judgments if j["file_path"] == "/l/gone.png"]
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")

    snapshot = db.export_taste_state()
    assert db.truncate_all_data() is True
    for p in ("/l/fav.png", "/l/pass.png", "/l/seen.png", "/l/plain.png"):
        db.save_media(_media(p))  # rescan; gone.png does not come back

    counts = db.restore_taste_state(snapshot)

    assert counts["restored"] == 3
    assert counts["missing"] == 1
    j2, l2, f2 = _state(db)
    assert j2 == [j for j in judgments if j["file_path"] != "/l/gone.png"]
    assert l2 == [lk for lk in links if lk["file_path"] != "/l/gone.png"]
    assert f2 == [{"file_path": "/l/fav.png"}]
    assert favs == [{"file_path": "/l/fav.png"}, {"file_path": "/l/gone.png"}]
    # No new events: the restore is a put-back, not a re-judgment.
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    # Judgment reads see the original verdicts, reasons and timestamps.
    fav = db.get_judgment("/l/fav.png")
    assert fav.verdict == "favorite" and fav.reasons == ["realism", "light"]
    assert fav.confirmed_at == T0.isoformat()
    passed = db.get_judgment("/l/pass.png")
    assert passed.verdict == "pass" and passed.reasons == ["composition"]
    assert [c.name for c in passed.categories] == ["Anime", "Sci-Fi"]
    assert db.get_judgment("/l/seen.png").seen_at == T0.isoformat()
    assert before_gone  # sanity: the missing path really carried a judgment


def test_restore_writes_one_tombstone_per_missing_path(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/l/fav.png", "/l/pass.png", "/l/seen.png"):
        db.save_media(_media(p))
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-small"))

    counts = db.restore_taste_state(snapshot, tombstone_missing=True)

    assert counts == {"restored": 3, "missing": 1, "tombstoned": 1}
    [t] = db.list_tombstones()
    assert t["file_path"] == "/l/gone.png"
    assert (t["is_favorite"], t["passed"]) == (1, 0)
    assert json.loads(t["reasons"]) == ["emotion"]
    assert json.loads(t["categories"]) == ["Sci-Fi"]
    assert t["confirmed_at"] == T1.isoformat()
    assert t["embedding_model"] == "clip-small"
    assert np.array_equal(np.frombuffer(t["embedding"], dtype=np.float32), vec)


def test_restore_without_tombstones_flag_writes_none(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    counts = db.restore_taste_state(snapshot)
    assert counts == {"restored": 0, "missing": 4, "tombstoned": 0}
    assert db.list_tombstones() == []


def test_restore_skips_categories_deleted_meanwhile(db):
    anime, scifi = _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/l/pass.png"))
    db.delete_category(scifi.id)
    db.restore_taste_state(snapshot)
    assert [c.name for c in db.get_media_categories("/l/pass.png")] == ["Anime"]


def test_restore_is_idempotent(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/l/fav.png", "/l/pass.png", "/l/seen.png"):
        db.save_media(_media(p))
    db.restore_taste_state(snapshot)
    first = _state(db)
    db.restore_taste_state(snapshot)
    assert _state(db) == first


def test_snapshot_survives_json_roundtrip(db):
    """The snapshot is plain data (so it could be persisted)."""
    _seed(db)
    snapshot = json.loads(json.dumps(db.export_taste_state()))
    db.truncate_all_data()
    db.save_media(_media("/l/fav.png"))
    assert db.restore_taste_state(snapshot)["restored"] == 1
    assert db.get_judgment("/l/fav.png").verdict == "favorite"
