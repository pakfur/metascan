"""Pending taste snapshot: the file a full clean leaves behind is applied
later (startup, end of every scan) and survives corruption without taking
the server down."""

import asyncio
import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services import taste_snapshot as ts
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


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _pending_snapshot(db):
    for p in ("/p/a.png", "/p/b.png"):
        db.save_media(_media(p))
    db.set_verdict("/p/a.png", "favorite", now=T0)
    db.set_verdict("/p/b.png", "pass", now=T0)
    snap = db.export_taste_state()
    db.truncate_all_data()
    ts.write_snapshot_file(Path(db.db_path), snap)
    return snap


def test_apply_without_a_file_is_a_noop(db):
    counts = ts.apply_pending_taste_snapshot(db, Path(db.db_path))
    assert counts == {"restored": 0, "missing": 0}
    assert not ts.snapshot_file_path(Path(db.db_path)).exists()


def test_apply_restores_reappeared_and_keeps_the_rest(db):
    _pending_snapshot(db)
    db.save_media(_media("/p/a.png"))
    counts = ts.apply_pending_taste_snapshot(db, Path(db.db_path))
    assert counts == {"restored": 1, "missing": 1}
    assert db.get_judgment("/p/a.png").verdict == "favorite"
    left = json.loads(ts.snapshot_file_path(Path(db.db_path)).read_text())
    assert [j["file_path"] for j in left["judgments"]] == ["/p/b.png"]
    assert db.list_tombstones() == []

    db.save_media(_media("/p/b.png"))
    assert ts.apply_pending_taste_snapshot(db, Path(db.db_path)) == {
        "restored": 1,
        "missing": 0,
    }
    assert db.get_judgment("/p/b.png").verdict == "pass"
    assert not ts.snapshot_file_path(Path(db.db_path)).exists()


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[1, 2]",
        '{"judgments": 5}',
        '{"judgments": [{"file_path": "/x.png"}]}',
        '{"favorites": [{"a": 1}]}',
    ],
)
def test_corrupt_file_is_renamed_and_logged(db, caplog, payload):
    data_dir = Path(db.db_path)
    f = ts.snapshot_file_path(data_dir)
    f.write_text(payload, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        counts = ts.apply_pending_taste_snapshot(db, data_dir)
    assert counts == {"restored": 0, "missing": 0}
    assert not f.exists()
    [kept] = list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))
    assert kept.read_text(encoding="utf-8") == payload
    assert any("corrupt" in r.getMessage().lower() for r in caplog.records)
    # Not retried: a second apply finds nothing.
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 0,
        "missing": 0,
    }


def test_merge_adds_pending_leftovers_to_a_new_snapshot(db):
    _pending_snapshot(db)
    db.save_media(_media("/p/c.png"))
    db.set_verdict("/p/c.png", "favorite", now=T0)
    fresh = db.export_taste_state()
    merged = ts.merge_pending_into_snapshot(Path(db.db_path), fresh)
    assert sorted(j["file_path"] for j in merged["judgments"]) == [
        "/p/a.png",
        "/p/b.png",
        "/p/c.png",
    ]
    assert sorted(merged["favorites"]) == ["/p/a.png", "/p/c.png"]


def test_merge_never_lets_a_pending_entry_shadow_the_fresh_one(db):
    snap = _pending_snapshot(db)
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "pass", now=T0)  # newer than the pending one
    fresh = db.export_taste_state()
    merged = ts.merge_pending_into_snapshot(Path(db.db_path), fresh)
    a = [j for j in merged["judgments"] if j["file_path"] == "/p/a.png"]
    assert len(a) == 1 and a[0]["passed"] == 1
    assert "/p/a.png" not in merged["favorites"]
    assert snap  # sanity


def test_lifespan_helper_applies_and_never_raises(db, monkeypatch):
    from backend import main as main_mod

    _pending_snapshot(db)
    db.save_media(_media("/p/a.png"))
    asyncio.run(main_mod._apply_pending_taste_snapshot(db, Path(db.db_path)))
    assert db.get_judgment("/p/a.png").verdict == "favorite"

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(main_mod, "apply_pending_taste_snapshot", boom)
    asyncio.run(main_mod._apply_pending_taste_snapshot(db, Path(db.db_path)))


# ----------------------------------------------------------------------
# Review fixes
# ----------------------------------------------------------------------


def test_write_fsyncs_file_and_directory_before_returning(tmp_path, monkeypatch):
    calls = []
    real_fsync = os.fsync
    real_replace = os.replace

    def spy_fsync(fd):
        calls.append(("fsync", stat.S_ISDIR(os.fstat(fd).st_mode)))
        real_fsync(fd)

    def spy_replace(a, b):
        calls.append(("replace", None))
        real_replace(a, b)

    monkeypatch.setattr(ts.os, "fsync", spy_fsync)
    monkeypatch.setattr(ts.os, "replace", spy_replace)
    ts.write_snapshot_file(
        tmp_path, {"judgments": [], "categories": [], "favorites": []}
    )
    assert calls[0] == ("fsync", False)  # file data durable before the rename
    assert calls[1] == ("replace", None)
    if os.name != "nt":
        assert calls[2] == ("fsync", True)  # and the rename itself


def test_write_tolerates_an_unsupported_directory_fsync(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def picky_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("not supported")
        real_fsync(fd)

    monkeypatch.setattr(ts.os, "fsync", picky_fsync)
    ts.write_snapshot_file(
        tmp_path, {"judgments": [], "categories": [], "favorites": []}
    )
    assert ts.snapshot_file_path(tmp_path).exists()


_GOOD_J = {
    "file_path": "/x.png",
    "passed": 0,
    "reasons": "[]",
    "seen_at": None,
    "judged_at": None,
    "confirmed_at": None,
}
_GOOD_L = {
    "file_path": "/x.png",
    "category_id": 1,
    "category_name": "A",
    "source": "viewer",
    "assigned_at": "2026-01-01T00:00:00+00:00",
}


@pytest.mark.parametrize(
    "snap",
    [
        {"judgments": [{**_GOOD_J, "passed": None}]},
        {"judgments": [{**_GOOD_J, "passed": "yes"}]},
        {"judgments": [{**_GOOD_J, "reasons": ["light"]}]},
        {"judgments": [{**_GOOD_J, "file_path": 3}]},
        {"judgments": [{**_GOOD_J, "seen_at": 5}]},
        {"categories": [{**_GOOD_L, "category_id": "x"}]},
        {"categories": [{**_GOOD_L, "category_id": None}]},
        {"categories": [{**_GOOD_L, "source": None}]},
    ],
)
def test_bad_value_types_are_quarantined(db, snap):
    data_dir = Path(db.db_path)
    ts.snapshot_file_path(data_dir).write_text(json.dumps(snap), encoding="utf-8")
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 0,
        "missing": 0,
    }
    assert not ts.snapshot_file_path(data_dir).exists()
    assert len(list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))) == 1


def test_good_value_types_pass_validation():
    assert ts._valid_shape(
        {
            "taken_at": "t",
            "judgments": [_GOOD_J, {**_GOOD_J, "passed": 1, "seen_at": "t"}],
            "categories": [_GOOD_L],
            "favorites": ["/x.png"],
        }
    )


@pytest.mark.parametrize("exc", [TypeError("bad"), ValueError("bad")])
def test_restore_rejecting_the_data_quarantines_the_file(db, monkeypatch, caplog, exc):
    _pending_snapshot(db)
    data_dir = Path(db.db_path)

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(db, "restore_taste_state", boom)
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert ts.apply_pending_taste_snapshot(db, data_dir) == {
            "restored": 0,
            "missing": 0,
        }
    assert not ts.snapshot_file_path(data_dir).exists()
    assert len(list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))) == 1
    assert any("corrupt" in r.getMessage().lower() for r in caplog.records)


def test_read_error_keeps_the_file_in_place(db, monkeypatch, caplog):
    snap = _pending_snapshot(db)
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    real_read = Path.read_text

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("locked")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", locked)
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert ts.apply_pending_taste_snapshot(db, data_dir) == {
            "restored": 0,
            "missing": 0,
        }
    monkeypatch.setattr(Path, "read_text", real_read)
    assert json.loads(target.read_text(encoding="utf-8")) == snap
    assert list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*")) == []
    assert any("locked" in r.getMessage() for r in caplog.records)


def test_completed_restore_with_a_stuck_file_never_re_pends_tombstoned_paths(
    db, monkeypatch
):
    snap = _pending_snapshot(db)  # a.png and b.png, neither in the DB
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    real_unlink = Path.unlink
    attempts = []

    def stuck(self, *a, **k):
        if self == target:
            attempts.append(1)
            raise PermissionError("held by antivirus")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", stuck)
    counts = ts.restore_and_persist(db, data_dir, snap, True)
    assert counts["tombstoned"] == 2
    assert len(attempts) == 2  # retried once

    # The end-of-scan apply must not bring the tombstoned paths back.
    ts.apply_pending_taste_snapshot(db, data_dir)
    fresh = db.export_taste_state()
    merged = ts.merge_pending_into_snapshot(data_dir, fresh)
    assert merged["judgments"] == [] and merged["favorites"] == []
    assert len(db.list_tombstones()) == 2


def test_quarantine_names_never_collide(tmp_path):
    for _ in range(3):
        ts.snapshot_file_path(tmp_path).write_text("{bad", encoding="utf-8")
        assert ts.load_pending_snapshot(tmp_path) is None
    kept = list(tmp_path.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))
    assert len(kept) == 3
    assert all(str(os.getpid()) in k.name for k in kept)
