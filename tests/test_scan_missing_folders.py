"""A configured scan folder whose files can't be seen -- missing (an
unplugged drive), unreadable, or empty while the library has images from it
(the mount point of a drive that is not mounted) -- is never read as "every
file in it was deleted": a Full clean & rescan is refused or ends
incomplete, and the ordinary stale cleanup leaves the folder's rows alone.
Real temp folders and a real temp DB; the scanner is faked and re-saves the
rows the test chooses."""

import asyncio
import errno
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api import scan as scan_api
from backend.config import DirectoryConfig
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
    assert f"{gone} is missing" in message
    # Reconnecting comes first; removing the folder is the last resort, and
    # the message says what it costs.
    assert message.index("Reconnect its drive") < message.index("Remove the folder")
    assert "only if it is gone for good" in message
    assert "its images' ratings are then dropped from the library" in message
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
    assert "2 scan folders can't be scanned" in message
    assert f"{first} (missing)" in message and f"{second} (missing)" in message
    assert message.index("Reconnect") < message.index("Remove a folder")
    assert "only if it is gone for good" in message


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
    _touch(b / "pass.png")
    _touch(b / "sub" / "fail.png")
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
    assert any(
        str(lib) in m and "is missing" in m and "left 2 row(s)" in m for m in warnings
    ), warnings


# ---------------------------------------------------------------------------
# 4. A folder that is there but shows nothing: the empty mount point of a
#    drive that is not mounted, or a folder the server may not list
# ---------------------------------------------------------------------------


def _seed_with_empty_mount_point(db, tmp_path):
    """``lib`` holds a file; ``mnt`` is an empty directory -- the mount point
    of a drive that is not mounted (fstab, NFS, SMB) -- while the library
    still has rated images from it."""
    lib, mnt = tmp_path / "lib", tmp_path / "mnt_drive"
    mnt.mkdir()
    p = {
        "here": _touch(lib / "here.png"),
        "m1": str(mnt / "m1.png"),
        "m2": str(mnt / "sub" / "m2.png"),
    }
    for path in p.values():
        db.save_media(_media(path))
    db.set_favorite_state(p["m1"], True, now=T0)
    db.set_grade(p["m1"], "pass", now=T0)
    db.set_grade(p["m2"], "fail", now=T0)
    db.set_reason(p["m2"], "light", True, now=T0)
    return lib, mnt, p


def _assert_mount_point_untouched(db, p):
    assert sorted(db.get_existing_file_paths()) == sorted(p.values())
    assert _marks(db.get_judgment(p["m1"])) == FAVORITE
    m2 = db.get_judgment(p["m2"])
    assert (_marks(m2), m2.reasons) == (FAIL, ["light"])
    assert db.list_tombstones() == []


def test_full_clean_is_refused_while_a_scan_folder_is_an_empty_mount_point(
    monkeypatch, db, tmp_path
):
    lib, mnt, p = _seed_with_empty_mount_point(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, mnt])

    err = _start(full_clean=True)

    assert err is not None and err.status_code == 409
    assert err.detail["code"] == "scan_folders_missing"
    assert err.detail["folders"] == [str(mnt)]
    message = err.detail["message"]
    assert f"{mnt} is empty" in message and "Is its drive mounted?" in message
    assert "only if it is gone for good" in message
    assert scanned == [] and events == []
    _assert_mount_point_untouched(db, p)
    assert _pending(db) is None


def test_the_scan_task_refuses_a_full_clean_with_an_empty_mount_point(
    monkeypatch, db, tmp_path
):
    lib, mnt, p = _seed_with_empty_mount_point(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, mnt])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    [error] = [data for _, event, data in events if event == "error"]
    assert error["folders"] == [str(mnt)] and f"{mnt} is empty" in error["message"]
    assert not any(event == "phase_changed" for _, event, _ in events)
    assert scanned == []
    _assert_mount_point_untouched(db, p)
    assert _pending(db) is None


def test_stale_cleanup_leaves_an_empty_mount_points_rows_alone(
    monkeypatch, db, tmp_path, caplog
):
    lib, mnt, p = _seed_with_empty_mount_point(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [lib, mnt])

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        assert _start(full_cleanup=True) is None

    assert scanned == [str(lib)]  # nothing to scan in it either
    _assert_mount_point_untouched(db, p)
    [complete] = _completes(events)
    assert (complete["stale_removed"], complete["missing_folders"]) == (0, [str(mnt)])
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        str(mnt) in m and "is empty" in m and "left 2 row(s)" in m for m in warnings
    ), warnings


def test_an_empty_scan_folder_the_library_has_nothing_from_blocks_nothing(
    monkeypatch, db, tmp_path
):
    """A folder just added to the scan list, nothing in it yet: empty, but
    nothing says a drive is missing."""
    a, b, p = _seed_two_folders(db, tmp_path)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    reimport = {str(a): [p["kept"]], str(b): [p["pass"], p["fail"]], str(fresh): []}

    def scan(directory):
        for path in reimport[directory]:
            db.save_media(_media(path))
        return len(reimport[directory])

    events, scanned = _wire(monkeypatch, db, [a, b, fresh], scan)
    assert _start(full_clean=True, full_cleanup=True) is None

    assert scanned == [str(a), str(b), str(fresh)]
    [complete] = _completes(events)
    assert complete["missing_folders"] == []
    [t] = db.list_tombstones()
    assert t["file_path"] == p["deleted"]
    assert _pending(db) is None


def test_a_full_clean_is_refused_when_pending_ratings_point_into_an_empty_folder(
    monkeypatch, db, tmp_path
):
    """An earlier full clean ran while b's drive was away: b's ratings are
    pending and no row of b is left. b's mount point is back, empty -- the
    drive is still not mounted -- so the pending file is the only evidence."""
    a, b, p = _seed_two_folders(db, tmp_path)

    def scan(directory):
        db.save_media(_media(p["kept"]))
        shutil.rmtree(b)
        return 1

    _wire(monkeypatch, db, [a, b], scan)
    assert _start(full_clean=True) is None
    pending_before = _pending(db)
    assert _pending_paths(db) == sorted([p["deleted"], p["pass"], p["fail"]])
    assert sorted(db.get_existing_file_paths()) == [p["kept"]]  # none of b

    b.mkdir()
    events, scanned = _wire(monkeypatch, db, [a, b])
    err = _start(full_clean=True)

    assert err is not None and err.detail["folders"] == [str(b)]
    assert f"{b} is empty" in err.detail["message"]
    assert scanned == [] and events == []
    assert _pending(db) == pending_before
    assert db.list_tombstones() == []


@pytest.mark.parametrize(
    "raw",
    [b"[" * 100_000, b'{"version": 3, "judgments": ["\xff"]}'],
    ids=["nested-too-deep", "not-utf8"],
)
def test_a_pending_file_the_parser_cannot_read_never_breaks_the_check(
    monkeypatch, db, tmp_path, raw
):
    """The up-front check reads the pending file for evidence without side
    effects. One the parser cannot read (nested deeper than it goes, not
    UTF-8) holds none -- it never raises out of the check -- and is left as
    it is for the full clean's own loader to set aside
    (test_taste_snapshot_briefs). Here the library's row is the evidence."""
    mnt = tmp_path / "mnt"
    mnt.mkdir()  # the empty mount point of a drive that is not mounted
    db.save_media(_media(mnt / "away.png"))
    target = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    target.write_bytes(raw)
    assert scan_api.pending_snapshot_paths(Path(db.db_path)) == set()

    events, scanned = _wire(monkeypatch, db, [mnt])
    err = _start(full_clean=True)

    assert err is not None and err.detail["folders"] == [str(mnt)]
    assert scanned == [] and events == []
    assert target.read_bytes() == raw


def _unlistable(folder: Path) -> None:
    """Take read permission off ``folder`` (0o300: it can be entered but not
    listed, and rglob then silently finds nothing in it), or skip the test
    where that doesn't stop listing (root, Windows)."""
    os.chmod(folder, 0o300)
    try:
        os.listdir(folder)
    except PermissionError:
        return
    os.chmod(folder, 0o755)
    pytest.skip("permissions don't stop listing a folder here")


def test_full_clean_is_refused_while_a_scan_folder_cannot_be_listed(
    monkeypatch, db, tmp_path
):
    lib, locked = tmp_path / "lib", tmp_path / "locked"
    p = {
        "here": _touch(lib / "here.png"),
        "l1": _touch(locked / "l1.png"),
        "l2": _touch(locked / "sub" / "l2.png"),
    }
    for path in p.values():
        db.save_media(_media(path))
    db.set_grade(p["l1"], "pass", now=T0)
    db.set_grade(p["l2"], "fail", now=T0)
    events, scanned = _wire(monkeypatch, db, [lib, locked])

    _unlistable(locked)
    try:
        err = _start(full_clean=True)
    finally:
        os.chmod(locked, 0o755)

    assert err is not None and err.status_code == 409
    assert err.detail["folders"] == [str(locked)]
    assert f"{locked} can't be read" in err.detail["message"]
    assert scanned == [] and events == []
    assert sorted(db.get_existing_file_paths()) == sorted(p.values())
    assert db.list_tombstones() == [] and _pending(db) is None


def test_a_folder_emptied_mid_run_leaves_the_full_clean_incomplete(
    monkeypatch, db, tmp_path
):
    """b's drive is unmounted during a's scan and only its empty mount
    point is left: nothing comes back from b, and nothing of it may be
    tombstoned."""
    a, b, p = _seed_two_folders(db, tmp_path)

    def scan(directory):
        if directory == str(a):
            db.save_media(_media(p["kept"]))
            for child in b.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            return 1
        return 0  # b holds nothing to import

    events, scanned = _wire(monkeypatch, db, [a, b], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a)]
    assert _marks(db.get_judgment(p["kept"])) == FAVORITE
    assert db.list_tombstones() == []
    assert _pending_paths(db) == sorted([p["deleted"], p["pass"], p["fail"]])
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]


# ---------------------------------------------------------------------------
# 5. Folders that go away (or fail) at a folder's turn, during its scan or
#    before the cleanup
# ---------------------------------------------------------------------------


def _three_folders(db, tmp_path):
    """Folders a, b, c with one image each; b's is ✓, c's is ✗."""
    folders = [tmp_path / n for n in "abc"]
    p = {f.name: _touch(f / "x.png") for f in folders}
    for path in p.values():
        db.save_media(_media(path))
    db.set_grade(p["b"], "pass", now=T0)
    db.set_grade(p["c"], "fail", now=T0)
    return folders, p


def _stat_fails(monkeypatch, folder: Path) -> None:
    """From now on stat()-ing ``folder`` itself fails with EIO, the way a
    dead network share answers."""
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        if os.fspath(self) == os.fspath(folder):
            raise OSError(errno.EIO, "Input/output error", os.fspath(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)


def _errors(events):
    return [data for _, event, data in events if event == "error"]


def test_a_folder_missing_at_its_turn_but_back_by_the_end_leaves_it_incomplete(
    monkeypatch, db, tmp_path
):
    """b is unplugged during a's scan and plugged back in during c's: only
    the check at b's own turn knows b was never scanned."""
    (a, b, c), p = _three_folders(db, tmp_path)

    def scan(directory):
        if directory == str(a):
            shutil.rmtree(b)  # unplugged
        if directory == str(c):
            _touch(b / "x.png")  # plugged back in
        db.save_media(_media(Path(directory) / "x.png"))
        return 1

    events, scanned = _wire(monkeypatch, db, [a, b, c], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a), str(c)]
    assert db.list_tombstones() == []
    assert _pending_paths(db) == [p["b"]]
    assert _marks(db.get_judgment(p["c"])) == FAIL
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]


def test_the_scanners_folder_gone_race_skips_that_folder_and_scans_the_rest(
    monkeypatch, db, tmp_path
):
    """b goes between the check at its turn and the scanner's own check,
    which raises ValueError("Directory does not exist"): c is still scanned
    and the run ends incomplete instead of failing."""
    (a, b, c), p = _three_folders(db, tmp_path)

    def scan(directory):
        if directory == str(b):
            shutil.rmtree(b)
            raise ValueError(f"Directory does not exist: {directory}")
        db.save_media(_media(Path(directory) / "x.png"))
        return 1

    events, scanned = _wire(monkeypatch, db, [a, b, c], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a), str(b), str(c)]
    assert _errors(events) == []
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]
    assert db.list_tombstones() == []
    assert _pending_paths(db) == [p["b"]]
    assert _marks(db.get_judgment(p["c"])) == FAIL


def test_a_scanner_error_for_a_folder_that_is_there_still_fails_the_run(
    monkeypatch, db, tmp_path
):
    a, b, p = _seed_two_folders(db, tmp_path)

    def scan(directory):
        raise ValueError("bad media")

    events, scanned = _wire(monkeypatch, db, [a, b], scan)
    assert _start(full_clean=True) is None

    assert scanned == [str(a)]
    assert _errors(events) == [{"message": "bad media"}]
    assert db.list_tombstones() == []
    assert _pending_paths(db) == sorted(p.values())


def test_a_folder_whose_stat_fails_at_its_turn_is_skipped_and_the_rest_scanned(
    monkeypatch, db, tmp_path
):
    (a, b, c), p = _three_folders(db, tmp_path)
    events, scanned = _wire(monkeypatch, db, [a, b, c], lambda directory: 0)
    _stat_fails(monkeypatch, b)

    assert _start(full_cleanup=False) is None

    assert scanned == [str(a), str(c)]
    assert _errors(events) == []
    [complete] = _completes(events)
    assert complete["missing_folders"] == [str(b)]


def test_a_folder_lost_after_its_turn_is_reported_by_the_stale_cleanup(
    monkeypatch, db, tmp_path
):
    """b is scanned, then its drive goes during c's scan: the cleanup leaves
    b's rows alone and is the one that reports b."""
    (a, b, c), p = _three_folders(db, tmp_path)

    def scan(directory):
        if directory == str(c):
            shutil.rmtree(b)
        return 0

    events, scanned = _wire(monkeypatch, db, [a, b, c], scan)
    assert _start(full_cleanup=True) is None

    assert scanned == [str(a), str(b), str(c)]
    assert p["b"] in db.get_existing_file_paths()
    assert _marks(db.get_judgment(p["b"])) == PASS
    [complete] = _completes(events)
    assert (complete["stale_removed"], complete["missing_folders"]) == (0, [str(b)])


def test_stale_cleanup_leaves_the_rows_of_a_folder_whose_stat_fails_alone(
    monkeypatch, db, tmp_path, caplog
):
    (a, b, c), p = _three_folders(db, tmp_path)

    def scan(directory):
        if directory == str(c):
            shutil.rmtree(b)  # the share holding b drops ...
            _stat_fails(monkeypatch, b)  # ... and stat()-ing it now fails
        return 0

    events, scanned = _wire(monkeypatch, db, [a, b, c], scan)
    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        assert _start(full_cleanup=True) is None

    assert _errors(events) == []
    assert p["b"] in db.get_existing_file_paths()
    [complete] = _completes(events)
    assert complete["stale_removed"] == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(b) in m and "Stale cleanup" in m for m in warnings), warnings


# ---------------------------------------------------------------------------
# 6. The stale cleanup's own order: per-file pass, folder check, re-stat
# ---------------------------------------------------------------------------


def _dirs(*folders):
    return [DirectoryConfig(filepath=str(f)) for f in folders]


def _drive_away_while_read(paths, folder, away, back):
    """``paths`` as the per-file pass reads them, with ``folder``'s drive
    unplugged from the first read on and, with ``back``, plugged in again
    once the last one has been read (before the folder check)."""
    folder.rename(away)
    yield from paths
    if back:
        away.rename(folder)


def _cleanup_case(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _touch(a / "here.png")
    on_b = [_touch(b / "x.png"), _touch(b / "sub" / "y.png")]
    gone = str(a / "deleted.png")  # really deleted, under a folder that is there
    return a, b, on_b, gone


def test_stale_cleanup_checks_the_folders_after_the_per_file_pass(tmp_path):
    """A drive unplugged as the pass begins is seen as missing."""
    a, b, on_b, gone = _cleanup_case(tmp_path)
    paths = _drive_away_while_read([gone, *on_b], b, tmp_path / "away", back=False)

    result = scan_api._stale_paths_to_clean(paths, _dirs(a, b))

    assert result[0] == [Path(gone)]
    assert result[1] == {str(b): 2}


def test_stale_cleanup_restats_what_it_would_delete(tmp_path):
    """A drive away during the pass but back before the folder check: its
    files are there again, so none of them is deleted."""
    a, b, on_b, gone = _cleanup_case(tmp_path)
    paths = _drive_away_while_read([gone, *on_b], b, tmp_path / "away", back=True)

    result = scan_api._stale_paths_to_clean(paths, _dirs(a, b))

    assert result[0] == [Path(gone)]
    assert result[1] == {}


def test_a_mount_point_inside_a_scan_folder_is_covered_once_it_is_listed_too(
    monkeypatch, db, tmp_path, caplog
):
    """Only scan folders themselves are checked, so the docs tell the user
    to add a drive mounted inside one to the scan list as well. Then its
    empty mount point protects its rows like any other."""
    lib = tmp_path / "lib"
    drive = lib / "drive"  # the drive's mount point, inside lib, not mounted
    drive.mkdir(parents=True)
    p = {
        "here": _touch(lib / "here.png"),
        "d1": str(drive / "d1.png"),
        "d2": str(drive / "sub" / "d2.png"),
    }
    for path in p.values():
        db.save_media(_media(path))
    db.set_grade(p["d1"], "pass", now=T0)
    db.set_grade(p["d2"], "fail", now=T0)
    events, scanned = _wire(monkeypatch, db, [lib, drive])

    err = _start(full_clean=True)
    assert err is not None and err.detail["folders"] == [str(drive)]

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        assert _start(full_cleanup=True) is None
    assert scanned == [str(lib)]
    assert sorted(db.get_existing_file_paths()) == sorted(p.values())
    assert db.list_tombstones() == []
    [complete] = _completes(events)
    assert (complete["stale_removed"], complete["missing_folders"]) == (0, [str(drive)])
