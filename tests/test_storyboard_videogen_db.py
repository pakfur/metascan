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
