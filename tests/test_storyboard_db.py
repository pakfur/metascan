"""Tests for the Phase B storyboard tables + CRUD on DatabaseManager."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
import sqlite3

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


def _build_tree(db):
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=42
    )
    su = db.create_subject(
        sb, name="MAYA", description="late 20s, shaved head, red scarf"
    )
    sc = db.create_scene(sb, name="Salvage Yard", sort_order=0, location="salvage yard")
    pa = db.create_panel(
        sc,
        action="hand rests on hull seam",
        sort_order=0,
        shot_size="ECU",
        subject_ids=[su],
    )
    return sb, su, sc, pa


def test_create_and_get_tree(db):
    sb, su, sc, pa = _build_tree(db)
    tree = db.get_storyboard_tree(sb)
    assert tree["name"] == "Yard"
    assert tree["subjects"][0]["name"] == "MAYA"
    assert tree["scenes"][0]["panels"][0]["subject_ids"] == [su]
    assert tree["scenes"][0]["panels"][0]["images"] == []


def test_get_storyboard_missing_returns_none(db):
    assert db.get_storyboard(999) is None
    assert db.get_storyboard_tree(999) is None


def test_list_storyboards(db):
    db.create_storyboard(name="A", target_model="sd", architecture="t2i", base_seed=1)
    db.create_storyboard(name="B", target_model="sd", architecture="t2i", base_seed=2)
    rows = db.list_storyboards()
    assert [r["name"] for r in rows] == ["A", "B"]


def test_update_storyboard(db):
    sb, *_ = _build_tree(db)
    db.update_storyboard(sb, name="Renamed", base_seed=99)
    row = db.get_storyboard(sb)
    assert row["name"] == "Renamed"
    assert row["base_seed"] == 99


def test_update_storyboard_rejects_unknown(db):
    sb, *_ = _build_tree(db)
    with pytest.raises(ValueError):
        db.update_storyboard(sb, bogus="x")


def test_delete_storyboard_cascades(db):
    sb, su, sc, pa = _build_tree(db)
    assert db.delete_storyboard(sb)[0] is True
    with db.lock, db._get_connection() as conn:
        for table in ("storyboard_subjects", "scenes", "panels"):
            assert (
                conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
            )


def test_delete_storyboard_missing_returns_false(db):
    assert db.delete_storyboard(999)[0] is False


def test_create_subject_converts_reference_path_to_posix(db):
    """storyboard_subjects.reference_path FKs media(file_path), which is
    always stored POSIX -- a native-style path must be converted before
    the INSERT or it can never match an existing media row."""
    sb, *_ = _build_tree(db)
    db.save_media(_media("/mnt/c/pics/ref.png"))
    su2 = db.create_subject(
        sb, name="X", description="d", reference_path="C:\\pics\\ref.png"
    )
    tree = db.get_storyboard_tree(sb)
    subj = next(s for s in tree["subjects"] if s["id"] == su2)
    assert subj["reference_path"] == "/mnt/c/pics/ref.png"


def test_update_subject_converts_reference_path_to_posix(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/mnt/c/pics/ref2.png"))
    db.update_subject(su, reference_path="C:\\pics\\ref2.png")
    tree = db.get_storyboard_tree(sb)
    assert tree["subjects"][0]["reference_path"] == "/mnt/c/pics/ref2.png"


def test_create_subject_unknown_reference_path_raises_integrity_error(db):
    sb, *_ = _build_tree(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_subject(sb, name="X", description="d", reference_path="/nope.png")


def test_update_subject_unknown_reference_path_raises_integrity_error(db):
    sb, su, sc, pa = _build_tree(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.update_subject(su, reference_path="/nope.png")


def test_update_subject_and_delete(db):
    sb, su, sc, pa = _build_tree(db)
    db.update_subject(su, description="updated desc")
    tree = db.get_storyboard_tree(sb)
    assert tree["subjects"][0]["description"] == "updated desc"
    assert db.delete_subject(su) is True
    assert db.get_storyboard_tree(sb)["subjects"] == []


def test_update_scene_and_delete(db):
    sb, su, sc, pa = _build_tree(db)
    db.update_scene(sc, mood="tense")
    tree = db.get_storyboard_tree(sb)
    assert tree["scenes"][0]["mood"] == "tense"
    # deleting the scene should cascade the panel too
    assert db.delete_scene(sc)[0] is True
    tree = db.get_storyboard_tree(sb)
    assert tree["scenes"] == []


def test_delete_media_cascades_panel_image_and_nulls_selection(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    img_id = db.create_panel_image(pa, file_path="/pics/a.png", seed=1)
    assert db.select_panel_image(pa, img_id) is True
    panel = db.get_panel(pa)
    assert panel["selected_image_id"] == img_id

    assert db.delete_media(Path("/pics/a.png")) is True

    with db.lock, db._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM panel_images WHERE id = ?", (img_id,)
        ).fetchone()["n"]
        assert n == 0

    panel = db.get_panel(pa)
    assert panel is not None
    assert panel["selected_image_id"] is None
    assert panel["action"] == "hand rests on hull seam"


def test_select_panel_image_swaps_hidden(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.set_media_hidden("/pics/b.png", True)
    img_a = db.create_panel_image(pa, file_path="/pics/a.png", seed=1)
    img_b = db.create_panel_image(pa, file_path="/pics/b.png", seed=2, variant_index=1)

    assert db.select_panel_image(pa, img_a) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is False
    assert rows["/pics/b.png"] is True

    assert db.select_panel_image(pa, img_b) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is True
    assert rows["/pics/b.png"] is False

    assert db.select_panel_image(pa, None) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is True
    assert rows["/pics/b.png"] is True
    panel = db.get_panel(pa)
    assert panel["selected_image_id"] is None


def test_select_panel_image_missing_panel_returns_false(db):
    assert db.select_panel_image(999, None) is False


def test_select_panel_image_missing_image_returns_false(db):
    sb, su, sc, pa = _build_tree(db)
    assert db.select_panel_image(pa, 999) is False


def test_replace_structure_is_destructive_and_resolves_names(db):
    sb, su, sc, pa = _build_tree(db)
    parsed = {
        "subjects": [{"name": "MAYA", "description": "d1"}],
        "scenes": [
            {
                "name": "S1",
                "location": None,
                "time_of_day": None,
                "mood": None,
                "lighting": None,
                "panels": [
                    {
                        "action": "a1",
                        "shot_size": "CU",
                        "angle": None,
                        "lens": None,
                        "subjects": ["maya", "GHOST"],
                    }
                ],
            }
        ],
    }
    db.replace_storyboard_structure(sb, parsed)
    tree = db.get_storyboard_tree(sb)
    assert len(tree["subjects"]) == 1
    maya_id = tree["subjects"][0]["id"]
    assert maya_id != su  # old subject rows were destroyed and replaced
    assert len(tree["scenes"]) == 1
    assert tree["scenes"][0]["name"] == "S1"
    assert tree["scenes"][0]["panels"][0]["subject_ids"] == [maya_id]

    # old scene/panel rows are gone
    with db.lock, db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM scenes").fetchone()["n"]
        assert n == 1
        n = conn.execute("SELECT COUNT(*) AS n FROM panels").fetchone()["n"]
        assert n == 1


def test_delete_panel_removes_generation_jobs(db):
    sb, su, sc, pa = _build_tree(db)
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)
    assert db.delete_panel(pa)[0] is True
    assert db.get_generation_job(jid) is None


def test_delete_panel_missing_returns_false(db):
    assert db.delete_panel(999)[0] is False


def _hidden_by_path(db) -> dict:
    return {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }


def test_delete_panel_unhides_media_and_purges_jobs(db):
    """Every ingested storyboard variant is hidden=1; select_panel_image is
    the only unhide path and needs a live panel. delete_panel destroys the
    panel (cascading panel_images) -- it must unhide the affected media
    first, or those files are hidden forever. It must also purge
    generation_jobs for the panel so a restart can't re-adopt a job for a
    panel that no longer exists."""
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_panel(pa)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_delete_scene_unhides_media_and_purges_jobs(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_scene(sc)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_delete_storyboard_unhides_media_and_purges_jobs(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_storyboard(sb)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_replace_structure_unhides_media_and_purges_jobs(db):
    """A re-parse (replace_storyboard_structure) destroys the old
    scene/panel tree exactly like a delete does -- same requirement."""
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    db.replace_storyboard_structure(sb, {"subjects": [], "scenes": []})

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_update_panel_whitelist_rejects_unknown(db):
    sb, su, sc, pa = _build_tree(db)
    with pytest.raises(ValueError):
        db.update_panel(pa, bogus="x")


def test_update_panel_encodes_subject_ids(db):
    sb, su, sc, pa = _build_tree(db)
    su2 = db.create_subject(sb, name="GHOST", description="d")
    db.update_panel(pa, subject_ids=[su2, su], prompt="a prompt", prompt_locked=True)
    panel = db.get_panel(pa)
    assert panel["subject_ids"] == [su2, su]
    assert panel["prompt"] == "a prompt"
    assert bool(panel["prompt_locked"]) is True


def test_get_panel_missing_returns_none(db):
    assert db.get_panel(999) is None


def test_create_panel_image_missing_media_raises_integrity_error(db):
    sb, su, sc, pa = _build_tree(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_panel_image(pa, file_path="/pics/does-not-exist.png")


def test_list_and_count_panel_images(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.create_panel_image(pa, file_path="/pics/a.png", variant_index=1)
    db.create_panel_image(pa, file_path="/pics/b.png", variant_index=0)
    assert db.count_panel_images(pa) == 2
    images = db.list_panel_images(pa)
    assert [i["file_path"] for i in images] == ["/pics/b.png", "/pics/a.png"]


def test_list_generation_jobs_filters_by_panel_ids(db):
    sb, su, sc, pa = _build_tree(db)
    pa2 = db.create_panel(sc, action="second panel")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    j1 = db.create_generation_job(pid, "{}", panel_id=pa)
    j2 = db.create_generation_job(pid, "{}", panel_id=pa2)
    db.create_generation_job(pid, "{}")  # no panel_id

    rows = db.list_generation_jobs(panel_ids=[pa])
    assert [r["id"] for r in rows] == [j1]

    rows = db.list_generation_jobs(panel_ids=[pa, pa2])
    assert sorted(r["id"] for r in rows) == sorted([j1, j2])


def test_latest_jobs_for_panels(db):
    sb, su, sc, pa = _build_tree(db)
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    j1 = db.create_generation_job(pid, "{}", panel_id=pa)
    j2 = db.create_generation_job(pid, "{}", panel_id=pa)
    result = db.latest_jobs_for_panels([pa])
    assert set(result.keys()) == {pa}
    assert result[pa]["id"] == j2
    assert j2 > j1


def test_latest_jobs_for_panels_empty_list(db):
    assert db.latest_jobs_for_panels([]) == {}


def test_get_storyboard_tree_and_list_panel_images_convert_paths(db, monkeypatch):
    """panel_images.file_path is stored POSIX; get_storyboard_tree and
    list_panel_images must return it through to_native_path, mirroring
    get_folder's precedent -- GET /api/storyboard/{id} and GET /api/media
    have to agree on path shape. Patch to_native_path with a
    distinguishable transform so the assertion can't pass merely because
    POSIX-in/POSIX-out looks like a no-op on a Linux test host."""
    calls = []

    def fake_to_native(p):
        calls.append(p)
        return f"NATIVE::{p}"

    monkeypatch.setattr("metascan.core.database_sqlite.to_native_path", fake_to_native)

    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.create_panel_image(pa, file_path="/pics/a.png")

    tree = db.get_storyboard_tree(sb)
    img = tree["scenes"][0]["panels"][0]["images"][0]
    assert img["file_path"] == "NATIVE::/pics/a.png"

    images = db.list_panel_images(pa)
    assert images[0]["file_path"] == "NATIVE::/pics/a.png"
    assert "/pics/a.png" in calls


def test_storyboard_id_for_panel(db):
    sb, su, sc, pa = _build_tree(db)
    assert db.storyboard_id_for_panel(pa) == sb


def test_storyboard_id_for_panel_missing_returns_none(db):
    assert db.storyboard_id_for_panel(999) is None


# ---- folder_id type migration -------------------------------------------


def test_storyboards_folder_id_column_migrates_int_to_text(tmp_path):
    """storyboards.folder_id must be TEXT -- folders.id is a uuid4 string,
    and a numeric-looking uuid stored against an INTEGER column would
    silently coerce and corrupt add_folder_items lookups. A dev DB created
    from an earlier commit on this branch would still have the old
    INTEGER column; DatabaseManager must detect and rebuild it (preserving
    data) rather than requiring a fresh DB."""
    db_dir = tmp_path / "migrate_db"
    db_dir.mkdir()
    db_file = db_dir / "metascan.db"

    raw = sqlite3.connect(str(db_file))
    try:
        raw.execute(
            """
            CREATE TABLE storyboards (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                source_text   TEXT,
                aspect_ratio  TEXT NOT NULL DEFAULT '16:9',
                style_block   TEXT,
                negative      TEXT,
                target_model  TEXT NOT NULL,
                architecture  TEXT NOT NULL,
                preset_id     INTEGER,
                base_seed     INTEGER NOT NULL,
                batch_size    INTEGER NOT NULL DEFAULT 4,
                folder_id     INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        raw.execute(
            "INSERT INTO storyboards (id, name, target_model, architecture, "
            "base_seed) VALUES (1, 'Old', 'sd', 't2i', 42)"
        )
        raw.commit()
    finally:
        raw.close()

    mgr = DatabaseManager(db_dir)
    try:
        with mgr.lock, mgr._get_connection() as conn:
            ddl_row = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='storyboards'"
            ).fetchone()
        ddl = ddl_row["sql"]
        assert re.search(r"folder_id\s+TEXT", ddl)
        assert not re.search(r"folder_id\s+INTEGER", ddl)

        # Pre-existing data survived the rebuild.
        row = mgr.get_storyboard(1)
        assert row is not None
        assert row["name"] == "Old"
        assert row["base_seed"] == 42

        # And the table is fully functional post-migration.
        folder = mgr.create_folder(
            "11111111-1111-1111-1111-111111111111", "manual", "F"
        )
        mgr.update_storyboard(1, folder_id=folder["id"])
        assert mgr.get_storyboard(1)["folder_id"] == folder["id"]
    finally:
        mgr.close()


# ---- purge_images + folder removal ----------------------------------------


def test_delete_panel_purge_images_deletes_media_rows(db):
    """purge_images=True deletes the media rows instead of unhiding them
    and returns the file paths so the service can remove the files."""
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    ok, purged = db.delete_panel(pa, purge_images=True)

    assert ok is True
    assert purged == ["/pics/a.png"]
    assert "/pics/a.png" not in _hidden_by_path(db)
    assert db.get_generation_job(jid) is None


def test_delete_storyboard_purge_images_deletes_media_rows(db):
    sb, su, sc, pa = _build_tree(db)
    for name in ("a", "b"):
        db.save_media(_media(f"/pics/{name}.png"))
        db.set_media_hidden(f"/pics/{name}.png", True)
        db.create_panel_image(pa, file_path=f"/pics/{name}.png")

    ok, purged, _folder = db.delete_storyboard(sb, purge_images=True)

    assert ok is True
    assert sorted(purged) == ["/pics/a.png", "/pics/b.png"]
    assert _hidden_by_path(db) == {}


def test_delete_scene_purge_images_deletes_media_rows(db):
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_panel_image(pa, file_path="/pics/a.png")

    ok, purged = db.delete_scene(sc, purge_images=True)

    assert ok is True
    assert purged == ["/pics/a.png"]
    assert "/pics/a.png" not in _hidden_by_path(db)


def test_purge_spares_media_referenced_by_another_panel(db):
    """A file shared with a surviving panel's panel_images must not be
    deleted out from under it (FK) -- it is unhidden instead, and its
    path is NOT returned for filesystem removal."""
    sb, su, sc, pa = _build_tree(db)
    pa2 = db.create_panel(sc, action="second", sort_order=1, subject_ids=[su])
    db.save_media(_media("/pics/shared.png"))
    db.set_media_hidden("/pics/shared.png", True)
    db.create_panel_image(pa, file_path="/pics/shared.png")
    db.create_panel_image(pa2, file_path="/pics/shared.png")

    ok, purged = db.delete_panel(pa, purge_images=True)

    assert ok is True
    assert purged == []
    assert _hidden_by_path(db)["/pics/shared.png"] is False


def test_purge_spares_media_used_as_subject_reference(db):
    """A generated image picked as a subject reference (possibly in a
    different storyboard) survives a purge -- deleting its media row
    would break storyboard_subjects.reference_path's FK."""
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/ref.png"))
    db.set_media_hidden("/pics/ref.png", True)
    db.create_panel_image(pa, file_path="/pics/ref.png")
    sb2 = db.create_storyboard(
        name="Other", target_model="sd", architecture="t2i", base_seed=1
    )
    db.create_subject(sb2, name="X", description="d", reference_path="/pics/ref.png")

    ok, purged, _folder = db.delete_storyboard(sb, purge_images=True)

    assert ok is True
    assert purged == []
    assert _hidden_by_path(db)["/pics/ref.png"] is False


def test_delete_storyboard_removes_its_folder(db):
    """The 'Storyboard: <name>' folder the runner created goes with the
    storyboard (folder_items cascade), purge or not. The deleted folder id
    is returned so the route can broadcast folder_deleted."""
    sb, su, sc, pa = _build_tree(db)
    db.save_media(_media("/pics/a.png"))
    db.create_panel_image(pa, file_path="/pics/a.png")
    folder = db.create_folder(
        "22222222-2222-2222-2222-222222222222", "manual", "Storyboard: Yard"
    )
    db.update_storyboard(sb, folder_id=folder["id"])
    db.add_folder_items(folder["id"], ["/pics/a.png"])

    ok, _purged, deleted_folder = db.delete_storyboard(sb)

    assert ok is True
    assert deleted_folder == folder["id"]
    assert db.get_folder(folder["id"]) is None
    with db.lock, db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM folder_items").fetchone()["n"]
        assert n == 0


def test_delete_storyboard_without_folder_returns_none_folder(db):
    sb, *_ = _build_tree(db)
    ok, _purged, deleted_folder = db.delete_storyboard(sb)
    assert ok is True
    assert deleted_folder is None


def test_v3_schema_shapes(db):
    """Panels are thin containers; beats carry framing/prompt/keeper."""
    with db._get_connection() as conn:
        panel_cols = {r["name"] for r in conn.execute("PRAGMA table_info(panels)")}
        beat_cols = {r["name"] for r in conn.execute("PRAGMA table_info(beats)")}
        sb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(storyboards)")}
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    for gone in (
        "shot_size",
        "angle",
        "lens",
        "notes",
        "negative",
        "brief",
        "prompt",
        "prompt_locked",
        "prompt_source",
        "subject_ids",
        "selected_image_id",
    ):
        assert gone not in panel_cols, gone
    for kept in (
        "action",
        "duration_s",
        "video_prompt",
        "video_anchor",
        "video_compiled_anchor",
    ):
        assert kept in panel_cols, kept
    for added in (
        "shot_size",
        "angle",
        "lens",
        "subject_ids",
        "brief",
        "prompt",
        "prompt_locked",
        "prompt_source",
        "selected_image_id",
    ):
        assert added in beat_cols, added
    assert "notes" in sb_cols
    assert "beat_images" in tables
    assert "panel_images" not in tables


def test_v3_migration_from_v2_layout(tmp_path):
    """A dev DB with the old panel-centric layout is dropped and rebuilt:
    hidden media released, panel-scoped jobs purged."""
    db_dir = tmp_path / "olddb"
    db_dir.mkdir()
    db_path = db_dir / "metascan.db"
    conn = sqlite3.connect(db_path)
    # The media table needs the full set of columns later migration steps
    # (unrelated to this v3 gate) unconditionally reference -- this
    # represents a dev DB that's already fully migrated on the media side,
    # just not yet on the Phase-B panel/beat tables.
    conn.executescript(
        """
        CREATE TABLE media (
            file_path TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '{}',
            is_favorite INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            playback_speed REAL,
            width INTEGER,
            height INTEGER,
            file_size INTEGER,
            frame_rate REAL,
            duration REAL,
            modified_at TEXT,
            camera_make TEXT,
            camera_model TEXT,
            lens_model TEXT,
            datetime_original TEXT,
            gps_latitude REAL,
            gps_longitude REAL,
            gps_altitude REAL,
            orientation INTEGER,
            photo_exposure TEXT,
            hidden INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE panels (id INTEGER PRIMARY KEY, prompt TEXT);
        CREATE TABLE beats (id INTEGER PRIMARY KEY, panel_id INTEGER);
        CREATE TABLE panel_images (id INTEGER PRIMARY KEY, panel_id INTEGER,
            file_path TEXT);
        CREATE TABLE generation_jobs (
            id INTEGER PRIMARY KEY,
            panel_id INTEGER,
            state TEXT,
            comfy_prompt_id TEXT
        );
        INSERT INTO media (file_path, data, hidden) VALUES ('a/x.png', '{}', 1);
        INSERT INTO panel_images VALUES (1, 1, 'a/x.png');
        INSERT INTO generation_jobs (id, panel_id) VALUES (7, 1);
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()

    db = DatabaseManager(db_dir)
    try:
        with db._get_connection() as c:
            assert c.execute("PRAGMA user_version").fetchone()[0] >= 3
            assert (
                c.execute(
                    "SELECT hidden FROM media WHERE file_path='a/x.png'"
                ).fetchone()[0]
                == 0
            )
            assert (
                c.execute(
                    "SELECT COUNT(*) FROM generation_jobs WHERE panel_id IS NOT NULL"
                ).fetchone()[0]
                == 0
            )
    finally:
        db.close()
