"""A configured scan folder that is missing (an unmounted drive, say) is
never read as "every file in it was deleted": a Full clean & rescan is
refused or ends incomplete, and the ordinary stale cleanup leaves the
folder's rows alone. Real temp folders and a real temp DB; the scanner is
faked and re-saves the rows the test chooses."""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api import scan as scan_api
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

FAVORITE = (True, "pass")
PASS = (False, "pass")
FAIL = (False, "fail")


def _marks(state):
    return (state.favorite, state.grade)


def _media(path) -> Media:
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


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return str(path)


def _wire(monkeypatch, db, folders, scan=None):
    """Point the scan module at ``db`` and a config listing ``folders``.
    The fake scanner hands each folder it is asked to scan to ``scan``
    (which re-imports what the test wants). Returns (events, scanned)."""
    events = []
    scanned = []

    class FakeScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, directory, *a, **k):
            scanned.append(directory)
            return scan(directory) if scan else 0

    async def fake_broadcast(channel, event, data=None):
        events.append((channel, event, data))

    config = {
        "directories": [
            {"filepath": str(f), "search_subfolders": True} for f in folders
        ]
    }
    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", FakeScanner)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: config)
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)
    monkeypatch.setattr(scan_api, "_scan_task", None)
    monkeypatch.setattr(scan_api, "_cancel_requested", False)
    return events, scanned


def _start(**body):
    """POST /api/scan/start the way the route runs it, then wait for the
    scan it started. Returns the HTTPException a refusal raised, else None."""

    async def go():
        try:
            await scan_api.start_scan(scan_api.ScanRequest(**body))
        except HTTPException as e:
            return e
        await scan_api._scan_task
        return None

    return asyncio.run(go())


def _pending(db):
    f = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def _pending_paths(db):
    snap = _pending(db)
    if snap is None:
        return None
    return sorted(
        {j["file_path"] for j in snap["judgments"]}
        | {lk["file_path"] for lk in snap["categories"]}
        | set(snap["favorites"])
    )


def _completes(events):
    return [data for channel, event, data in events if event == "complete"]


def _seed_two_folders(db, tmp_path):
    """Rated images in two scan folders ``a`` and ``b``:
    a/kept.png ★ + ✓ with a reason; a/deleted.png ✗ (its file is gone);
    b/pass.png ✓; b/fail.png ✗ with a reason and a category."""
    a, b = tmp_path / "a", tmp_path / "b"
    p = {
        "kept": _touch(a / "kept.png"),
        "deleted": str(a / "deleted.png"),
        "pass": _touch(b / "pass.png"),
        "fail": _touch(b / "sub" / "fail.png"),
    }
    for path in p.values():
        db.save_media(_media(path))
    cat = db.create_category("Landscape")
    db.set_favorite_state(p["kept"], True, now=T0)
    db.set_grade(p["kept"], "pass", now=T0)
    db.set_reason(p["kept"], "light", True, now=T0)
    db.set_grade(p["deleted"], "fail", now=T0)
    db.set_grade(p["pass"], "pass", now=T0)
    db.set_grade(p["fail"], "fail", now=T0)
    db.set_reason(p["fail"], "concept", True, now=T0)
    db.add_media_categories([p["fail"]], cat.id, "viewer")
    return a, b, p


def _assert_b_ratings_back(db, p):
    passed = db.get_judgment(p["pass"])
    assert (_marks(passed), passed.confirmed_at) == (PASS, T0.isoformat())
    failed = db.get_judgment(p["fail"])
    assert (_marks(failed), failed.reasons) == (FAIL, ["concept"])
    assert [c.name for c in failed.categories] == ["Landscape"]


# ---------------------------------------------------------------------------
# 1. A full clean is refused up front while a configured folder is missing
# ---------------------------------------------------------------------------


def _seed_with_unmounted_folder(db, tmp_path):
    lib = tmp_path / "lib"
    gone = tmp_path / "unmounted"  # never created: the drive is unplugged
    p = {"here": _touch(lib / "here.png"), "away": str(gone / "away.png")}
    for path in p.values():
        db.save_media(_media(path))
    db.set_favorite_state(p["here"], True, now=T0)
    db.set_grade(p["here"], "pass", now=T0)
    db.set_grade(p["away"], "fail", now=T0)
    db.set_reason(p["away"], "light", True, now=T0)
    return lib, gone, p


def _assert_nothing_changed(db, p):
    assert sorted(db.get_existing_file_paths()) == sorted(p.values())
    assert _marks(db.get_judgment(p["here"])) == FAVORITE
    away = db.get_judgment(p["away"])
    assert (_marks(away), away.reasons) == (FAIL, ["light"])
    assert db.list_tombstones() == []
    assert _pending(db) is None


def test_full_clean_is_refused_with_409_naming_the_missing_folder(
    monkeypatch, db, tmp_path
):
    lib, gone, p = _seed_with_unmounted_folder(db, tmp_path)
    _wire(monkeypatch, db, [lib, gone])
    started = []

    async def fake_run_scan(*args, **kwargs):
        started.append(args)

    monkeypatch.setattr(scan_api, "_run_scan", fake_run_scan)
    app = FastAPI()
    app.include_router(scan_api.router)
    with TestClient(app) as client:
        resp = client.post("/api/scan/start", json={"full_clean": True})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "scan_folders_missing"
    assert detail["folders"] == [str(gone)]
    message = detail["message"]
    assert str(gone) in message
    assert "reconnect the drive" in message.lower()
    assert "remove the folder from the scan list" in message.lower()
    assert started == []
    _assert_nothing_changed(db, p)


def test_a_refused_full_clean_truncates_nothing_and_writes_no_snapshot(
    monkeypatch, db, tmp_path
):
    lib, gone, p = _seed_with_unmounted_folder(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, gone])

    err = _start(full_clean=True)

    assert err is not None and err.status_code == 409
    assert err.detail["folders"] == [str(gone)]
    assert scanned == [] and events == []
    _assert_nothing_changed(db, p)


def test_the_refusal_names_every_missing_folder_once_in_config_order(
    monkeypatch, db, tmp_path
):
    present = tmp_path / "present"
    present.mkdir()
    first, second = tmp_path / "first_drive", tmp_path / "second_drive"
    # A hand-edited config.json may list a folder twice.
    _wire(monkeypatch, db, [first, present, second, first])

    err = _start(full_clean=True)

    assert err is not None and err.status_code == 409
    assert err.detail["folders"] == [str(first), str(second)]
    message = err.detail["message"]
    assert "2 scan folders are missing" in message
    assert str(first) in message and str(second) in message
    assert "remove the folders from the scan list" in message


def test_an_ordinary_scan_is_not_refused_by_a_missing_folder(monkeypatch, db, tmp_path):
    lib, gone, p = _seed_with_unmounted_folder(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, gone])

    assert _start(full_clean=False) is None

    assert scanned == [str(lib)]
    assert len(_completes(events)) == 1
    _assert_nothing_changed(db, p)


def test_the_scan_task_itself_refuses_a_full_clean_with_a_folder_missing(
    monkeypatch, db, tmp_path
):
    """The same check runs inside the scan task, before the snapshot, for a
    folder lost between the request and the task (or a direct caller)."""
    lib, gone, p = _seed_with_unmounted_folder(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, gone])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    [error] = [data for _, event, data in events if event == "error"]
    assert error["code"] == "scan_folders_missing"
    assert error["folders"] == [str(gone)]
    assert str(gone) in error["message"]
    assert not any(event == "phase_changed" for _, event, _ in events)
    assert scanned == []
    _assert_nothing_changed(db, p)


# ---------------------------------------------------------------------------
# 2. A folder that disappears during a full clean leaves it incomplete
# ---------------------------------------------------------------------------


def test_a_folder_gone_mid_run_leaves_the_full_clean_incomplete(
    monkeypatch, db, tmp_path, caplog
):
    a, b, p = _seed_two_folders(db, tmp_path)
    events_before = db.list_judgment_events()

    def scan(directory):
        assert directory == str(a), "b is gone: it must not be scanned"
        db.save_media(_media(p["kept"]))
        shutil.rmtree(b)  # the drive holding b is unplugged now
        return 1

    events, scanned = _wire(monkeypatch, db, [a, b], scan)
    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        assert _start(full_clean=True) is None  # both folders were there

    assert scanned == [str(a)]
    kept = db.get_judgment(p["kept"])
    assert (_marks(kept), kept.reasons, kept.confirmed_at) == (
        FAVORITE,
        ["light"],
        T0.isoformat(),
    )
    # Incomplete: no tombstones at all -- not even a/deleted.png, whose file
    # really is gone -- and everything that did not come back stays pending.
    assert db.list_tombstones() == []
    assert _pending_paths(db) == sorted([p["deleted"], p["pass"], p["fail"]])
    assert db.list_judgment_events() == events_before
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]
    assert any(
        r.levelno == logging.WARNING and str(b) in r.getMessage()
        for r in caplog.records
    )

    # The drive is back: the next full clean restores b's ratings, and now
    # that it completes, tombstones only the file that really is gone.
    b.mkdir()
    reimport = {str(a): [p["kept"]], str(b): [p["pass"], p["fail"]]}

    def scan_all(directory):
        for path in reimport[directory]:
            db.save_media(_media(path))
        return len(reimport[directory])

    events, scanned = _wire(monkeypatch, db, [a, b], scan_all)
    assert _start(full_clean=True) is None

    assert scanned == [str(a), str(b)]
    _assert_b_ratings_back(db, p)
    assert _marks(db.get_judgment(p["kept"])) == FAVORITE
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (p["deleted"], "fail")
    assert _pending(db) is None
    assert db.list_judgment_events() == events_before
    [complete] = _completes(events)
    assert complete["missing_folders"] == []


def test_a_folder_lost_during_its_own_scan_leaves_the_full_clean_incomplete(
    monkeypatch, db, tmp_path
):
    """The drive drops while its folder is being scanned: half of it came
    back, the rest would be tombstoned -- checked again at the end."""
    a, b, p = _seed_two_folders(db, tmp_path)

    def scan(directory):
        if directory == str(a):
            db.save_media(_media(p["kept"]))
            return 1
        db.save_media(_media(p["pass"]))
        shutil.rmtree(b)  # unplugged before b/sub/fail.png was read
        return 1

    events, scanned = _wire(monkeypatch, db, [a, b], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a), str(b)]
    assert _marks(db.get_judgment(p["pass"])) == PASS
    assert db.list_tombstones() == []
    assert _pending_paths(db) == sorted([p["deleted"], p["fail"]])
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]


def test_full_clean_with_every_folder_present_completes_as_before(
    monkeypatch, db, tmp_path
):
    a, b, p = _seed_two_folders(db, tmp_path)
    reimport = {str(a): [p["kept"]], str(b): [p["pass"], p["fail"]]}

    def scan(directory):
        for path in reimport[directory]:
            db.save_media(_media(path))
        return len(reimport[directory])

    events, scanned = _wire(monkeypatch, db, [a, b], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a), str(b)]
    _assert_b_ratings_back(db, p)
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (p["deleted"], "fail")
    assert _pending(db) is None
    [complete] = _completes(events)
    assert complete.get("missing_folders", []) == []


# ---------------------------------------------------------------------------
# 3. The ordinary stale cleanup never deletes a missing folder's rows
# ---------------------------------------------------------------------------


def test_stale_cleanup_leaves_a_missing_folders_rows_alone(
    monkeypatch, db, tmp_path, caplog
):
    lib = tmp_path / "lib"  # configured, missing: its drive is unplugged
    library = tmp_path / "library"  # configured, there (shares "lib")
    p = {
        "away": str(lib / "away.png"),
        "away_sub": str(lib / "sub" / "deep.png"),
        "here": _touch(library / "here.png"),
        "deleted": str(library / "deleted.png"),
        "unlisted": str(tmp_path / "removed_from_config" / "old.png"),
    }
    for path in p.values():
        db.save_media(_media(path))
    db.set_grade(p["away"], "pass", now=T0)
    db.set_grade(p["away_sub"], "fail", now=T0)
    db.set_reason(p["away_sub"], "light", True, now=T0)
    db.set_grade(p["deleted"], "fail", now=T0)
    events, scanned = _wire(monkeypatch, db, [lib, library])

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        assert _start(full_cleanup=True) is None

    assert scanned == [str(library)]
    # The missing folder's rows and their ratings are untouched ...
    assert sorted(db.get_existing_file_paths()) == sorted(
        [p["away"], p["away_sub"], p["here"]]
    )
    assert _marks(db.get_judgment(p["away"])) == PASS
    deep = db.get_judgment(p["away_sub"])
    assert (_marks(deep), deep.reasons) == (FAIL, ["light"])
    # ... while a deleted file under a folder that is there, and a row of a
    # folder no longer in the config, are cleaned as before.
    assert [t["file_path"] for t in db.list_tombstones()] == [p["deleted"]]
    [complete] = _completes(events)
    assert complete["stale_removed"] == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(lib) in m and "2 rows" in m for m in warnings), warnings
