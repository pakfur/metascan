"""Schema, lineage columns and migration v5 for taste-rating capture."""

from datetime import datetime
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
