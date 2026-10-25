"""Pending taste snapshot: the file a full clean leaves behind is applied
later (startup, end of every scan) and survives corruption without taking
the server down."""

import asyncio
import json
import logging
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services import taste_snapshot as ts
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from tests.test_grades_migration import _build_v5_db

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


# A seeded rating as (★ favorite, grade): ★ with ✓, what a pre-grades
# favorite became, and ✗ without ★, what an old reject became.
FAVORITE = (True, "pass")
REJECT = (False, "fail")


def _marks(state):
    return (state.favorite, state.grade)


def _favorite(db, path, now):
    """★ + ✓: a favorite that was also graded pass."""
    db.set_favorite_state(path, True, now=now)
    db.set_grade(path, "pass", now=now)


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
    _favorite(db, "/p/a.png", T0)
    db.set_grade("/p/b.png", "fail", now=T0)
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
    assert _marks(db.get_judgment("/p/a.png")) == FAVORITE
    left = json.loads(ts.snapshot_file_path(Path(db.db_path)).read_text())
    assert [j["file_path"] for j in left["judgments"]] == ["/p/b.png"]
    assert db.list_tombstones() == []

    db.save_media(_media("/p/b.png"))
    assert ts.apply_pending_taste_snapshot(db, Path(db.db_path)) == {
        "restored": 1,
        "missing": 0,
    }
    assert _marks(db.get_judgment("/p/b.png")) == REJECT
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
    _favorite(db, "/p/c.png", T0)
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
    db.set_grade("/p/a.png", "fail", now=T0)  # newer than the pending one
    fresh = db.export_taste_state()
    merged = ts.merge_pending_into_snapshot(Path(db.db_path), fresh)
    a = [j for j in merged["judgments"] if j["file_path"] == "/p/a.png"]
    assert len(a) == 1 and a[0]["grade"] == "fail"
    assert "/p/a.png" not in merged["favorites"]
    assert snap  # sanity


def test_lifespan_helper_applies_and_never_raises(db, monkeypatch):
    from backend import main as main_mod

    _pending_snapshot(db)
    db.save_media(_media("/p/a.png"))
    asyncio.run(main_mod._apply_pending_taste_snapshot(db, Path(db.db_path)))
    assert _marks(db.get_judgment("/p/a.png")) == FAVORITE

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


# A judgment row of a version-less (pre-grades) file, and of a v2 file.
_GOOD_J = {
    "file_path": "/x.png",
    "passed": 0,
    "reasons": "[]",
    "seen_at": None,
    "judged_at": None,
    "confirmed_at": None,
}
_V2_J = {
    "file_path": "/x.png",
    "grade": "fail",
    "reasons": "[]",
    "seen_at": None,
    "judged_at": "t",
    "confirmed_at": "t",
}
_GOOD_L = {
    "file_path": "/x.png",
    "category_id": 1,
    "category_name": "A",
    "source": "viewer",
    "assigned_at": "2026-01-01T00:00:00+00:00",
}


def _v2(*judgments):
    return {
        "version": 2,
        "taken_at": "t",
        "judgments": list(judgments),
        "categories": [],
        "favorites": [],
    }


def _assert_quarantined(db, snap):
    """Write ``snap`` as the pending file and apply it: it must be set aside
    unread -- nothing restored, the file renamed .corrupt-*, byte for byte."""
    data_dir = Path(db.db_path)
    raw = json.dumps(snap)
    ts.snapshot_file_path(data_dir).write_text(raw, encoding="utf-8")
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 0,
        "missing": 0,
    }
    assert not ts.snapshot_file_path(data_dir).exists()
    [kept] = list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))
    assert kept.read_text(encoding="utf-8") == raw


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
        _v2({k: v for k, v in _V2_J.items() if k != "grade"}),
        _v2({**_V2_J, "reasons": ["light"]}),
        _v2({**_V2_J, "judged_at": 5}),
    ],
)
def test_bad_value_types_are_quarantined(db, snap):
    _assert_quarantined(db, snap)


def test_good_value_types_pass_validation():
    assert ts._valid_shape(
        {
            "taken_at": "t",
            "judgments": [_GOOD_J, {**_GOOD_J, "passed": 1, "seen_at": "t"}],
            "categories": [_GOOD_L],
            "favorites": ["/x.png"],
        }
    )
    assert ts._valid_shape(
        {
            **_v2(_V2_J, {**_V2_J, "grade": "pass"}, {**_V2_J, "grade": None}),
            "categories": [_GOOD_L],
            "favorites": ["/x.png"],
        }
    )
    # The interim export: no version, a grade beside the legacy flag.
    assert ts._valid_shape(
        {"judgments": [{**_GOOD_J, "grade": None}, {**_V2_J, "passed": 1}]}
    )


_BAD_GRADES = ["maybe", "PASS", "", 1, 0, True, ["pass"], {"pass": 1}]


@pytest.mark.parametrize("grade", _BAD_GRADES)
def test_bad_v2_grade_value_is_quarantined(db, grade):
    """A grade other than "pass" / "fail" / null sets the file aside before
    anything is restored, while the same file with a real grade is read."""
    assert ts._valid_shape(_v2(_V2_J))
    _assert_quarantined(db, _v2({**_V2_J, "grade": grade}))


@pytest.mark.parametrize("grade", _BAD_GRADES)
def test_bad_grade_in_a_versionless_file_is_quarantined(db, grade):
    """The interim export (no version, a grade beside ``passed``) has its
    grades checked the same way -- even for a path that is not back, whose
    row the restore would never write."""
    good = {"judgments": [{**_V2_J, "passed": 1}]}
    assert ts._valid_shape(good)
    _assert_quarantined(db, {"judgments": [{**_V2_J, "passed": 1, "grade": grade}]})


@pytest.mark.parametrize("version", [1, 4, "2", "3", None, 2.5, True])
def test_unknown_snapshot_version_is_quarantined(db, version):
    """Only versions 2 and 3 and version-less (pre-v2) files are understood;
    any other version is set aside rather than read with a guessed meaning
    -- here with rows that would be valid in every known shape."""
    row = {**_V2_J, "passed": 1}
    assert ts._valid_shape(_v2(row)) and ts._valid_shape({"judgments": [row]})
    _assert_quarantined(db, {**_v2(row), "version": version})


def test_v2_leftovers_never_gain_a_grade(db):
    """A ★-only path left pending comes back ★ only: the rewritten file
    keeps its version even with no judgment row left in it, so it is never
    read as a pre-grades file (whose favorites mean ✓)."""
    for p in ("/p/star.png", "/p/x.png"):
        db.save_media(_media(p))
    db.set_favorite_state("/p/star.png", True, now=T0)
    db.set_grade("/p/x.png", "fail", now=T0)
    snap = db.export_taste_state()
    db.truncate_all_data()
    data_dir = Path(db.db_path)
    ts.write_snapshot_file(data_dir, snap)

    db.save_media(_media("/p/x.png"))
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 1,
        "missing": 1,
    }
    db.save_media(_media("/p/star.png"))
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 1,
        "missing": 0,
    }

    assert _marks(db.get_judgment("/p/star.png")) == (True, None)
    assert _marks(db.get_judgment("/p/x.png")) == REJECT
    assert not ts.snapshot_file_path(data_dir).exists()


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("bad"),
        ValueError("bad"),
        KeyError("grade"),
        sqlite3.IntegrityError("CHECK constraint failed"),
        sqlite3.ProgrammingError("Error binding parameter 2"),
    ],
)
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
    real_read = Path.read_bytes

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("locked")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        assert ts.apply_pending_taste_snapshot(db, data_dir) == {
            "restored": 0,
            "missing": 0,
        }
    monkeypatch.setattr(Path, "read_bytes", real_read)
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


def test_unreadable_file_is_signalled_and_apply_leaves_it(db, monkeypatch):
    snap = _pending_snapshot(db)
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    real_read = Path.read_bytes

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("locked")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(ts.PendingSnapshotUnreadable):
        ts.load_pending_snapshot(data_dir)
    with pytest.raises(ts.PendingSnapshotUnreadable):
        ts.apply_pending_taste_snapshot(db, data_dir, raise_unreadable=True)
    with pytest.raises(ts.PendingSnapshotUnreadable):
        ts.merge_pending_into_snapshot(data_dir, db.export_taste_state())
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 0,
        "missing": 0,
    }
    monkeypatch.setattr(Path, "read_bytes", real_read)
    assert json.loads(target.read_text(encoding="utf-8")) == snap


def test_corrupt_file_that_cannot_be_renamed_is_signalled(db, monkeypatch):
    data_dir = Path(db.db_path)
    ts.snapshot_file_path(data_dir).write_text("{bad", encoding="utf-8")

    def no_rename(a, b):
        raise PermissionError("locked")

    monkeypatch.setattr(ts.os, "replace", no_rename)
    with pytest.raises(ts.PendingSnapshotUnreadable):
        ts.load_pending_snapshot(data_dir)
    assert ts.snapshot_file_path(data_dir).read_text(encoding="utf-8") == "{bad"


def test_real_export_shapes_validate_and_restore(db):
    """Every shape export_taste_state really produces must pass the strict
    validator: seen-only rows (NULL judged_at / confirmed_at), favorites
    with reasons, rejects, migration-seeded favorites, a favorite with no
    judgment row, ✓ without ★, ★ beside ✗, and category links of every
    source."""
    names = ("seen", "fav", "reject", "mig", "bare", "cat", "graded", "star_fail")
    paths = [f"/r/{n}.png" for n in names]
    for p in paths:
        db.save_media(_media(p))
    cat = db.create_category("Portrait")
    db.mark_seen("/r/seen.png", now=T0)
    _favorite(db, "/r/fav.png", T0)
    db.set_reason("/r/fav.png", "light", True, now=T0)
    db.set_grade("/r/reject.png", "fail", now=T0)
    db.set_grade("/r/graded.png", "pass", now=T0)
    db.set_favorite_state("/r/star_fail.png", True, now=T0)
    db.set_grade("/r/star_fail.png", "fail", now=T0)
    db.set_reason("/r/star_fail.png", "concept", True, now=T0)
    db.add_media_categories(["/r/cat.png"], cat.id, "viewer")
    internal = {src: db.create_category(f"C-{src}") for src in ("generation", "sticky")}
    with db._get_connection() as conn:
        # user_version 5 migration seed: favorite + judgment row, no reasons.
        conn.execute(
            "UPDATE media SET is_favorite = 1 WHERE file_path = ?", ("/r/mig.png",)
        )
        conn.execute(
            "INSERT INTO media_judgments (file_path, reasons, seen_at, "
            "judged_at, confirmed_at) VALUES (?, '[]', ?, ?, ?)",
            ("/r/mig.png", "2026-01-01 00:00:00", "2026-01-01 00:00:00", None),
        )
        # Legacy favorite with no judgment row at all.
        conn.execute(
            "UPDATE media SET is_favorite = 1 WHERE file_path = ?", ("/r/bare.png",)
        )
        # Server-internal link sources.
        for src, other in internal.items():
            conn.execute(
                "INSERT INTO media_categories (file_path, category_id, source, "
                "assigned_at) VALUES (?, ?, ?, ?)",
                ("/r/cat.png", other.id, src, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()

    snap = db.export_taste_state()
    assert any(j["judged_at"] is None for j in snap["judgments"])  # seen-only
    data_dir = Path(db.db_path)
    ts.write_snapshot_file(data_dir, snap)
    loaded = ts.load_pending_snapshot(data_dir)
    assert loaded == json.loads(json.dumps(snap))
    assert ts._valid_shape(loaded)

    before = (
        db.get_judgment("/r/fav.png"),
        db.get_judgment("/r/mig.png"),
        db.get_judgment("/r/seen.png"),
        db.get_judgment("/r/graded.png"),
        db.get_judgment("/r/star_fail.png"),
        db.get_media_categories("/r/cat.png"),
    )
    db.truncate_all_data()
    for p in paths:
        db.save_media(_media(p))
    counts = ts.apply_pending_taste_snapshot(db, data_dir)
    assert counts == {"restored": len(paths), "missing": 0}
    assert list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*")) == []
    assert not ts.snapshot_file_path(data_dir).exists()
    after = (
        db.get_judgment("/r/fav.png"),
        db.get_judgment("/r/mig.png"),
        db.get_judgment("/r/seen.png"),
        db.get_judgment("/r/graded.png"),
        db.get_judgment("/r/star_fail.png"),
        db.get_media_categories("/r/cat.png"),
    )
    assert after == before
    # A ★ with no judgment row comes back as ★ alone: the restore never
    # derives a grade.
    assert _marks(db.get_judgment("/r/bare.png")) == (True, None)
    assert _marks(db.get_judgment("/r/reject.png")) == REJECT
    assert _marks(db.get_judgment("/r/graded.png")) == (False, "pass")
    star_fail = db.get_judgment("/r/star_fail.png")
    assert (_marks(star_fail), star_fail.reasons) == ((True, "fail"), ["concept"])


# ----------------------------------------------------------------------
# Pending files written before grades (no version key)
# ----------------------------------------------------------------------

T_SEEN = "2026-03-01T10:00:00+00:00"
T_JUDGED = "2026-03-02T11:00:00+00:00"
T_CONFIRMED = "2026-03-09T12:00:00+00:00"
T_ASSIGNED = "2026-03-03T13:00:00+00:00"


def _legacy_j(path, passed, reasons="[]", judged=True):
    return {
        "file_path": path,
        "passed": passed,
        "reasons": reasons,
        "seen_at": T_SEEN,
        "judged_at": T_JUDGED if judged else None,
        "confirmed_at": T_CONFIRMED if judged else None,
    }


# A pending file exactly as user_version 5 wrote it: no version key, old
# meaning (``passed = 1`` is a reject; a favorite is ★ and the positive).
LEGACY_SNAPSHOT = {
    "taken_at": "2026-03-10T08:00:00+00:00",
    "judgments": [
        _legacy_j("/lib/cleared.png", 0),  # favorited, then unfavorited
        _legacy_j("/lib/fav.png", 0, '["light"]'),
        _legacy_j("/lib/reject.png", 1, '["composition", "concept"]'),
        _legacy_j("/lib/seen.png", 0, judged=False),
    ],
    "categories": [
        {
            "file_path": "/lib/reject.png",
            "category_id": 1,
            "category_name": "Landscape",
            "source": "viewer",
            "assigned_at": T_ASSIGNED,
        }
    ],
    # fav_bare.png: a favorite with no judgment row.
    "favorites": ["/lib/fav.png", "/lib/fav_bare.png"],
}
LEGACY_PATHS = [
    "/lib/cleared.png",
    "/lib/fav.png",
    "/lib/fav_bare.png",
    "/lib/reject.png",
    "/lib/seen.png",
]


def _add_v5_categories(db_file):
    """Categories survive a full clean; give the v5 file the one the legacy
    snapshot links to."""
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(
            "CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE COLLATE NOCASE, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO categories (id, name, created_at) "
            "VALUES (1, 'Landscape', ?)",
            (T_SEEN,),
        )
        conn.commit()
    finally:
        conn.close()


def _user_version(db_file):
    conn = sqlite3.connect(str(db_file))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_legacy_pending_snapshot_restores_with_old_meaning(tmp_path):
    """Review Focus 1: a pending file from before grades, still on disk at
    the first v6 start, is applied after the migration with its old meaning
    -- ``passed = 1`` is ✗, a favorite is ★ + ✓ -- and is consumed, never
    quarantined for its shape. A put-back: original timestamps, no events."""
    from backend import main as main_mod

    db_dir = tmp_path / "db"
    # The v5 full clean re-imported every path, then the server stopped
    # before putting the ratings back.
    db_file = _build_v5_db(db_dir, live={p: (0, None) for p in LEGACY_PATHS})
    _add_v5_categories(db_file)
    target = ts.snapshot_file_path(db_dir)
    target.write_text(json.dumps(LEGACY_SNAPSHOT), encoding="utf-8")
    started = datetime.now(timezone.utc)

    db = DatabaseManager(db_dir)  # startup: migrations first...
    assert _user_version(db_file) == 7
    asyncio.run(main_mod._apply_pending_taste_snapshot(db, db_dir))  # ...then this

    assert {p: _marks(db.get_judgment(p)) for p in LEGACY_PATHS} == {
        "/lib/cleared.png": (False, None),
        "/lib/fav.png": (True, "pass"),
        "/lib/fav_bare.png": (True, "pass"),
        "/lib/reject.png": (False, "fail"),
        "/lib/seen.png": (False, None),
    }
    reject = db.get_judgment("/lib/reject.png")
    assert (reject.reasons, reject.seen_at, reject.judged_at, reject.confirmed_at) == (
        ["composition", "concept"],
        T_SEEN,
        T_JUDGED,
        T_CONFIRMED,
    )
    assert [c.name for c in reject.categories] == ["Landscape"]
    fav = db.get_judgment("/lib/fav.png")
    assert (fav.reasons, fav.judged_at, fav.confirmed_at) == (
        ["light"],
        T_JUDGED,
        T_CONFIRMED,
    )
    seen = db.get_judgment("/lib/seen.png")
    assert (seen.seen_at, seen.judged_at) == (T_SEEN, None)
    # A favorite with no row gets a ✓ dated now, as v6 dates the row it
    # creates for one (otherwise the grade would have no decay clock).
    bare = db.get_judgment("/lib/fav_bare.png")
    assert bare.judged_at == bare.confirmed_at
    assert (
        started <= datetime.fromisoformat(bare.judged_at) <= datetime.now(timezone.utc)
    )
    assert db.list_judgment_events() == []
    assert not target.exists()
    assert list(db_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*")) == []


def test_legacy_leftovers_stay_pending_with_their_old_meaning(db):
    """What has not come back yet stays pending, rewritten as a current
    (version 3) file that already carries the old meaning -- so it means the
    same whenever it is applied, or tombstoned by a later full clean."""
    data_dir = Path(db.db_path)
    db.create_category("Landscape")
    target = ts.snapshot_file_path(data_dir)
    target.write_text(json.dumps(LEGACY_SNAPSHOT), encoding="utf-8")
    db.save_media(_media("/lib/fav.png"))

    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 1,
        "missing": 4,
    }

    assert _marks(db.get_judgment("/lib/fav.png")) == (True, "pass")
    left = json.loads(target.read_text(encoding="utf-8"))
    assert left["version"] == 3
    assert (left["briefs"], left["library_prompts"]) == ([], [])
    assert {j["file_path"]: j["grade"] for j in left["judgments"]} == {
        "/lib/cleared.png": None,
        "/lib/fav_bare.png": "pass",
        "/lib/reject.png": "fail",
        "/lib/seen.png": None,
    }
    assert len(left["judgments"]) == 4
    assert all("passed" not in j for j in left["judgments"])
    assert left["favorites"] == ["/lib/fav_bare.png"]
    assert list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*")) == []

    for p in ("/lib/reject.png", "/lib/fav_bare.png"):
        db.save_media(_media(p))
    ts.apply_pending_taste_snapshot(db, data_dir)
    reject = db.get_judgment("/lib/reject.png")
    assert (_marks(reject), reject.reasons) == (
        (False, "fail"),
        ["composition", "concept"],
    )
    assert _marks(db.get_judgment("/lib/fav_bare.png")) == (True, "pass")


# (★ in v5, the v5 judgment row's ``passed`` -- None: no row) -> the grade
# v6 gives that rating.
@pytest.mark.parametrize(
    "favorite, passed, grade",
    [
        (True, 0, "pass"),
        (True, None, "pass"),
        # Never written by v5 code, but v5 read it as a favorite (★ first).
        (True, 1, "pass"),
        (False, 1, "fail"),
        (False, 0, None),
    ],
)
def test_legacy_snapshot_and_v6_migration_give_one_meaning(
    tmp_path, favorite, passed, grade
):
    """One v5 rating means the same after the upgrade whether it was live
    (migrated) or waiting in a pending full-clean file (translated)."""
    path = "/lib/x.png"
    row = None if passed is None else _legacy_j(path, passed)
    live = {path: (1 if favorite else 0, row)}
    _build_v5_db(tmp_path / "live", live=live)
    migrated = DatabaseManager(tmp_path / "live")

    _build_v5_db(tmp_path / "pending", live={path: (0, None)})
    ts.snapshot_file_path(tmp_path / "pending").write_text(
        json.dumps(
            {
                "taken_at": T_CONFIRMED,
                "judgments": [] if row is None else [row],
                "categories": [],
                "favorites": [path] if favorite else [],
            }
        ),
        encoding="utf-8",
    )
    restored = DatabaseManager(tmp_path / "pending")
    ts.apply_pending_taste_snapshot(restored, tmp_path / "pending")

    assert _marks(migrated.get_judgment(path)) == (favorite, grade)
    assert _marks(restored.get_judgment(path)) == (favorite, grade)


def test_upgrade_legacy_snapshot_translates_the_old_meaning():
    now = datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc)
    link = {**_GOOD_L, "file_path": "/a.png"}
    legacy = {
        "taken_at": "t0",
        "judgments": [
            _legacy_j("/a.png", 1, '["light"]'),
            _legacy_j("/b.png", 0),
            _legacy_j("/c.png", 0, judged=False),
            _legacy_j("/d.png", 1),
        ],
        "categories": [link],
        "favorites": ["/b.png", "/d.png", "/e.png"],
    }

    def v2_row(path, grade, reasons="[]", judged=True):
        return {**_legacy_j(path, 0, reasons, judged), "grade": grade}

    expected_rows = [
        v2_row("/a.png", "fail", '["light"]'),
        v2_row("/b.png", "pass"),
        v2_row("/c.png", None, judged=False),
        v2_row("/d.png", "pass"),  # ★ first, as v5 read it and v6 maps it
        {
            "file_path": "/e.png",  # a favorite with no row
            "grade": "pass",
            "reasons": "[]",
            "seen_at": now.isoformat(),
            "judged_at": now.isoformat(),
            "confirmed_at": now.isoformat(),
        },
    ]
    for r in expected_rows:
        r.pop("passed", None)

    out = ts.upgrade_legacy_snapshot(legacy, now=now)

    assert sorted(out["judgments"], key=lambda j: j["file_path"]) == expected_rows
    assert {k: v for k, v in out.items() if k != "judgments"} == {
        "version": 3,
        "taken_at": "t0",
        "categories": [link],
        "favorites": ["/b.png", "/d.png", "/e.png"],
        "briefs": [],
        "library_prompts": [],
    }


def test_upgrade_keeps_the_grades_a_versionless_file_already_has():
    """The interim export (no version, a grade on every row) is restored as
    stored: its ``passed`` flag is not read, a starred row keeps the grade it
    has, and a ★ with no row stays ★ alone (★ is not a grade)."""
    interim = {
        "taken_at": "t0",
        "judgments": [
            {**_legacy_j("/a.png", 0, judged=False), "grade": None},
            {**_legacy_j("/b.png", 1), "grade": "fail"},
        ],
        "categories": [],
        "favorites": ["/a.png", "/c.png"],
    }
    rows = [
        {**_legacy_j("/a.png", 0, judged=False), "grade": None},
        {**_legacy_j("/b.png", 1), "grade": "fail"},
    ]
    for r in rows:
        r.pop("passed")

    out = ts.upgrade_legacy_snapshot(interim)

    assert sorted(out["judgments"], key=lambda j: j["file_path"]) == rows
    assert {k: v for k, v in out.items() if k != "judgments"} == {
        "version": 3,
        "taken_at": "t0",
        "categories": [],
        "favorites": ["/a.png", "/c.png"],
        "briefs": [],
        "library_prompts": [],
    }
