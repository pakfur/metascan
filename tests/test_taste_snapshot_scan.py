"""Scan-route level: a full clean & rescan keeps ratings, reasons and
categories (final-review item 1). Uses a real temp DB and a fake scanner
that re-saves a chosen set of paths."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.api import scan as scan_api
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


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


class _DirCfg:
    filepath = "/lib"
    search_subfolders = True


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _seed(db):
    for p in ("/lib/fav.png", "/lib/pass.png", "/lib/gone.png"):
        db.save_media(_media(p))
    cat = db.create_category("Landscape")
    db.set_verdict("/lib/fav.png", "favorite", now=T0)
    db.set_reason("/lib/fav.png", "light", True, now=T0)
    db.set_verdict("/lib/pass.png", "pass", now=T0)
    db.set_reason("/lib/pass.png", "concept", True, now=T0)
    db.add_media_categories(["/lib/pass.png"], cat.id, "viewer")
    db.set_verdict("/lib/gone.png", "pass", now=T0)


def _wire(monkeypatch, db, rescanned, scanner_cls=None):
    class FakeScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            for p in rescanned:
                db.save_media(_media(p))
            return len(rescanned)

    events = []

    async def fake_broadcast(channel, event, data=None):
        events.append((channel, event, data))

    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", scanner_cls or FakeScanner)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [_DirCfg()])
    monkeypatch.setattr(scan_api.Path, "exists", lambda self: True)
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)
    return events


def test_full_clean_restores_taste_state_and_tombstones_the_rest(monkeypatch, db):
    _seed(db)
    events_before = db.list_judgment_events()
    _wire(monkeypatch, db, ["/lib/fav.png", "/lib/pass.png", "/lib/new.png"])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    fav = db.get_judgment("/lib/fav.png")
    assert (fav.verdict, fav.reasons, fav.confirmed_at) == (
        "favorite",
        ["light"],
        T0.isoformat(),
    )
    passed = db.get_judgment("/lib/pass.png")
    assert (passed.verdict, passed.reasons) == ("pass", ["concept"])
    assert [c.name for c in passed.categories] == ["Landscape"]
    assert db.get_judgment("/lib/new.png").verdict is None
    # No duplicate favorite / pass events from the restore.
    assert db.list_judgment_events() == events_before
    [t] = db.list_tombstones()
    assert t["file_path"] == "/lib/gone.png" and t["passed"] == 1


def test_full_clean_cancel_still_restores_and_tombstones_once(monkeypatch, db):
    _seed(db)
    _wire(monkeypatch, db, [])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    # Nothing re-appeared: every judged path is preserved as a tombstone.
    paths = sorted(t["file_path"] for t in db.list_tombstones())
    assert paths == ["/lib/fav.png", "/lib/gone.png", "/lib/pass.png"]
    fav = [t for t in db.list_tombstones() if t["file_path"] == "/lib/fav.png"][0]
    assert fav["is_favorite"] == 1 and json.loads(fav["reasons"]) == ["light"]


def test_full_clean_scanner_error_restores_what_came_back(monkeypatch, db):
    _seed(db)

    class HalfScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            raise RuntimeError("boom")

    events = _wire(monkeypatch, db, [], scanner_cls=HalfScanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert db.get_judgment("/lib/fav.png").verdict == "favorite"
    assert sorted(t["file_path"] for t in db.list_tombstones()) == [
        "/lib/gone.png",
        "/lib/pass.png",
    ]
    assert ("scan", "error", {"message": "boom"}) in events
    restored = [e for e in events if e[1] == "taste_restored"]
    assert len(restored) == 1


def test_full_clean_writes_snapshot_file_before_truncating(monkeypatch, db):
    _seed(db)
    seen = {}
    real_truncate = db.truncate_all_data

    def spy_truncate():
        f = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
        seen["file"] = json.loads(f.read_text(encoding="utf-8"))
        return real_truncate()

    monkeypatch.setattr(db, "truncate_all_data", spy_truncate)
    _wire(monkeypatch, db, ["/lib/fav.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert seen["file"]["favorites"] == ["/lib/fav.png"]
    assert len(seen["file"]["judgments"]) == 3


def test_full_clean_aborts_without_deleting_when_snapshot_fails(monkeypatch, db):
    _seed(db)

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "export_taste_state", boom)
    events = _wire(monkeypatch, db, [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert db.get_judgment("/lib/fav.png").verdict == "favorite"
    assert any(e[1] == "error" for e in events)
