"""Category operations: create/rename/delete, item links, event logging."""

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


def test_create_trims_and_rejects_case_duplicate(db):
    assert db.create_category("  Sci-Fi ").name == "Sci-Fi"
    with pytest.raises(ValueError, match="duplicate_name"):
        db.create_category("sci-fi")
    with pytest.raises(ValueError, match="invalid_name"):
        db.create_category("   ")
    with pytest.raises(ValueError, match="invalid_name"):
        db.create_category("x" * 41)
    assert db.create_category("x" * 40).name == "x" * 40


def test_list_sorted_case_insensitively_with_counts(db):
    db.create_category("beta")
    db.create_category("Alpha")
    db.create_category("Gamma")
    rows = db.list_categories()
    assert [r["name"] for r in rows] == ["Alpha", "beta", "Gamma"]
    assert set(rows[0]) == {"id", "name", "item_count", "created_at"}


def test_rename(db):
    a = db.create_category("Anime")
    db.create_category("Sci-Fi")
    assert db.rename_category(a.id, "  Cartoons ").name == "Cartoons"
    assert db.rename_category(a.id, "CARTOONS").name == "CARTOONS"
    with pytest.raises(ValueError, match="duplicate_name"):
        db.rename_category(a.id, "sci-fi")
    with pytest.raises(ValueError, match="invalid_name"):
        db.rename_category(a.id, " ")
    assert db.rename_category(999, "Whatever") is None


def test_add_is_idempotent_and_skips_unknown_media(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    assert db.add_media_categories(["/p/a.png", "/p/missing.png"], cid, "bulk") == 1
    assert db.add_media_categories(["/p/a.png"], cid, "viewer") == 0
    assert db.list_categories()[0]["item_count"] == 1
    assert [c.name for c in db.get_media_categories("/p/a.png")] == ["Anime"]
    events = db.list_judgment_events("/p/a.png")
    assert [e["event"] for e in events] == ["category_on"]
    assert events[0]["category"] == "Anime"


def test_remove_logs_event(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "viewer")
    assert db.remove_media_categories(["/p/a.png"], cid) == 1
    assert db.remove_media_categories(["/p/a.png"], cid) == 0
    assert [e["event"] for e in db.list_judgment_events("/p/a.png")] == [
        "category_on",
        "category_off",
    ]


def test_delete_category_logs_off_for_each_item(db):
    for p in ("/p/a.png", "/p/b.png"):
        db.save_media(_media(p))
    cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png", "/p/b.png"], cid, "bulk")
    assert db.delete_category(cid) is True
    assert db.delete_category(cid) is False
    for p in ("/p/a.png", "/p/b.png"):
        evs = db.list_judgment_events(p)
        assert [e["event"] for e in evs] == ["category_on", "category_off"]
        assert evs[1]["category"] == "Anime"
        assert db.get_media_categories(p) == []


def test_delete_category_keeps_judgments(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    db.set_verdict("/p/a.png", "favorite", sticky_category_id=cid)
    assert db.delete_category(cid) is True
    s = db.get_judgment("/p/a.png")
    assert (s.verdict, s.categories) == ("favorite", [])


def test_invalid_source_and_unknown_category(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    with pytest.raises(ValueError, match="invalid_source"):
        db.add_media_categories(["/p/a.png"], cid, "magic")
    with pytest.raises(ValueError, match="unknown_category"):
        db.add_media_categories(["/p/a.png"], 999, "bulk")


def test_categories_cascade_on_media_delete(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "bulk")
    db.delete_media(Path("/p/a.png"))
    assert db.list_categories()[0]["item_count"] == 0
