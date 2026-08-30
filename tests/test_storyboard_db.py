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
    )
    return sb, su, sc, pa


def test_create_and_get_tree(db):
    sb, su, sc, pa = _build_tree(db)
    # shot_size/subject_ids/images now live on the beat, not the panel.
    db.create_beat(
        pa, action="hand rests on hull seam", shot_size="ECU", subject_ids=[su]
    )
    tree = db.get_storyboard_tree(sb)
    assert tree["name"] == "Yard"
    assert tree["subjects"][0]["name"] == "MAYA"
    beat = tree["scenes"][0]["panels"][0]["beats"][0]
    assert beat["subject_ids"] == [su]
    assert beat["shot_size"] == "ECU"
    assert beat["images"] == []


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
    assert db.delete_subject(su) == (True, [])
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


def test_delete_media_cascades_beat_image_and_nulls_selection(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    img_id = db.create_beat_image(beat_id, file_path="/pics/a.png", seed=1)
    assert db.select_beat_image(beat_id, img_id) is True
    beat = db.get_beat(beat_id)
    assert beat["selected_image_id"] == img_id

    assert db.delete_media(Path("/pics/a.png")) is True

    with db.lock, db._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM beat_images WHERE id = ?", (img_id,)
        ).fetchone()["n"]
        assert n == 0

    beat = db.get_beat(beat_id)
    assert beat is not None
    assert beat["selected_image_id"] is None
    panel = db.get_panel(pa)
    assert panel["action"] == "hand rests on hull seam"


def test_select_beat_image_swaps_hidden(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.set_media_hidden("/pics/b.png", True)
    img_a = db.create_beat_image(beat_id, file_path="/pics/a.png", seed=1)
    img_b = db.create_beat_image(
        beat_id, file_path="/pics/b.png", seed=2, variant_index=1
    )

    assert db.select_beat_image(beat_id, img_a) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is False
    assert rows["/pics/b.png"] is True

    assert db.select_beat_image(beat_id, img_b) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is True
    assert rows["/pics/b.png"] is False

    assert db.select_beat_image(beat_id, None) is True
    rows = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/pics/a.png"] is True
    assert rows["/pics/b.png"] is True
    beat = db.get_beat(beat_id)
    assert beat["selected_image_id"] is None


def test_select_beat_image_missing_beat_returns_false(db):
    assert db.select_beat_image(999, None) is False


def test_select_beat_image_missing_image_returns_false(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    assert db.select_beat_image(beat_id, 999) is False


def test_replace_structure_is_destructive_and_resolves_names(db):
    """Subject-name resolution now only applies to subjects/scenes -- panels
    carry just ``action``; per-panel framing/subject_ids live on beats
    (populated later by the compose stages, not by replace_storyboard_
    structure), so the parsed panel's ``shot_size``/``subjects`` fields are
    simply ignored here rather than resolved."""
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
    assert tree["scenes"][0]["panels"][0]["action"] == "a1"

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
    """Every ingested storyboard variant is hidden=1; select_beat_image is
    the only unhide path and needs a live beat. delete_panel destroys the
    panel (cascading beats -> beat_images) -- it must unhide the affected
    media first, or those files are hidden forever. It must also purge
    generation_jobs for the panel so a restart can't re-adopt a job for a
    panel that no longer exists."""
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_panel(pa)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_delete_scene_unhides_media_and_purges_jobs(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_scene(sc)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_delete_storyboard_unhides_media_and_purges_jobs(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    assert db.delete_storyboard(sb)[0] is True

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_replace_structure_unhides_media_and_purges_jobs(db):
    """A re-parse (replace_storyboard_structure) destroys the old
    scene/panel/beat tree exactly like a delete does -- same requirement."""
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    db.replace_storyboard_structure(sb, {"subjects": [], "scenes": []})

    assert _hidden_by_path(db)["/pics/a.png"] is False
    assert db.get_generation_job(jid) is None


def test_update_panel_whitelist_rejects_unknown(db):
    sb, su, sc, pa = _build_tree(db)
    with pytest.raises(ValueError):
        db.update_panel(pa, bogus="x")


def test_update_beat_encodes_subject_ids(db):
    """subject_ids/prompt/prompt_locked now live on the beat, not the
    panel -- see _BEAT_UPDATABLE."""
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    su2 = db.create_subject(sb, name="GHOST", description="d")
    db.update_beat(
        beat_id, subject_ids=[su2, su], prompt="a prompt", prompt_locked=True
    )
    beat = db.get_beat(beat_id)
    assert beat["subject_ids"] == [su2, su]
    assert beat["prompt"] == "a prompt"
    assert bool(beat["prompt_locked"]) is True


def test_get_panel_missing_returns_none(db):
    assert db.get_panel(999) is None


def test_create_beat_image_missing_media_raises_integrity_error(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_beat_image(beat_id, file_path="/pics/does-not-exist.png")


def test_list_and_count_beat_images(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.create_beat_image(beat_id, file_path="/pics/a.png", variant_index=1)
    db.create_beat_image(beat_id, file_path="/pics/b.png", variant_index=0)
    assert db.count_beat_images(beat_id) == 2
    images = db.list_beat_images(beat_id)
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


def test_latest_jobs_for_beats(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    j1 = db.create_generation_job(pid, "{}", panel_id=pa, beat_id=beat_id)
    j2 = db.create_generation_job(pid, "{}", panel_id=pa, beat_id=beat_id)
    result = db.latest_jobs_for_beats([beat_id])
    assert set(result.keys()) == {beat_id}
    assert result[beat_id]["id"] == j2
    assert j2 > j1


def test_latest_jobs_for_beats_empty_list(db):
    assert db.latest_jobs_for_beats([]) == {}


def test_get_storyboard_tree_and_list_beat_images_convert_paths(db, monkeypatch):
    """beat_images.file_path is stored POSIX; get_storyboard_tree and
    list_beat_images must return it through to_native_path, mirroring
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
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.create_beat_image(beat_id, file_path="/pics/a.png")

    tree = db.get_storyboard_tree(sb)
    img = tree["scenes"][0]["panels"][0]["beats"][0]["images"][0]
    assert img["file_path"] == "NATIVE::/pics/a.png"

    images = db.list_beat_images(beat_id)
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
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=pa)

    ok, purged = db.delete_panel(pa, purge_images=True)

    assert ok is True
    assert purged == ["/pics/a.png"]
    assert "/pics/a.png" not in _hidden_by_path(db)
    assert db.get_generation_job(jid) is None


def test_delete_storyboard_purge_images_deletes_media_rows(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    for name in ("a", "b"):
        db.save_media(_media(f"/pics/{name}.png"))
        db.set_media_hidden(f"/pics/{name}.png", True)
        db.create_beat_image(beat_id, file_path=f"/pics/{name}.png")

    ok, purged, _folder = db.delete_storyboard(sb, purge_images=True)

    assert ok is True
    assert sorted(purged) == ["/pics/a.png", "/pics/b.png"]
    assert _hidden_by_path(db) == {}


def test_delete_scene_purge_images_deletes_media_rows(db):
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.create_beat_image(beat_id, file_path="/pics/a.png")

    ok, purged = db.delete_scene(sc, purge_images=True)

    assert ok is True
    assert purged == ["/pics/a.png"]
    assert "/pics/a.png" not in _hidden_by_path(db)


def test_purge_spares_media_referenced_by_another_panel(db):
    """A file shared with a surviving panel's (beat's) beat_images must
    not be deleted out from under it (FK) -- it is unhidden instead, and
    its path is NOT returned for filesystem removal."""
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    pa2 = db.create_panel(sc, action="second", sort_order=1)
    beat2_id = db.create_beat(pa2, action="second beat", subject_ids=[su])
    db.save_media(_media("/pics/shared.png"))
    db.set_media_hidden("/pics/shared.png", True)
    db.create_beat_image(beat_id, file_path="/pics/shared.png")
    db.create_beat_image(beat2_id, file_path="/pics/shared.png")

    ok, purged = db.delete_panel(pa, purge_images=True)

    assert ok is True
    assert purged == []
    assert _hidden_by_path(db)["/pics/shared.png"] is False


def test_purge_spares_media_used_as_subject_reference(db):
    """A generated image picked as a subject reference (possibly in a
    different storyboard) survives a purge -- deleting its media row
    would break storyboard_subjects.reference_path's FK."""
    sb, su, sc, pa = _build_tree(db)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/ref.png"))
    db.set_media_hidden("/pics/ref.png", True)
    db.create_beat_image(beat_id, file_path="/pics/ref.png")
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
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/pics/a.png"))
    db.create_beat_image(beat_id, file_path="/pics/a.png")
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


def test_cinematic_columns_roundtrip(db):
    sb = db.create_storyboard(
        name="C", target_model="sd", architecture="t2i", base_seed=1
    )
    assert db.get_storyboard(sb)["pacing"] == "standard"
    db.update_storyboard(sb, pacing="propulsive")
    assert db.get_storyboard(sb)["pacing"] == "propulsive"

    sids, _ = db.replace_storyboard_scenes(
        sb,
        [
            {
                "name": "S1",
                "arc_beats": ["setup", "turn"],
                "charge_in": 0,
                "charge_out": -4,
            }
        ],
    )
    tree = db.get_storyboard_tree(sb)
    scene = tree["scenes"][0]
    assert scene["arc_beats"] == ["setup", "turn"]
    assert scene["charge_in"] == 0 and scene["charge_out"] == -4
    db.update_scene(sids[0], arc_beats=["setup"], charge_in=1)
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["arc_beats"] == ["setup"] and scene["charge_in"] == 1

    pids, _ = db.replace_scene_panels(
        sids[0],
        [{"action": "a", "duration_s": 10.0, "is_turn": 1, "subtext": "hidden"}],
    )
    panel = db.get_panel(pids[0])
    assert panel["is_turn"] == 1 and panel["subtext"] == "hidden"
    db.update_panel(pids[0], is_turn=0, subtext=None)
    panel = db.get_panel(pids[0])
    assert panel["is_turn"] == 0 and panel["subtext"] is None

    bids, _ = db.replace_panel_beats(
        pids[0],
        [
            {
                "action": "b",
                "composition": "centered",
                "light_quality": "soft",
                "emotional_intent": "jaw set",
                "reveals": "the room",
                "movement_motivation": None,
            }
        ],
    )
    beat = db.get_beat(bids[0])
    assert beat["composition"] == "centered"
    assert beat["light_quality"] == "soft"
    assert beat["emotional_intent"] == "jaw set"
    assert beat["reveals"] == "the room"
    assert beat["movement_motivation"] is None
    db.update_beat(bids[0], movement_motivation="she pulls away")
    assert db.get_beat(bids[0])["movement_motivation"] == "she pulls away"


# ---- subject deletion modes -------------------------------------------------


def _subject_tree(db):
    """Two subjects; beat A casts both + dialog by MAYA, beat B casts only
    RIO, beat C (own shot, own scene) casts nobody but MAYA speaks in it."""
    sb, maya, sc, pa = _build_tree(db)
    rio = db.create_subject(sb, name="RIO", description="tall")
    a = db.create_beat(
        pa,
        action="A",
        subject_ids=[maya, rio],
        dialog=[
            {
                "subject_id": maya,
                "voice": None,
                "delivery": None,
                "language": "en",
                "text": "hi",
            },
            {
                "subject_id": rio,
                "voice": None,
                "delivery": None,
                "language": "en",
                "text": "hey",
            },
        ],
    )
    b = db.create_beat(pa, action="B", sort_order=1, subject_ids=[rio])
    sc2 = db.create_scene(sb, name="Later", sort_order=1)
    pa2 = db.create_panel(sc2, action="talk", sort_order=0)
    c = db.create_beat(
        pa2,
        action="C",
        subject_ids=[],
        dialog=[
            {
                "subject_id": maya,
                "voice": None,
                "delivery": None,
                "language": "en",
                "text": "bye",
            },
        ],
    )
    return sb, maya, rio, sc, pa, sc2, pa2, a, b, c


def test_subject_references_counts_cast_and_dialog(db):
    sb, maya, rio, sc, pa, sc2, pa2, a, b, c = _subject_tree(db)
    refs = db.subject_references(maya)
    assert sorted(refs["beat_ids"]) == sorted([a, c])
    assert refs["image_count"] == 0
    assert db.subject_references(rio)["beat_ids"] == sorted([a, b])
    assert db.subject_references(999999) == {"beat_ids": [], "image_count": 0}


def test_delete_subject_unlink_strips_ids_but_keeps_text(db):
    sb, maya, rio, sc, pa, sc2, pa2, a, b, c = _subject_tree(db)
    assert db.delete_subject(maya) == (True, [])
    tree = db.get_storyboard_tree(sb)
    assert [s["id"] for s in tree["subjects"]] == [rio]
    beats = {
        bt["id"]: bt for s in tree["scenes"] for p in s["panels"] for bt in p["beats"]
    }
    assert set(beats) == {a, b, c}
    assert beats[a]["subject_ids"] == [rio]
    assert beats[a]["action"] == "A"
    assert [d["subject_id"] for d in beats[a]["dialog"]] == [None, rio]
    assert [d["text"] for d in beats[a]["dialog"]] == ["hi", "hey"]
    assert beats[c]["dialog"][0]["subject_id"] is None
    assert beats[c]["dialog"][0]["text"] == "bye"
    assert db.subject_references(maya) == {"beat_ids": [], "image_count": 0}


def test_delete_subject_content_removes_beats_and_empty_shots_scenes(db):
    sb, maya, rio, sc, pa, sc2, pa2, a, b, c = _subject_tree(db)
    assert db.delete_subject(maya, mode="content") == (True, [])
    tree = db.get_storyboard_tree(sb)
    assert [s["id"] for s in tree["subjects"]] == [rio]
    # scene 2's only shot's only beat referenced MAYA -> shot + scene gone
    assert [s["id"] for s in tree["scenes"]] == [sc]
    beats = tree["scenes"][0]["panels"][0]["beats"]
    assert [bt["id"] for bt in beats] == [b]


def test_delete_subject_content_releases_images_purge_trashes(db, tmp_path):
    sb, maya, rio, sc, pa, sc2, pa2, a, b, c = _subject_tree(db)
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    db.save_media(_media(str(img)))
    db.set_media_hidden(str(img), True)
    db.create_beat_image(a, file_path=str(img), seed=1)
    assert db.subject_references(maya)["image_count"] == 1
    assert db.delete_subject(maya, mode="content") == (True, [])
    hidden = {
        r["file_path"]: r["hidden"]
        for r in db.get_all_media_summaries(include_hidden=True)
    }
    assert hidden[str(img)] == 0

    # purge on the other subject: beat b's image is trashed
    img2 = tmp_path / "b.png"
    img2.write_bytes(b"x")
    db.save_media(_media(str(img2)))
    db.set_media_hidden(str(img2), True)
    db.create_beat_image(b, file_path=str(img2), seed=1)
    ok, purged = db.delete_subject(rio, mode="purge")
    assert ok is True
    assert purged == [str(img2)]
    assert db.get_media(Path(str(img2))) is None


def test_delete_subject_bad_mode_raises(db):
    sb, maya, *_ = _subject_tree(db)
    with pytest.raises(ValueError):
        db.delete_subject(maya, mode="nope")


def test_scene_template_brief_composed_from_round_trip(db):
    sb, su, sc, pa = _build_tree(db)
    db.update_scene(
        sc,
        template_id="two_party_negotiation_18",
        brief="A asks; B refuses.",
        composed_from={"stage": "scenes", "outline_hash": "abc", "at": "2026-08-29"},
    )
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["template_id"] == "two_party_negotiation_18"
    assert scene["brief"] == "A asks; B refuses."
    assert scene["composed_from"] == {
        "stage": "scenes",
        "outline_hash": "abc",
        "at": "2026-08-29",
    }
    db.update_scene(sc, template_id=None, composed_from=None)
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["template_id"] is None and scene["composed_from"] is None


def test_replace_storyboard_scenes_writes_brief(db):
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    ids, _ = db.replace_storyboard_scenes(
        sb, [{"name": "S", "brief": "what must happen", "arc_beats": ["setup"]}]
    )
    assert db.get_scene(ids[0])["brief"] == "what must happen"


def test_merge_scenes_folds_adjacent_scenes(db):
    sb = db.create_storyboard(name="M", target_model="sd", architecture="t2i")
    db.update_storyboard(sb, outline='{"logline": "L"}')
    a = db.create_scene(
        sb,
        name="A",
        sort_order=0,
        setting="yard",
        brief="A asks.",
        notes="n1",
        function="negotiation",
        template_id="two_party_negotiation_18",
    )
    b = db.create_scene(sb, name="B", sort_order=1, brief="", notes="n2")
    c = db.create_scene(sb, name="C", sort_order=2, brief="B refuses.")
    d = db.create_scene(sb, name="D", sort_order=3)
    db.update_scene(a, arc_beats=["setup"], charge_in=0, charge_out=-1)
    db.update_scene(b, arc_beats=["rising"], charge_in=-1, charge_out=-2)
    db.update_scene(c, arc_beats=["rising", "turn"], charge_in=-2, charge_out=3)
    pa = db.create_panel(a, action="a1", sort_order=0)
    pb = db.create_panel(b, action="b1", sort_order=0)
    pc0 = db.create_panel(c, action="c1", sort_order=0)
    pc1 = db.create_panel(c, action="c2", sort_order=1)

    merged = db.merge_scenes([a, b, c])
    assert merged == a

    tree = db.get_storyboard_tree(sb)
    assert [s["name"] for s in tree["scenes"]] == ["A", "D"]
    assert [s["sort_order"] for s in tree["scenes"]] == [0, 1]
    s = tree["scenes"][0]
    assert s["setting"] == "yard" and s["function"] == "negotiation"
    assert s["brief"] == "A asks.\n\nB refuses."
    assert s["notes"] == "n1\n\nn2"
    assert s["arc_beats"] == ["setup", "rising", "turn"]
    assert s["charge_in"] == 0 and s["charge_out"] == 3
    assert s["template_id"] is None
    assert s["composed_from"]["stage"] == "merge"
    assert [(p["id"], p["sort_order"]) for p in s["panels"]] == [
        (pa, 0),
        (pb, 1),
        (pc0, 2),
        (pc1, 3),
    ]
    assert db.get_scene(b) is None and db.get_scene(c) is None
    assert db.get_scene(d)["sort_order"] == 1


def test_merge_scenes_rejects_bad_input(db):
    sb = db.create_storyboard(name="M", target_model="sd", architecture="t2i")
    a = db.create_scene(sb, name="A", sort_order=0)
    db.create_scene(sb, name="B", sort_order=1)
    c = db.create_scene(sb, name="C", sort_order=2)
    other = db.create_storyboard(name="O", target_model="sd", architecture="t2i")
    o = db.create_scene(other, name="O1", sort_order=0)
    with pytest.raises(ValueError, match="at least two"):
        db.merge_scenes([a])
    with pytest.raises(ValueError, match="adjacent"):
        db.merge_scenes([a, c])
    with pytest.raises(ValueError, match="same storyboard"):
        db.merge_scenes([a, o])
    with pytest.raises(ValueError, match="unknown scene"):
        db.merge_scenes([a, 99999])
