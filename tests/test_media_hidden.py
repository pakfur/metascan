"""Tests for the media.hidden column, summary filter, and API pass-through."""

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


def test_hidden_column_defaults_to_zero(db):
    db.save_media(_media("/pics/a.png"))
    rows = db.get_all_media_summaries()
    assert rows[0]["hidden"] is False


def test_set_media_hidden_roundtrip(db):
    db.save_media(_media("/pics/a.png"))
    assert db.set_media_hidden("/pics/a.png", True) is True
    assert db.get_all_media_summaries(include_hidden=True)[0]["hidden"] is True


def test_default_summaries_exclude_hidden(db):
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_media_hidden("/pics/b.png", True)
    default = db.get_all_media_summaries()
    assert [r["file_path"] for r in default] == ["/pics/a.png"]
    both = db.get_all_media_summaries(include_hidden=True)
    assert len(both) == 2


def test_hidden_survives_rescan_upsert(db):
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.save_media(_media("/pics/a.png"))  # rescan re-saves
    assert db.get_all_media_summaries() == []


def test_favorites_and_hidden_filters_compose(db):
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_favorite(Path("/pics/a.png"), True)
    db.set_favorite(Path("/pics/b.png"), True)
    db.set_media_hidden("/pics/b.png", True)
    rows = db.get_all_media_summaries(favorites_only=True)
    assert [r["file_path"] for r in rows] == ["/pics/a.png"]


def test_covering_indexes_include_hidden(db):
    with db._get_connection() as conn:
        for name in ("idx_media_summary_added", "idx_media_summary_modified"):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
            assert row is not None and "hidden" in row["sql"]
