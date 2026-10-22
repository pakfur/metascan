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


def _pending(db):
    # Path.exists is monkeypatched to True by _wire, so read and catch.
    f = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _pending_paths(db):
    snap = _pending(db)
    if snap is None:
        return None
    return sorted(
        {j["file_path"] for j in snap["judgments"]}
        | {lk["file_path"] for lk in snap["categories"]}
        | set(snap["favorites"])
    )


def test_full_clean_completed_deletes_the_pending_file(monkeypatch, db):
    _seed(db)
    _wire(monkeypatch, db, ["/lib/fav.png", "/lib/pass.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _pending(db) is None
    assert [t["file_path"] for t in db.list_tombstones()] == ["/lib/gone.png"]


def test_full_clean_cancel_keeps_missing_paths_pending(monkeypatch, db):
    """Intentional change: a cancelled full clean no longer tombstones the
    paths that have not been re-imported yet; they stay pending."""
    _seed(db)
    _wire(monkeypatch, db, [])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/pass.png"]


def test_full_clean_cancel_mid_scan_restores_what_came_back(monkeypatch, db):
    _seed(db)
    events_before = db.list_judgment_events()

    class CancellingScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            scan_api._cancel_requested = True  # user hits Cancel now
            return 1

    events = _wire(monkeypatch, db, [], scanner_cls=CancellingScanner)
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False

    fav = db.get_judgment("/lib/fav.png")
    assert (fav.verdict, fav.reasons, fav.confirmed_at) == (
        "favorite",
        ["light"],
        T0.isoformat(),
    )
    assert db.list_judgment_events() == events_before
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/gone.png", "/lib/pass.png"]
    assert len([e for e in events if e[1] == "taste_restored"]) == 1

    # A later ordinary scan brings the rest back: restored, file deleted.
    _wire(monkeypatch, db, ["/lib/pass.png", "/lib/gone.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    passed = db.get_judgment("/lib/pass.png")
    assert (passed.verdict, passed.reasons) == ("pass", ["concept"])
    assert [c.name for c in passed.categories] == ["Landscape"]
    assert db.get_judgment("/lib/gone.png").verdict == "pass"
    assert db.list_judgment_events() == events_before
    assert db.list_tombstones() == []
    assert _pending(db) is None


def test_full_clean_scanner_error_restores_what_came_back(monkeypatch, db):
    """Intentional change: the paths that did not come back are kept
    pending instead of being tombstoned."""
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
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/gone.png", "/lib/pass.png"]
    assert ("scan", "error", {"message": "boom"}) in events
    restored = [e for e in events if e[1] == "taste_restored"]
    assert len(restored) == 1

    _wire(monkeypatch, db, ["/lib/pass.png", "/lib/gone.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert db.get_judgment("/lib/pass.png").verdict == "pass"
    assert _pending(db) is None


def test_full_clean_shutdown_leaves_pending_file_untouched(monkeypatch, db):
    _seed(db)

    class ShutdownScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            raise asyncio.CancelledError()

    _wire(monkeypatch, db, [], scanner_cls=ShutdownScanner)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _pending(db) is not None
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/pass.png"]
    assert db.get_judgment("/lib/fav.png").verdict is None  # not restored yet
    assert db.list_tombstones() == []

    # Next startup: the pending file is applied.
    from backend.services.taste_snapshot import apply_pending_taste_snapshot

    db.save_media(_media("/lib/pass.png"))
    counts = apply_pending_taste_snapshot(db, Path(db.db_path))
    assert counts["restored"] == 2
    assert db.get_judgment("/lib/fav.png").verdict == "favorite"
    assert db.get_judgment("/lib/pass.png").reasons == ["concept"]
    assert _pending_paths(db) == ["/lib/gone.png"]
    assert db.list_tombstones() == []


def test_new_full_clean_merges_a_pending_file(monkeypatch, db):
    """Leftovers of an interrupted full clean ride along into the next
    one; when that one completes they are tombstoned like any other."""
    _seed(db)
    _wire(monkeypatch, db, ["/lib/fav.png"])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/pass.png"]

    seen = {}
    real_truncate = db.truncate_all_data

    def spy_truncate():
        seen["paths"] = _pending_paths(db)
        return real_truncate()

    monkeypatch.setattr(db, "truncate_all_data", spy_truncate)
    # fav.png re-appears now; the other two never do.
    _wire(monkeypatch, db, ["/lib/fav.png"])
    # Simulate fav.png being present before the second full clean (it was
    # re-imported by some earlier scan) so its pending entry is applied.
    db.save_media(_media("/lib/fav.png"))
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert seen["paths"] == ["/lib/fav.png", "/lib/gone.png", "/lib/pass.png"]
    assert db.get_judgment("/lib/fav.png").verdict == "favorite"
    assert sorted(t["file_path"] for t in db.list_tombstones()) == [
        "/lib/gone.png",
        "/lib/pass.png",
    ]
    assert _pending(db) is None


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


def test_full_clean_aborts_when_pending_file_is_unreadable(monkeypatch, db):
    """A pending file that exists but can't be read may hold valid ratings:
    the full clean must not overwrite it or truncate the DB."""
    _seed(db)
    events = _wire(monkeypatch, db, [])
    target = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    old = {
        "taken_at": "t",
        "judgments": [
            {
                "file_path": "/lib/old.png",
                "passed": 1,
                "reasons": "[]",
                "seen_at": None,
                "judged_at": "t",
                "confirmed_at": "t",
            }
        ],
        "categories": [],
        "favorites": [],
    }
    target.write_text(json.dumps(old), encoding="utf-8")
    real_read = Path.read_text

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("locked by another process")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", locked)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    monkeypatch.setattr(Path, "read_text", real_read)

    assert json.loads(target.read_text(encoding="utf-8")) == old
    assert db.get_judgment("/lib/fav.png").verdict == "favorite"  # not truncated
    assert len(db.get_existing_file_paths()) == 3
    errors = [e for e in events if e[1] == "error"]
    assert len(errors) == 1
    msg = errors[0][2]["message"].lower()
    assert "pending ratings file" in msg and "could not be read" in msg
    assert not any(e[1] == "phase_changed" for e in events)
