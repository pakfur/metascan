"""DB tests for per-shot lora stacks (panels.image_loras / panels.video_loras)."""

from __future__ import annotations

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _panel(db) -> int:
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=42
    )
    sc = db.create_scene(sb, name="Salvage Yard", sort_order=0)
    return sb, db.create_panel(sc, action="hand rests on hull seam", sort_order=0)


def test_new_panel_defaults_to_empty_lora_lists(db):
    _, pa = _panel(db)
    panel = db.get_panel(pa)
    assert panel["image_loras"] == []
    assert panel["video_loras"] == []


def test_update_panel_round_trips_lora_lists(db):
    _, pa = _panel(db)
    image_loras = [
        {"name": "style.safetensors", "strength": 0.7},
        {"name": "detail.safetensors", "strength": 1.0},
    ]
    video_loras = [{"name": "motion.safetensors", "strength": 0.9}]
    db.update_panel(pa, image_loras=image_loras, video_loras=video_loras)
    panel = db.get_panel(pa)
    assert panel["image_loras"] == image_loras
    assert panel["video_loras"] == video_loras


def test_storyboard_tree_parses_panel_lora_lists(db):
    sb, pa = _panel(db)
    db.update_panel(pa, image_loras=[{"name": "a.safetensors", "strength": 1.0}])
    tree = db.get_storyboard_tree(sb)
    panel = tree["scenes"][0]["panels"][0]
    assert panel["image_loras"] == [{"name": "a.safetensors", "strength": 1.0}]
    assert panel["video_loras"] == []


def test_corrupt_lora_json_reads_as_empty_list(db):
    _, pa = _panel(db)
    with db.lock, db._get_connection() as conn:
        conn.execute("UPDATE panels SET image_loras = 'not json' WHERE id = ?", (pa,))
        conn.commit()
    assert db.get_panel(pa)["image_loras"] == []
