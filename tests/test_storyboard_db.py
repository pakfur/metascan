"""Tests for the Phase B storyboard tables + CRUD on DatabaseManager."""

from __future__ import annotations

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
    assert db.delete_storyboard(sb) is True
    with db.lock, db._get_connection() as conn:
        for table in ("storyboard_subjects", "scenes", "panels"):
            assert (
                conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
            )


def test_delete_storyboard_missing_returns_false(db):
    assert db.delete_storyboard(999) is False


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
    assert db.delete_scene(sc) is True
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
    assert db.delete_panel(pa) is True
    assert db.get_generation_job(jid) is None


def test_delete_panel_missing_returns_false(db):
    assert db.delete_panel(999) is False


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


def test_storyboard_id_for_panel(db):
    sb, su, sc, pa = _build_tree(db)
    assert db.storyboard_id_for_panel(pa) == sb


def test_storyboard_id_for_panel_missing_returns_none(db):
    assert db.storyboard_id_for_panel(999) is None
