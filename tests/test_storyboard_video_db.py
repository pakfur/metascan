"""Video-prompt columns (spec V3 §2)."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def ids(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S")
    panel = db.create_panel(scene, action="she runs")
    return sb, panel


def test_storyboard_video_fields_roundtrip(db, ids):
    sb, _ = ids
    db.update_storyboard(sb, video_target="minimax", video_mode="ref2va")
    row = db.get_storyboard(sb)
    assert row["video_target"] == "minimax"
    assert row["video_mode"] == "ref2va"
    db.update_storyboard(sb, video_target=None)
    assert db.get_storyboard(sb)["video_target"] is None


def test_panel_video_prompt_fields_and_warnings_decode(db, ids):
    sb, panel = ids
    db.update_panel(
        panel,
        video_prompt="subject_definitions: ...",
        video_prompt_source="compiled",
        video_prompt_warnings='["word count low"]',
    )
    got = db.get_panel(panel)
    assert got["video_prompt"].startswith("subject_definitions")
    assert got["video_prompt_locked"] == 0
    assert got["video_prompt_warnings"] == ["word count low"]
    tree = db.get_storyboard_tree(sb)
    tp = tree["scenes"][0]["panels"][0]
    assert tp["video_prompt_warnings"] == ["word count low"]


def test_warnings_null_decodes_to_empty_list(db, ids):
    _, panel = ids
    assert db.get_panel(panel)["video_prompt_warnings"] == []
