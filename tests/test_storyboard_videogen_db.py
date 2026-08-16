"""V4 schema: ref2v preset kind + video-generation columns."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def test_ref2v_preset_kind_accepted(db):
    pid = db.create_workflow_preset("h3", "ref2v", "{}", "{}")
    assert db.get_workflow_preset(pid)["kind"] == "ref2v"


def test_bad_kind_still_rejected(db):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.create_workflow_preset("x", "bogus", "{}", "{}")


def test_check_rebuild_preserves_existing_presets(tmp_path):
    # Simulate an OLD database: create it with the pre-V4 CHECK, insert a
    # row, close, then reopen through DatabaseManager (which must rebuild).
    import sqlite3

    dbdir = tmp_path / "db"
    dbdir.mkdir()
    dbfile = dbdir / "metascan.db"  # match DatabaseManager's actual filename
    # BINDING DIRECTIVE: open tests/test_storyboard_db.py or DatabaseManager
    # to confirm the on-disk filename/layout the fixture produces, and
    # mirror it here so DatabaseManager(dbdir) reopens THIS file.
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "CREATE TABLE workflow_presets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
        "kind TEXT NOT NULL CHECK(kind IN ('t2i','ref')), "
        "workflow_json TEXT NOT NULL, bindings TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
        "VALUES ('old', 't2i', '{}', '{}')"
    )
    conn.commit()
    conn.close()
    mgr = DatabaseManager(dbdir)
    try:
        rows = mgr.list_workflow_presets()
        assert any(r["name"] == "old" and r["kind"] == "t2i" for r in rows)
        pid = mgr.create_workflow_preset("new", "ref2v", "{}", "{}")
        assert mgr.get_workflow_preset(pid)["kind"] == "ref2v"
    finally:
        mgr.close()


def test_check_rebuild_preserves_generation_jobs_fk(tmp_path):
    # Same OLD-database simulation as test_check_rebuild_preserves_existing_
    # presets, but also seeds a generation_jobs row whose preset_id FKs the
    # about-to-be-rebuilt workflow_presets table -- the create/copy/drop/
    # rename must not orphan or renumber referencing rows in *other*
    # tables. Mirrors the real generation_jobs DDL (database_sqlite.py,
    # near "CREATE TABLE IF NOT EXISTS generation_jobs") closely enough to
    # be an honest FK test: same preset_id FK, same NOT NULL columns.
    import sqlite3

    dbdir = tmp_path / "db"
    dbdir.mkdir()
    dbfile = dbdir / "metascan.db"
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "CREATE TABLE workflow_presets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
        "kind TEXT NOT NULL CHECK(kind IN ('t2i','ref')), "
        "workflow_json TEXT NOT NULL, bindings TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "CREATE TABLE generation_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "preset_id INTEGER NOT NULL REFERENCES workflow_presets(id), "
        "panel_id INTEGER, "
        "state TEXT NOT NULL DEFAULT 'queued' "
        "CHECK(state IN ('queued','running','done','failed','cancelled')), "
        "comfy_prompt_id TEXT, "
        "params TEXT NOT NULL, "
        "error TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "started_at TEXT, "
        "finished_at TEXT)"
    )
    cur = conn.execute(
        "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
        "VALUES ('old', 't2i', '{}', '{}')"
    )
    old_preset_id = cur.lastrowid
    conn.execute(
        "INSERT INTO generation_jobs (preset_id, params) VALUES (?, '{}')",
        (old_preset_id,),
    )
    conn.commit()
    conn.close()

    mgr = DatabaseManager(dbdir)
    try:
        job = mgr.list_generation_jobs(limit=10)[0]
        assert job["preset_id"] == old_preset_id
        assert mgr.get_workflow_preset(old_preset_id)["name"] == "old"
    finally:
        mgr.close()

    check_conn = sqlite3.connect(dbfile)
    try:
        check_conn.execute("PRAGMA foreign_keys = ON")
        violations = check_conn.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []
    finally:
        check_conn.close()


def test_video_columns_roundtrip(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S")
    panel = db.create_panel(scene, action="a")
    pid = db.create_workflow_preset("h3", "ref2v", "{}", "{}")
    db.update_storyboard(sb, video_preset_id=pid)
    db.update_panel(panel, video_anchor="keeper", video_compiled_anchor="keeper")
    subj = db.create_subject(
        sb, name="M", description="d", voice_ref_path="/audio/maya.wav"
    )
    assert db.get_storyboard(sb)["video_preset_id"] == pid
    got = db.get_panel(panel)
    assert got["video_anchor"] == "keeper"
    assert got["video_compiled_anchor"] == "keeper"
    assert db.get_subject(subj)["voice_ref_path"] == "/audio/maya.wav"
