"""Schema, lineage columns and migration v5 for taste-rating capture."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


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


def test_new_tables_exist(db):
    with db._get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "media_judgments",
        "judgment_events",
        "media_tombstones",
        "categories",
        "media_categories",
    } <= names


def test_lineage_columns_added(db):
    with db._get_connection() as conn:
        for table in ("t2i_images", "i2v_videos"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {"writer_id", "variant_group", "variant_index"} <= cols


def test_user_version_is_5(db):
    with db._get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_migration_seeds_existing_favorites(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    mgr.save_media(_media("/pics/fav.png"))
    mgr.set_favorite(Path("/pics/fav.png"), True)
    with mgr._get_connection() as conn:
        conn.execute("DELETE FROM media_judgments")
        conn.execute("DELETE FROM judgment_events")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    mgr.close()
    mgr = DatabaseManager(tmp_path / "db")
    with mgr._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM media_judgments WHERE file_path = '/pics/fav.png'"
        ).fetchone()
        assert (
            row["confirmed_at"] is not None and row["judged_at"] == row["confirmed_at"]
        )
        assert row["seen_at"] == row["confirmed_at"]
        events = conn.execute("SELECT event FROM judgment_events").fetchall()
        assert [e["event"] for e in events] == ["favorite"]
    mgr.close()


def test_judgment_survives_rescan_upsert(db):
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO media_judgments (file_path, passed) VALUES ('/pics/a.png', 1)"
        )
        conn.commit()
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        assert (
            conn.execute("SELECT passed FROM media_judgments").fetchone()["passed"] == 1
        )


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _events(db, p):
    return [e["event"] for e in db.list_judgment_events(p)]


def test_favorite_then_pass_is_exclusive_and_clears_reasons(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite")
    db.set_reason("/p/a.png", "realism", True)
    s = db.set_verdict("/p/a.png", "pass")
    assert (s.verdict, s.is_favorite, s.reasons) == ("pass", False, [])
    assert _events(db, "/p/a.png") == ["favorite", "reason_on", "unfavorite", "pass"]


def test_reason_requires_verdict_and_known_name(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="no_verdict"):
        db.set_reason("/p/a.png", "light", True)
    db.set_verdict("/p/a.png", "pass")
    with pytest.raises(ValueError, match="unknown_reason"):
        db.set_reason("/p/a.png", "vibes", True)


def test_reasons_keep_canonical_order(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite")
    db.set_reason("/p/a.png", "motion", True)
    db.set_reason("/p/a.png", "realism", True)
    assert db.get_judgment("/p/a.png").reasons == ["realism", "motion"]


def test_reason_toggle_is_idempotent(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite")
    db.set_reason("/p/a.png", "light", True)
    db.set_reason("/p/a.png", "light", True)
    db.set_reason("/p/a.png", "light", False)
    db.set_reason("/p/a.png", "light", False)
    assert _events(db, "/p/a.png") == ["favorite", "reason_on", "reason_off"]


def test_clearing_verdict_logs_unevent_and_resets(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite")
    s = db.set_verdict("/p/a.png", None)
    assert (s.verdict, s.is_favorite) == (None, False)
    assert _events(db, "/p/a.png") == ["favorite", "unfavorite"]


def test_same_verdict_again_only_refreshes_confirmed_at(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite", now=T0)
    s = db.set_verdict("/p/a.png", "favorite", now=T0 + timedelta(days=2))
    assert _events(db, "/p/a.png") == ["favorite"]
    assert s.confirmed_at.startswith("2026-01-03")


def test_legacy_unfavorite_keeps_pass(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "pass")
    db.set_favorite(Path("/p/a.png"), False)
    assert db.get_judgment("/p/a.png").verdict == "pass"


def test_legacy_favorite_on_passed_item_flips(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "pass")
    db.set_reason("/p/a.png", "light", True)
    assert db.toggle_favorite(Path("/p/a.png")) is True
    s = db.get_judgment("/p/a.png")
    assert (s.verdict, s.reasons) == ("favorite", [])


def test_legacy_set_and_toggle_return_values(db):
    db.save_media(_media("/p/a.png"))
    assert db.set_favorite(Path("/p/a.png"), True) is True
    assert db.toggle_favorite(Path("/p/a.png")) is False
    assert db.get_judgment("/p/a.png").verdict is None
    assert db.set_favorite(Path("/nope.png"), True) is False
    assert db.toggle_favorite(Path("/nope.png")) is False


def test_seen_then_reconfirm_throttled(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.set_verdict("/p/a.png", "favorite", now=T0)
    db.mark_seen("/p/a.png", now=T0 + timedelta(hours=1))
    db.mark_seen("/p/a.png", now=T0 + timedelta(hours=25))
    assert _events(db, "/p/a.png") == ["seen", "favorite", "reconfirm"]
    assert db.get_judgment("/p/a.png").confirmed_at.startswith("2026-01-02T01")


def test_unjudged_item_seen_twice_logs_once(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.mark_seen("/p/a.png", now=T0 + timedelta(days=3))
    assert _events(db, "/p/a.png") == ["seen"]


def test_score_shown_recorded(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite", score_shown=0.82)
    assert db.list_judgment_events("/p/a.png")[0]["score_shown"] == pytest.approx(0.82)


def test_missing_media_returns_none(db):
    assert db.set_verdict("/nope.png", "favorite") is None
    assert db.mark_seen("/nope.png") is None
    assert db.get_judgment("/nope.png") is None
    assert db.set_reason("/nope.png", "light", True) is None


def test_sticky_category_added_on_verdict_only(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Sci-Fi").id
    db.mark_seen("/p/a.png")
    assert db.get_judgment("/p/a.png").categories == []
    s = db.set_verdict("/p/a.png", "pass", sticky_category_id=cid)
    assert [c.name for c in s.categories] == ["Sci-Fi"]
    assert _events(db, "/p/a.png")[-1] == "category_on"
    db.set_verdict("/p/a.png", "favorite", sticky_category_id=cid)
    assert _events(db, "/p/a.png").count("category_on") == 1


def test_unknown_sticky_category_raises(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="unknown_category"):
        db.set_verdict("/p/a.png", "favorite", sticky_category_id=999)
    assert db.get_judgment("/p/a.png").verdict is None


def test_path_with_spaces(db):
    db.save_media(_media("/p/my pics/a b.png"))
    assert db.set_verdict("/p/my pics/a b.png", "favorite").is_favorite is True
