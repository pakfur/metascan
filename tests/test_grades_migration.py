"""user_version 5 -> 6: today's ratings become pass / fail grades (spec
docs/superpowers/specs/2026-10-24-pass-fail-grades-design.md, "Migration"
and "Bugs fixed on the way").

The fixture is a v5 database built here with the pre-v6 schema (never by
the code under test): favorites with and without a judgment row, rejects
with reasons (the old ``passed = 1``), seen-only rows, tombstones of each
kind, and legacy ``favorite`` / ``pass`` events carrying ``score_shown``.
"""

import json
import logging
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from metascan.core import database_sqlite
from metascan.core.database_sqlite import (
    DatabaseManager,
    MigrationError,
    backup_before_v6,
)
from metascan.core.media import Media

# The rating tables exactly as user_version 5 created them, plus a media
# table with every column a v5 database has.
_V5_SCHEMA = """
CREATE TABLE media (
    file_path TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    is_favorite INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    playback_speed REAL DEFAULT NULL,
    width INTEGER, height INTEGER, file_size INTEGER, frame_rate REAL,
    duration REAL, modified_at TEXT,
    camera_make TEXT, camera_model TEXT, lens_model TEXT,
    datetime_original TEXT, gps_latitude REAL, gps_longitude REAL,
    gps_altitude REAL, orientation INTEGER, photo_exposure TEXT,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE media_judgments (
    file_path TEXT PRIMARY KEY
        REFERENCES media(file_path) ON DELETE CASCADE,
    passed INTEGER NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',
    seen_at TEXT,
    judged_at TEXT,
    confirmed_at TEXT
);
CREATE TABLE judgment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    event TEXT NOT NULL,
    reason TEXT,
    category TEXT,
    score_shown REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE media_tombstones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    is_favorite INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    categories TEXT NOT NULL DEFAULT '[]',
    seen_at TEXT,
    confirmed_at TEXT,
    embedding BLOB,
    embedding_model TEXT,
    lineage TEXT
);
"""

T_SEEN = "2026-03-01T10:00:00+00:00"
T_JUDGED = "2026-03-02T11:00:00+00:00"
T_CONFIRMED = "2026-03-09T12:00:00+00:00"
T_DELETED = "2026-04-01T09:00:00+00:00"


def _judgment(passed: int, reasons: List[str], judged: bool = True) -> Dict[str, Any]:
    return {
        "passed": passed,
        "reasons": json.dumps(reasons),
        "seen_at": T_SEEN,
        "judged_at": T_JUDGED if judged else None,
        "confirmed_at": T_CONFIRMED if judged else None,
    }


# path -> (media.is_favorite, media_judgments row or None)
LIVE: Dict[str, Any] = {
    "/lib/fav.png": (1, _judgment(0, ["light"])),
    "/lib/fav_bare.png": (1, None),  # a favorite with no judgment row
    "/lib/reject.png": (0, _judgment(1, ["composition", "concept"])),
    "/lib/seen.png": (0, _judgment(0, [], judged=False)),
    "/lib/cleared.png": (0, _judgment(0, [])),  # favorited, then unfavorited
    # Never written by v5 code, but v5 read it as a favorite (★ wins).
    "/lib/both.png": (1, _judgment(1, [])),
    "/lib/plain.png": (0, None),
}
EXPECTED_GRADES = {
    "/lib/fav.png": "pass",
    "/lib/fav_bare.png": "pass",
    "/lib/reject.png": "fail",
    "/lib/seen.png": None,
    "/lib/cleared.png": None,
    "/lib/both.png": "pass",
}
# (file_path, event, score_shown, created_at)
EVENTS = [
    ("/lib/fav.png", "seen", 0.5, T_SEEN),
    ("/lib/fav.png", "favorite", 0.71, T_JUDGED),
    ("/lib/reject.png", "favorite", None, T_SEEN),
    ("/lib/reject.png", "unfavorite", 0.4, T_JUDGED),
    ("/lib/reject.png", "pass", 0.4, T_JUDGED),
    ("/lib/seen.png", "seen", 0.66, T_SEEN),
    ("/lib/cleared.png", "pass", None, T_SEEN),
    ("/lib/cleared.png", "unpass", None, T_JUDGED),
]
# (file_path, is_favorite, passed, vector seed or None, embedding_model)
TOMBS = [
    ("/gone/fav.png", 1, 0, 1, "clip-small"),
    ("/gone/reject.png", 0, 1, 2, "clip-large"),
    ("/gone/seen.png", 0, 0, None, None),
    ("/gone/fav_novec.png", 1, 0, None, None),
    ("/gone/both.png", 1, 1, 3, "clip-small"),
]
EXPECTED_TOMB_GRADES = {
    "/gone/fav.png": "pass",
    "/gone/reject.png": "fail",
    "/gone/seen.png": None,
    "/gone/fav_novec.png": "pass",
    "/gone/both.png": "pass",
}

BAK_RE = re.compile(r"^metascan\.db\.pre-v6-\d{8}-\d{6}(-\d+)?\.bak$")


def _media(path: str, favorite: bool = False) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime(2026, 1, 1),
        modified_at=datetime(2026, 1, 1),
        is_favorite=favorite,
    )


def _vector(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(32).astype(np.float32).tobytes()


def _build_v5_db(db_dir: Path, live: Optional[Dict[str, Any]] = None) -> Path:
    """Write a user_version 5 database with the pre-v6 schema to
    ``db_dir/metascan.db``; ``live`` defaults to every rating case."""
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "metascan.db"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_V5_SCHEMA)
        for path, (fav, j) in (LIVE if live is None else live).items():
            conn.execute(
                "INSERT INTO media (file_path, data, is_favorite) VALUES (?, ?, ?)",
                (path, _media(path).to_json(), fav),
            )
            if j is not None:
                conn.execute(
                    "INSERT INTO media_judgments (file_path, passed, reasons, "
                    "seen_at, judged_at, confirmed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        path,
                        j["passed"],
                        j["reasons"],
                        j["seen_at"],
                        j["judged_at"],
                        j["confirmed_at"],
                    ),
                )
        if live is None:
            conn.executemany(
                "INSERT INTO judgment_events (file_path, event, score_shown, "
                "created_at) VALUES (?, ?, ?, ?)",
                EVENTS,
            )
            for path, fav, passed, seed, model in TOMBS:
                conn.execute(
                    "INSERT INTO media_tombstones (file_path, deleted_at, "
                    "is_favorite, passed, reasons, categories, seen_at, "
                    "confirmed_at, embedding, embedding_model, lineage) "
                    "VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, NULL)",
                    (
                        path,
                        T_DELETED,
                        fav,
                        passed,
                        json.dumps(["light"] if passed else []),
                        T_SEEN,
                        T_CONFIRMED,
                        _vector(seed) if seed is not None else None,
                        model,
                    ),
                )
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
    finally:
        conn.close()
    return db_file


def _query(db_file: Path, sql: str) -> List[tuple]:
    conn = sqlite3.connect(str(db_file))
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _user_version(db_file: Path) -> int:
    return int(_query(db_file, "PRAGMA user_version")[0][0])


def _kept_state(db_file: Path) -> Dict[str, List[tuple]]:
    """Every pre-v6 column of the rating tables that v6 keeps: what it must
    never change."""
    return {
        "favorites": _query(
            db_file, "SELECT file_path, is_favorite FROM media ORDER BY file_path"
        ),
        "judgments": _query(
            db_file,
            "SELECT file_path, reasons, seen_at, judged_at, confirmed_at "
            "FROM media_judgments ORDER BY file_path",
        ),
        "events": _query(db_file, "SELECT * FROM judgment_events ORDER BY id"),
        "tombstones": _query(
            db_file,
            "SELECT id, file_path, deleted_at, is_favorite, reasons, "
            "categories, seen_at, confirmed_at, embedding, embedding_model, "
            "lineage FROM media_tombstones ORDER BY id",
        ),
    }


def _legacy_state(db_file: Path) -> Dict[str, List[tuple]]:
    """The whole v5 rating state: what v6 keeps plus the old reject flags
    (``passed``) it drops once it has read them. A failed upgrade, and the
    backup, leave all of it as it was."""
    return {
        **_kept_state(db_file),
        "rejects": _query(
            db_file, "SELECT file_path, passed FROM media_judgments ORDER BY 1"
        ),
        "tomb_rejects": _query(
            db_file, "SELECT id, passed FROM media_tombstones ORDER BY id"
        ),
    }


def _grades(db_file: Path) -> Dict[str, Optional[str]]:
    return dict(_query(db_file, "SELECT file_path, grade FROM media_judgments"))


def _tomb_grades(db_file: Path) -> Dict[str, Optional[str]]:
    return dict(_query(db_file, "SELECT file_path, grade FROM media_tombstones"))


def _v6_state(db_file: Path) -> Dict[str, List[tuple]]:
    """Everything the migration writes, plus everything it must not."""
    return {
        **_kept_state(db_file),
        "grades": sorted(_grades(db_file).items()),
        "judgment_rows": _query(
            db_file, "SELECT * FROM media_judgments ORDER BY file_path"
        ),
        "tomb_rows": _query(db_file, "SELECT * FROM media_tombstones ORDER BY id"),
        "vectors": _query(
            db_file, "SELECT * FROM tombstone_vectors ORDER BY tombstone_id"
        ),
        "version": [(_user_version(db_file),)],
    }


def _backups(db_dir: Path) -> List[Path]:
    return sorted(db_dir.glob("metascan.db.pre-v6-*"))


def _assert_untouched_v5(db_file: Path, before: Dict[str, List[tuple]]) -> None:
    assert _user_version(db_file) == 5
    assert _legacy_state(db_file) == before
    assert set(_grades(db_file).values()) <= {None}
    assert set(_tomb_grades(db_file).values()) <= {None}
    assert _query(db_file, "SELECT COUNT(*) FROM tombstone_vectors") == [(0,)]


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def test_v6_maps_favorites_to_pass_and_rejects_to_fail(tmp_path, caplog):
    db_file = _build_v5_db(tmp_path / "db")
    before = _kept_state(db_file)
    caplog.set_level(logging.INFO, logger="metascan.core.database_sqlite")
    DatabaseManager(tmp_path / "db")

    assert _user_version(db_file) == 6
    assert _grades(db_file) == EXPECTED_GRADES  # /lib/plain.png: still no row
    # ★ is untouched: favorites stay favorites, nothing becomes one.
    assert _kept_state(db_file)["favorites"] == before["favorites"]
    # The old reject flags were read, then dropped (tests/test_grades_guard.py).
    dropped = 2 if database_sqlite._can_drop_columns() else 0
    # One INFO line with the counts, and no file paths in it.
    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO
        and r.getMessage().startswith("Database upgraded to version 6")
    ]
    assert "favorites -> pass 3 (judgment rows created 1)" in line
    assert "rejects -> fail 1" in line
    assert "tombstones -> pass 3 / fail 1" in line
    assert "tombstone vectors copied 3" in line
    assert f"old reject columns dropped {dropped}" in line
    assert "/lib/" not in line and "/gone/" not in line


def test_v6_creates_a_judgment_row_for_a_rowless_favorite(tmp_path):
    db_file = _build_v5_db(tmp_path / "db")
    t0 = datetime.now(timezone.utc)
    DatabaseManager(tmp_path / "db")
    t1 = datetime.now(timezone.utc)

    [row] = _query(
        db_file,
        "SELECT grade, reasons, seen_at, judged_at, confirmed_at "
        "FROM media_judgments WHERE file_path = '/lib/fav_bare.png'",
    )
    grade, reasons, seen_at, judged_at, confirmed_at = row
    assert (grade, reasons) == ("pass", "[]")
    assert seen_at == judged_at == confirmed_at
    assert t0 <= datetime.fromisoformat(judged_at) <= t1
    # One migration time for everything the migration stamps.
    assert {
        r[0] for r in _query(db_file, "SELECT created_at FROM tombstone_vectors")
    } == {judged_at}
    # A non-favorite without a row gets none.
    assert _query(
        db_file,
        "SELECT COUNT(*) FROM media_judgments WHERE file_path = '/lib/plain.png'",
    ) == [(0,)]


def test_v6_keeps_timestamps_reasons_and_writes_no_events(tmp_path):
    db_file = _build_v5_db(tmp_path / "db")
    before = _kept_state(db_file)
    DatabaseManager(tmp_path / "db")
    after = _kept_state(db_file)

    # Every pre-existing judgment row: reasons, seen_at, judged_at,
    # confirmed_at exactly as they were (decay clocks keep running).
    old_paths = {r[0] for r in before["judgments"]}
    assert [r for r in after["judgments"] if r[0] in old_paths] == before["judgments"]
    assert {r[0] for r in after["judgments"]} - old_paths == {"/lib/fav_bare.png"}
    reasons = dict(_query(db_file, "SELECT file_path, reasons FROM media_judgments"))
    assert json.loads(reasons["/lib/fav.png"]) == ["light"]
    assert json.loads(reasons["/lib/reject.png"]) == ["composition", "concept"]
    # No synthetic events: the log is byte-for-byte the v5 log.
    assert after["events"] == before["events"]
    assert len(after["events"]) == len(EVENTS)


def test_v6_maps_tombstones(tmp_path):
    db_file = _build_v5_db(tmp_path / "db")
    before = _kept_state(db_file)
    DatabaseManager(tmp_path / "db")

    assert _tomb_grades(db_file) == EXPECTED_TOMB_GRADES
    # is_favorite (view info), reasons, timestamps, embedding: as-is.
    assert _kept_state(db_file)["tombstones"] == before["tombstones"]
    # The columns later tasks fill start empty.
    assert _query(
        db_file,
        "SELECT COUNT(*) FROM media_tombstones WHERE brief IS NOT NULL OR "
        "brief_model IS NOT NULL OR prompt IS NOT NULL OR "
        "model_name IS NOT NULL OR thumb_path IS NOT NULL",
    ) == [(0,)]


def test_v6_backfills_tombstone_scorecard_ids(tmp_path):
    """Ruling 15: the upgrade records each existing tombstone's scorecard
    (from its T2I lineage) in ``scorecard_id``, the column the scorecard
    results select deleted images by; tombstones written later get it at
    deletion. Anything else -- no T2I lineage, no scorecard, a lineage that
    is not JSON, a scorecard id that is not text -- stays NULL."""
    db_file = _build_v5_db(tmp_path / "db", live={})
    lineages = {
        "/gone/card.png": json.dumps(
            {"t2i": {"id": 7, "scorecard_id": "card-1", "batch_id": "b-1"}}
        ),
        "/gone/plain.png": json.dumps({"t2i": {"id": 8, "scorecard_id": None}}),
        "/gone/clip.mp4": json.dumps({"i2v": {"id": 3, "writer_id": "w"}}),
        "/gone/odd.png": "not json",
        "/gone/number.png": json.dumps({"t2i": {"id": 9, "scorecard_id": 5}}),
        "/gone/lib.png": None,
    }
    conn = sqlite3.connect(str(db_file))
    try:
        conn.executemany(
            "INSERT INTO media_tombstones (file_path, deleted_at, is_favorite, "
            "passed, reasons, lineage) VALUES (?, ?, 0, 1, '[]', ?)",
            [(path, T_DELETED, lineage) for path, lineage in lineages.items()],
        )
        conn.commit()
    finally:
        conn.close()
    DatabaseManager(tmp_path / "db")
    assert _user_version(db_file) == 6
    assert dict(
        _query(db_file, "SELECT file_path, scorecard_id FROM media_tombstones")
    ) == {path: ("card-1" if path == "/gone/card.png" else None) for path in lineages}
    assert dict(_query(db_file, "SELECT file_path, lineage FROM media_tombstones")) == (
        lineages
    )
    # Idempotent: a second open leaves the column alone.
    DatabaseManager(tmp_path / "db")
    assert _query(
        db_file, "SELECT COUNT(*) FROM media_tombstones WHERE scorecard_id IS NOT NULL"
    ) == [(1,)]


def test_v6_copies_tombstone_embeddings_to_tombstone_vectors(tmp_path):
    db_file = _build_v5_db(tmp_path / "db")
    DatabaseManager(tmp_path / "db")

    ids = dict(_query(db_file, "SELECT file_path, id FROM media_tombstones"))
    rows = _query(
        db_file,
        "SELECT tombstone_id, model_key, embedding FROM tombstone_vectors "
        "ORDER BY tombstone_id",
    )
    assert rows == [
        (ids["/gone/fav.png"], "clip-small", _vector(1)),
        (ids["/gone/reject.png"], "clip-large", _vector(2)),
        (ids["/gone/both.png"], "clip-small", _vector(3)),
    ]
    # The tombstone's own embedding column is kept.
    assert _query(
        db_file,
        "SELECT embedding FROM media_tombstones WHERE file_path = '/gone/fav.png'",
    ) == [(_vector(1),)]


# ---------------------------------------------------------------------------
# Backup, idempotence, failure
# ---------------------------------------------------------------------------


def test_v6_backs_up_first_and_is_idempotent(tmp_path, caplog):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)
    DatabaseManager(db_dir)

    [backup] = _backups(db_dir)
    assert BAK_RE.match(backup.name), backup.name
    # The backup is the database as it was before the migration.
    assert _user_version(backup) == 5
    assert _legacy_state(backup) == before
    assert set(_grades(backup).values()) <= {None}
    assert set(_tomb_grades(backup).values()) <= {None}
    assert _query(backup, "SELECT COUNT(*) FROM tombstone_vectors") == [(0,)]
    # A self-contained file: no WAL beside it.
    assert _query(backup, "PRAGMA journal_mode") == [("delete",)]

    migrated = _v6_state(db_file)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="metascan.core.database_sqlite")
    DatabaseManager(db_dir)
    assert _v6_state(db_file) == migrated
    assert [p.name for p in _backups(db_dir)] == [backup.name]
    assert not [r for r in caplog.records if "version 6" in r.getMessage()]


def test_v6_skips_the_backup_with_nothing_to_migrate(tmp_path):
    # Media rows, but no judgment rows, tombstones or favorites.
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(
        db_dir, live={"/lib/a.png": (0, None), "/lib/b.png": (0, None)}
    )
    assert backup_before_v6(db_file, datetime.now(timezone.utc)) is None
    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    assert _backups(db_dir) == []
    assert _query(db_file, "SELECT COUNT(*) FROM media_judgments") == [(0,)]

    # A brand-new database has nothing to migrate either.
    DatabaseManager(tmp_path / "fresh")
    assert _user_version(tmp_path / "fresh" / "metascan.db") == 6
    assert _backups(tmp_path / "fresh") == []


def _v6_count_lines(caplog) -> List[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.getMessage().startswith("Database upgraded to version 6")
    ]


def test_v6_logs_its_counts_at_info_only_when_it_changed_something(tmp_path, caplog):
    # A brand-new database: every count is zero, so the line is DEBUG -- a
    # fresh install does not announce an upgrade.
    caplog.set_level(logging.DEBUG, logger="metascan.core.database_sqlite")
    DatabaseManager(tmp_path / "fresh")
    [line] = _v6_count_lines(caplog)
    assert line.levelno == logging.DEBUG
    assert "favorites -> pass 0" in line.getMessage()

    # A v5 database with no ratings still loses its old reject columns.
    caplog.clear()
    _build_v5_db(tmp_path / "unrated", live={"/lib/a.png": (0, None)})
    DatabaseManager(tmp_path / "unrated")
    [line] = _v6_count_lines(caplog)
    dropped = 2 if database_sqlite._can_drop_columns() else 0
    assert line.levelno == (logging.INFO if dropped else logging.DEBUG)

    # Ratings mapped: INFO (the counts themselves are checked above).
    caplog.clear()
    _build_v5_db(tmp_path / "rated")
    DatabaseManager(tmp_path / "rated")
    [line] = _v6_count_lines(caplog)
    assert line.levelno == logging.INFO


def test_backup_never_overwrites_an_existing_backup(tmp_path):
    db_file = _build_v5_db(tmp_path / "db")
    now = datetime(2026, 10, 24, 17, 30, 5, tzinfo=timezone.utc)
    first = backup_before_v6(db_file, now)
    second = backup_before_v6(db_file, now)
    assert first is not None and second is not None
    assert first.name == "metascan.db.pre-v6-20261024-173005.bak"
    assert second.name == "metascan.db.pre-v6-20261024-173005-2.bak"
    for bak in (first, second):
        assert _user_version(bak) == 5
        assert _legacy_state(bak) == _legacy_state(db_file)


def test_a_backup_cut_short_earlier_is_removed_first(tmp_path):
    """A start killed mid-backup leaves ``<backup>.partial`` (and maybe its
    journal) behind; the next backup removes them before it starts. Whole
    backups and other databases' files are never touched."""
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    stale = [
        db_dir / "metascan.db.pre-v6-20261024-173005.bak.partial",
        db_dir / "metascan.db.pre-v6-20261024-173005.bak.partial-journal",
        db_dir / "metascan.db.pre-v6-20261024-173005-2.bak.partial",
    ]
    kept = [
        db_dir / "metascan.db.pre-v6-20261023-080000.bak",  # an earlier whole one
        db_dir / "other.db.pre-v6-20261024-173005.bak.partial",
        db_dir / "notes.partial",
    ]
    for path in stale + kept:
        path.write_bytes(b"half a backup")

    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    assert [p for p in stale if p.exists()] == []
    assert all(p.read_bytes() == b"half a backup" for p in kept)
    new = [
        p
        for p in db_dir.glob("metascan.db.pre-v6-*.bak")
        if p.name != "metascan.db.pre-v6-20261023-080000.bak"
    ]
    assert len(new) == 1 and _user_version(new[0]) == 5


def test_a_backup_in_progress_elsewhere_is_never_removed(tmp_path, monkeypatch):
    """Two starts at once: a backup holds the database's write lock until
    its copy is in place, so the other start waits instead of sweeping that
    ``.partial`` away -- and gives up (still version 5) when it waits too
    long."""
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)
    writing = db_dir / "metascan.db.pre-v6-20261024-173005.bak.partial"
    writing.write_bytes(b"still being written")
    other = sqlite3.connect(str(db_file))
    other.execute("BEGIN IMMEDIATE")  # the other start, mid-backup
    try:
        with monkeypatch.context() as m:
            m.setattr(database_sqlite, "V6_BACKUP_LOCK_TIMEOUT_S", 0.05)
            with pytest.raises(MigrationError) as err:
                backup_before_v6(db_file, datetime.now(timezone.utc))
        message = str(err.value)
        assert "another Metascan" in message and "start Metascan again" in message
        assert writing.read_bytes() == b"still being written"
        assert [p.name for p in _backups(db_dir)] == [writing.name]
    finally:
        other.rollback()
        other.close()
    assert _user_version(db_file) == 5 and _legacy_state(db_file) == before

    # Once it has let go, the next start sweeps what it left and backs up.
    backup = backup_before_v6(db_file, datetime.now(timezone.utc))
    assert backup is not None and not writing.exists()
    assert [p.name for p in _backups(db_dir)] == [backup.name]


# Patches below use monkeypatch.context(): a bare monkeypatch.undo() would
# also undo conftest's isolated_app_paths and point the app at the real data.


def test_v6_refuses_without_disk_space(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)

    with monkeypatch.context() as m:
        m.setattr(database_sqlite, "_free_disk_bytes", lambda d: 1000)
        with pytest.raises(MigrationError) as err:
            DatabaseManager(db_dir)
    message = str(err.value)
    assert "free" in message and "start Metascan again" in message
    assert str(db_dir) in message
    _assert_untouched_v5(db_file, before)
    assert _backups(db_dir) == []  # not even a partial file

    # Space freed: the next start migrates.
    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    assert _grades(db_file) == EXPECTED_GRADES
    assert len(_backups(db_dir)) == 1


def test_v6_refuses_when_the_backup_fails(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)

    def broken_copy(src: sqlite3.Connection, dest: Path) -> None:
        dest.write_bytes(b"half a backup")
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as m:
        m.setattr(database_sqlite, "_copy_database", broken_copy)
        with pytest.raises(MigrationError, match="disk I/O error") as err:
            DatabaseManager(db_dir)
    assert "start Metascan again" in str(err.value)
    _assert_untouched_v5(db_file, before)
    assert _backups(db_dir) == []  # the partial copy is removed

    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6


def test_v6_failure_mid_transaction_leaves_v5(tmp_path):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    # Fails the tombstones UPDATE, which runs after the judgments UPDATE and
    # the rowless-favorite INSERT: both must be rolled back with it.
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TRIGGER fail_v6 BEFORE UPDATE ON media_tombstones "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    conn.close()
    before = _legacy_state(db_file)

    with pytest.raises(MigrationError, match="injected failure") as err:
        DatabaseManager(db_dir)
    message = str(err.value)
    assert "version 5" in message and "start Metascan again" in message
    _assert_untouched_v5(db_file, before)
    # The backup was taken before the transaction; the rollback is confirmed,
    # so the database still matches it and it is removed again: a cause that
    # repeats on every start must not pile up full copies.
    assert _backups(db_dir) == []
    assert ".bak" not in message and "the backup" not in message

    # The next start retries and migrates, with one backup.
    conn = sqlite3.connect(str(db_file))
    conn.execute("DROP TRIGGER fail_v6")
    conn.commit()
    conn.close()
    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    assert _grades(db_file) == EXPECTED_GRADES
    assert _tomb_grades(db_file) == EXPECTED_TOMB_GRADES
    [backup] = _backups(db_dir)
    assert BAK_RE.match(backup.name) and _legacy_state(backup) == before


def test_a_failure_that_repeats_never_piles_up_backups(tmp_path):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TRIGGER fail_v6 BEFORE UPDATE ON media_tombstones "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    conn.close()
    before = _legacy_state(db_file)
    for _ in range(3):
        with pytest.raises(MigrationError, match="injected failure"):
            DatabaseManager(db_dir)
        assert _backups(db_dir) == []
    _assert_untouched_v5(db_file, before)


def test_an_unconfirmed_rollback_keeps_the_backup(tmp_path, monkeypatch):
    """When the rollback itself fails, the attempt's backup is the one copy
    known to match the database as it was: it is kept and named."""
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)

    def lose_the_connection(conn: sqlite3.Connection) -> Dict[str, int]:
        conn.close()  # SQLite discards the transaction; rollback() now raises
        raise RuntimeError("counting failed")

    with monkeypatch.context() as m:
        m.setattr(DatabaseManager, "_v6_counts", staticmethod(lose_the_connection))
        with pytest.raises(MigrationError, match="counting failed") as err:
            DatabaseManager(db_dir)
    message = str(err.value)
    [backup] = _backups(db_dir)
    assert BAK_RE.match(backup.name)
    assert str(backup) in message and "keep the database and the backup" in message
    assert _legacy_state(backup) == before
    _assert_untouched_v5(db_file, before)


def test_a_backup_that_cannot_be_removed_is_named(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)

    def boom(conn: sqlite3.Connection) -> Dict[str, int]:
        raise RuntimeError("counting failed")

    real_unlink = Path.unlink

    def stuck(self: Path, missing_ok: bool = False) -> None:
        if ".pre-v6-" in self.name:
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    with monkeypatch.context() as m:
        m.setattr(DatabaseManager, "_v6_counts", staticmethod(boom))
        m.setattr(Path, "unlink", stuck)
        with pytest.raises(MigrationError, match="counting failed") as err:
            DatabaseManager(db_dir)
    [backup] = _backups(db_dir)
    assert str(backup) in str(err.value)
    _assert_untouched_v5(db_file, before)


def test_v6_failure_after_the_version_write_leaves_v5(tmp_path, monkeypatch):
    # Fails after every statement, user_version = 6 included, before the
    # commit: the version write is rolled back with the rest.
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)

    def boom(conn: sqlite3.Connection) -> Dict[str, int]:
        raise RuntimeError("counting failed")

    with monkeypatch.context() as m:
        m.setattr(DatabaseManager, "_v6_counts", staticmethod(boom))
        with pytest.raises(MigrationError, match="counting failed"):
            DatabaseManager(db_dir)
    _assert_untouched_v5(db_file, before)
    assert _backups(db_dir) == []  # rolled back: its backup is removed

    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6


# The child kills itself after every v6 statement, before the commit: no
# rollback, no cleanup -- a crash or power cut mid-migration. It points the
# app's base path at a temp dir first, as conftest does in-process.
_KILLED_CHILD = """
import os, sys
from pathlib import Path
from metascan.utils import app_paths
app_paths.get_base_path = lambda: Path(sys.argv[2])
from metascan.core.database_sqlite import DatabaseManager

def die(conn):
    os._exit(17)

DatabaseManager._v6_counts = staticmethod(die)
DatabaseManager(Path(sys.argv[1]))
"""


def test_v6_killed_mid_transaction_leaves_v5(tmp_path):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    before = _legacy_state(db_file)
    base = tmp_path / "child_base"
    base.mkdir()

    proc = subprocess.run(
        [sys.executable, "-c", _KILLED_CHILD, str(db_dir), str(base)],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 17, proc.stderr
    _assert_untouched_v5(db_file, before)

    DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    assert _grades(db_file) == EXPECTED_GRADES
    assert _tomb_grades(db_file) == EXPECTED_TOMB_GRADES


# ---------------------------------------------------------------------------
# Writes this task touches outside the migration
# ---------------------------------------------------------------------------


def test_upsert_preserves_an_existing_favorite(tmp_path):
    db = DatabaseManager(tmp_path / "db")
    # A new row keeps the caller's favorite (seeding stays valid).
    assert db.save_media(_media("/p/fav.png", favorite=True))
    assert db.save_media(_media("/p/plain.png"))
    db_file = tmp_path / "db" / "metascan.db"

    def favorites() -> Dict[str, int]:
        return dict(_query(db_file, "SELECT file_path, is_favorite FROM media"))

    assert favorites() == {"/p/fav.png": 1, "/p/plain.png": 0}
    # Re-ingesting an existing path never changes its ★, either way, on
    # either upsert path.
    assert db.save_media(_media("/p/fav.png", favorite=False))
    assert db.save_media(_media("/p/plain.png", favorite=True))
    assert favorites() == {"/p/fav.png": 1, "/p/plain.png": 0}
    assert (
        db.save_media_batch(
            [_media("/p/fav.png", favorite=False), _media("/p/plain.png", True)]
        )
        == 2
    )
    assert favorites() == {"/p/fav.png": 1, "/p/plain.png": 0}
    # The rest of the row is still updated.
    m = _media("/p/fav.png")
    m.width = 640
    db.save_media(m)
    assert _query(
        db_file, "SELECT width FROM media WHERE file_path = '/p/fav.png'"
    ) == [(640,)]


def test_tombstone_copies_the_judgment_grade(tmp_path):
    db = DatabaseManager(tmp_path / "db")
    db_file = tmp_path / "db" / "metascan.db"
    for p in ("/p/pass.png", "/p/fail.png", "/p/none.png"):
        db.save_media(_media(p))
    conn = sqlite3.connect(str(db_file))
    conn.executemany(
        "INSERT INTO media_judgments (file_path, grade, seen_at) VALUES (?, ?, ?)",
        [
            ("/p/pass.png", "pass", T_SEEN),
            ("/p/fail.png", "fail", T_SEEN),
            ("/p/none.png", None, T_SEEN),
        ],
    )
    conn.commit()
    conn.close()
    db.delete_media_batch([Path("/p/pass.png"), Path("/p/fail.png")])
    db.delete_media(Path("/p/none.png"))
    assert _tomb_grades(db_file) == {
        "/p/pass.png": "pass",
        "/p/fail.png": "fail",
        "/p/none.png": None,
    }


def test_grade_values_are_constrained(tmp_path):
    db = DatabaseManager(tmp_path / "db")
    db.save_media(_media("/p/a.png"))
    conn = sqlite3.connect(str(tmp_path / "db" / "metascan.db"))
    try:
        # "passed" / "favorite" are the old names: never a grade.
        for bad in ("passed", "favorite", "PASS", ""):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO media_judgments (file_path, grade) VALUES (?, ?)",
                    ("/p/a.png", bad),
                )
    finally:
        conn.close()


def test_grade_constants():
    from metascan.core import judgments

    assert judgments.GRADES == ("pass", "fail")
    assert (
        judgments.EVENT_GRADE_PASS,
        judgments.EVENT_GRADE_FAIL,
        judgments.EVENT_GRADE_CLEAR,
    ) == ("grade_pass", "grade_fail", "grade_clear")
    assert judgments.LEGACY_REJECT_EVENTS == ("pass", "unpass")
    # The new event names never collide with the legacy reject events.
    assert not set(judgments.LEGACY_REJECT_EVENTS) & {
        judgments.EVENT_GRADE_PASS,
        judgments.EVENT_GRADE_FAIL,
        judgments.EVENT_GRADE_CLEAR,
    }


# ---------------------------------------------------------------------------
# Refuse to start
# ---------------------------------------------------------------------------


def test_lifespan_stops_on_migration_error(monkeypatch, caplog):
    # Imported here: other test modules import this one's v5 fixture, and
    # fastapi.testclient warns (httpx deprecation) on import.
    from fastapi.testclient import TestClient

    from backend import dependencies
    from backend.main import create_app
    from metascan.utils.app_paths import get_data_dir

    data_dir = get_data_dir()  # the isolated temp data dir (conftest)
    db_file = _build_v5_db(data_dir)
    before = _legacy_state(db_file)
    monkeypatch.setattr(database_sqlite, "_free_disk_bytes", lambda d: 1000)
    caplog.set_level(logging.INFO)

    with pytest.raises(MigrationError) as err:
        with TestClient(create_app()):
            pytest.fail("the server must not start on a failed migration")
    message = str(err.value)
    assert "start Metascan again" in message
    # Logged as-is, so the owner reads it in the console / server log.
    assert [
        r
        for r in caplog.records
        if r.levelno == logging.CRITICAL and message in r.getMessage()
    ]
    # Nothing was opened half-way: no singleton, database still v5.
    assert dependencies._db_singleton is None
    _assert_untouched_v5(db_file, before)
