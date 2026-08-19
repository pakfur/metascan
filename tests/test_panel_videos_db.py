"""panel_videos: CRUD, the beat_images->panel_videos data move, tree
assembly, and release/purge semantics on panel delete."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="mp4",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _tree(db) -> tuple[int, int, int]:
    sb_id = db.create_storyboard(
        name="Board",
        target_model="sd",
        architecture="t2i",
        aspect_ratio="16:9",
        base_seed=1,
        batch_size=1,
    )
    scene_id = db.create_scene(sb_id, name="Scene", sort_order=0)
    panel_id = db.create_panel(scene_id, action="a", sort_order=0, duration_s=5.0)
    return sb_id, scene_id, panel_id


def test_create_list_count_roundtrip(db):
    _, _, panel_id = _tree(db)
    db.save_media(_media("/vids/take.mp4"))
    vid = db.create_panel_video(
        panel_id, file_path="/vids/take.mp4", seed=42, variant_index=0
    )
    assert vid > 0
    rows = db.list_panel_videos(panel_id)
    assert len(rows) == 1 and rows[0]["seed"] == 42
    assert db.count_panel_videos(panel_id) == 1


def test_tree_includes_panel_videos(db):
    sb_id, _, panel_id = _tree(db)
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")
    tree = db.get_storyboard_tree(sb_id)
    videos = tree["scenes"][0]["panels"][0]["videos"]
    assert len(videos) == 1
    assert videos[0]["file_path"].endswith("take.mp4")


def test_migration_moves_video_beat_images_to_panel(tmp_path):
    """Re-initializing an existing DB relocates video-suffixed beat_images
    rows to panel_videos, unhides their media, and clears any keeper
    pointer that referenced a moved clip."""
    db = DatabaseManager(tmp_path / "db")
    _, _, panel_id = _tree(db)
    beat_id = db.create_beat(panel_id, action="", sort_order=0)
    db.save_media(_media("/vids/clip.mp4"))
    db.save_media(_media("/vids/still.png"))
    clip_row = db.create_beat_image(beat_id, file_path="/vids/clip.mp4", seed=7)
    db.create_beat_image(beat_id, file_path="/vids/still.png")
    db.set_media_hidden("/vids/clip.mp4", True)
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE beats SET selected_image_id = ? WHERE id = ?",
            (clip_row, beat_id),
        )
        conn.commit()
    db.close()

    db2 = DatabaseManager(tmp_path / "db")
    try:
        videos = db2.list_panel_videos(panel_id)
        assert [Path(v["file_path"]).name for v in videos] == ["clip.mp4"]
        assert videos[0]["seed"] == 7
        images = db2.list_beat_images(beat_id)
        assert [Path(i["file_path"]).name for i in images] == ["still.png"]
        with db2.lock, db2._get_connection() as conn:
            hidden = conn.execute(
                "SELECT hidden FROM media WHERE file_path = '/vids/clip.mp4'"
            ).fetchone()["hidden"]
            keeper = conn.execute(
                "SELECT selected_image_id FROM beats WHERE id = ?", (beat_id,)
            ).fetchone()["selected_image_id"]
        assert hidden == 0
        assert keeper is None
    finally:
        db2.close()


def test_delete_panel_releases_hidden_clip_media(db):
    _, _, panel_id = _tree(db)
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")
    db.set_media_hidden("/vids/take.mp4", True)  # pre-panel_videos legacy state

    deleted, purged = db.delete_panel(panel_id)
    assert deleted and purged == []
    with db.lock, db._get_connection() as conn:
        hidden = conn.execute(
            "SELECT hidden FROM media WHERE file_path = '/vids/take.mp4'"
        ).fetchone()["hidden"]
        n = conn.execute("SELECT COUNT(*) AS n FROM panel_videos").fetchone()["n"]
    assert hidden == 0
    assert n == 0


def test_delete_panel_purges_clip_media(db):
    _, _, panel_id = _tree(db)
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")

    deleted, purged = db.delete_panel(panel_id, purge_images=True)
    assert deleted
    assert [Path(p).name for p in purged] == ["take.mp4"]
    with db.lock, db._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM media WHERE file_path = '/vids/take.mp4'"
        ).fetchone()["n"]
    assert n == 0
