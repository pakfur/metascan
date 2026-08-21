"""Beat CRUD + migrations for the story engine (spec V1 §3)."""

from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def panel_id(db):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S1")
    return db.create_panel(scene, action="she opens the door")


@pytest.fixture
def storyboard_id(db, panel_id):
    return db.storyboard_id_for_panel(panel_id)


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
def media_paths(db):
    paths = ["/pics/a.png", "/pics/b.png"]
    for p in paths:
        db.save_media(_media(p))
    return paths


def _hidden(db, path: str) -> int:
    with db.lock, db._get_connection() as conn:
        row = conn.execute(
            "SELECT hidden FROM media WHERE file_path = ?", (path,)
        ).fetchone()
        return int(row["hidden"])


def _insert_job(db, *, panel_id=None, beat_id=None) -> int:
    preset_id = db.create_workflow_preset("p", "t2i", "{}", "{}")
    with db.lock, db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO generation_jobs (preset_id, panel_id, beat_id, params) "
            "VALUES (?, ?, ?, '{}')",
            (preset_id, panel_id, beat_id),
        )
        conn.commit()
        return int(cur.lastrowid)


def _job_count(db, *, panel_id=None, beat_id=None) -> int:
    with db.lock, db._get_connection() as conn:
        if beat_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM generation_jobs WHERE beat_id = ?",
                (beat_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM generation_jobs WHERE panel_id = ?",
                (panel_id,),
            ).fetchone()
        return int(row["n"])


def test_beat_crud_roundtrip(db, panel_id):
    bid = db.create_beat(
        panel_id,
        action="hand touches the handle",
        duration_s=3.5,
        camera_motion="push_in",
        camera_amplitude="small",
        camera_speed="slow",
        dialog=[
            {
                "subject_id": None,
                "voice": "low male voice",
                "delivery": None,
                "language": "English",
                "text": "Wait.",
            }
        ],
        sound="hinge creak",
    )
    beat = db.get_beat(bid)
    assert beat["action"] == "hand touches the handle"
    assert beat["duration_s"] == 3.5
    assert beat["camera_motion"] == "push_in"
    assert beat["is_cut"] == 0
    assert beat["dialog"][0]["text"] == "Wait."
    db.update_beat(bid, action="hand grips the handle", dialog=[])
    beat2 = db.get_beat(bid)
    assert beat2["action"] == "hand grips the handle"
    assert beat2["dialog"] == []
    ok, purged = db.delete_beat(bid)
    assert ok is True and purged == []
    assert db.get_beat(bid) is None
    ok, purged = db.delete_beat(bid)
    assert ok is False and purged == []


def test_update_beat_rejects_unknown_field(db, panel_id):
    bid = db.create_beat(panel_id, action="x")
    with pytest.raises(ValueError, match="Not updatable"):
        db.update_beat(bid, panel_id=999)


def test_replace_panel_beats_is_transactional(db, panel_id):
    db.create_beat(panel_id, action="old one")
    ids, _ = db.replace_panel_beats(
        panel_id,
        [
            {"action": "new a", "duration_s": 4.0, "sort_order": 0},
            {
                "action": "new b",
                "duration_s": 5.0,
                "sort_order": 1,
                "camera_motion": "static",
            },
        ],
    )
    assert len(ids) == 2
    tree_beats = [b["action"] for b in db.list_beats(panel_id)]
    assert tree_beats == ["new a", "new b"]


def test_beats_cascade_with_panel(db, panel_id):
    bid = db.create_beat(panel_id, action="x")
    db.delete_panel(panel_id)
    assert db.get_beat(bid) is None


def test_tree_embeds_beats_and_new_columns(db, panel_id):
    db.create_beat(panel_id, action="b2", sort_order=1)
    db.create_beat(panel_id, action="b1", sort_order=0)
    sb_id = db.storyboard_id_for_panel(panel_id)
    db.update_storyboard(sb_id, outline='{"logline": "x"}')
    subj = db.create_subject(sb_id, name="Maya", description="d", voice="warm alto")
    db.update_subject(subj, voice="clear alto")
    db.update_panel(panel_id, duration_s=10.0)
    tree = db.get_storyboard_tree(sb_id)
    assert tree["outline"] == '{"logline": "x"}'
    assert tree["subjects"][0]["voice"] == "clear alto"
    panel = tree["scenes"][0]["panels"][0]
    assert panel["duration_s"] == 10.0
    assert [b["action"] for b in panel["beats"]] == ["b1", "b2"]


def test_beat_images_crud_and_keeper(db, panel_id, media_paths):
    """create/list/count beat_images; select_beat_image flips hidden."""
    beat_id = db.create_beat(panel_id, action="a beat")
    img1 = db.create_beat_image(
        beat_id, file_path=media_paths[0], seed=1, variant_index=0
    )
    img2 = db.create_beat_image(
        beat_id, file_path=media_paths[1], seed=2, variant_index=1
    )
    assert db.count_beat_images(beat_id) == 2
    assert [i["id"] for i in db.list_beat_images(beat_id)] == [img1, img2]

    db.set_media_hidden(media_paths[0], True)
    db.set_media_hidden(media_paths[1], True)
    assert db.select_beat_image(beat_id, img1) is True
    assert _hidden(db, media_paths[0]) == 0  # keeper unhidden
    assert db.select_beat_image(beat_id, img2) is True
    assert _hidden(db, media_paths[0]) == 1  # old keeper re-hidden
    assert _hidden(db, media_paths[1]) == 0
    assert db.select_beat_image(beat_id, None) is True
    assert db.get_beat(beat_id)["selected_image_id"] is None


def test_delete_beat_releases_media_and_jobs(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="a beat")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    _insert_job(db, panel_id=panel_id, beat_id=beat_id)  # raw INSERT helper
    ok, purged = db.delete_beat(beat_id)
    assert ok is True and purged == []
    assert _hidden(db, media_paths[0]) == 0
    assert _job_count(db, beat_id=beat_id) == 0


def test_delete_beat_purge_returns_paths(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="a beat")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    ok, purged = db.delete_beat(beat_id, purge_images=True)
    assert ok is True
    assert len(purged) == 1  # native path returned
    with db._get_connection() as conn:  # media row deleted
        assert (
            conn.execute(
                "SELECT 1 FROM media WHERE file_path = ?", (media_paths[0],)
            ).fetchone()
            is None
        )


def test_replace_panel_beats_releases_old_beats(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    new_ids, _ = db.replace_panel_beats(
        panel_id,
        [
            {"action": "new", "shot_size": "CU", "subject_ids": [1, 2]},
        ],
    )
    assert len(new_ids) == 1
    assert _hidden(db, media_paths[0]) == 0
    got = db.get_beat(new_ids[0])
    assert got["shot_size"] == "CU" and got["subject_ids"] == [1, 2]


def test_replace_panel_beats_purges_images_on_confirm(db, panel_id, media_paths):
    """A user-confirmed beats recompose purges the old beats' images:
    media rows are deleted (native paths returned for trashing) instead
    of released into the library."""
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    new_ids, purged = db.replace_panel_beats(
        panel_id, [{"action": "new"}], purge_images=True
    )
    assert len(new_ids) == 1
    assert len(purged) == 1
    with db.lock, db._get_connection() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM media WHERE file_path = ?", (media_paths[0],)
            ).fetchone()
            is None
        )


def test_replace_panel_beats_purge_spares_shared_file(db, panel_id, media_paths):
    """A file another surviving beat still references (a different panel's
    beat_images row) must survive the purge -- it is unhidden instead."""
    doomed = db.create_beat(panel_id, action="doomed")
    db.create_beat_image(doomed, file_path=media_paths[0])
    sb = db.storyboard_id_for_panel(panel_id)
    other_scene = db.create_scene(sb, name="S2")
    other_panel = db.create_panel(other_scene, action="other")
    survivor = db.create_beat(other_panel, action="keeps the file")
    db.create_beat_image(survivor, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    _, purged = db.replace_panel_beats(panel_id, [{"action": "new"}], purge_images=True)
    assert purged == []
    assert _hidden(db, media_paths[0]) == 0


def test_replace_panel_beats_purge_leaves_panel_videos(db, panel_id, media_paths):
    """Beats recompose is beat-scoped: the shot's rendered clips survive a
    confirmed purge untouched."""
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    _, purged = db.replace_panel_beats(panel_id, [{"action": "new"}], purge_images=True)
    assert purged and all("take.mp4" not in p for p in purged)
    with db.lock, db._get_connection() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM panel_videos WHERE panel_id = ?", (panel_id,)
            ).fetchone()
            is not None
        )


def test_replace_scene_panels_purges_images_and_clips(db, panel_id, media_paths):
    """A confirmed shots recompose destroys the scene's panels -- their
    beat images AND rendered clips purge together."""
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    with db.lock, db._get_connection() as conn:
        scene_id = int(
            conn.execute(
                "SELECT scene_id FROM panels WHERE id = ?", (panel_id,)
            ).fetchone()["scene_id"]
        )
    new_ids, purged = db.replace_scene_panels(
        scene_id, [{"action": "fresh"}], purge_images=True
    )
    assert len(new_ids) == 1
    assert len(purged) == 2
    with db.lock, db._get_connection() as conn:
        for path in (media_paths[0], "/vids/take.mp4"):
            assert (
                conn.execute(
                    "SELECT 1 FROM media WHERE file_path = ?", (path,)
                ).fetchone()
                is None
            )


def test_replace_scene_panels_without_purge_still_releases(db, panel_id, media_paths):
    """Default (unconfirmed) replace keeps the old behavior: media rows
    survive, unhidden into the library."""
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    with db.lock, db._get_connection() as conn:
        scene_id = int(
            conn.execute(
                "SELECT scene_id FROM panels WHERE id = ?", (panel_id,)
            ).fetchone()["scene_id"]
        )
    _, purged = db.replace_scene_panels(scene_id, [{"action": "fresh"}])
    assert purged == []
    assert _hidden(db, media_paths[0]) == 0


def test_replace_structure_purges_on_confirmed_reparse(
    db, panel_id, media_paths, storyboard_id
):
    """A confirmed re-parse (replace_storyboard_structure with
    purge_images=True) purges the whole old tree's generated media."""
    db.save_media(_media("/vids/take.mp4"))
    db.create_panel_video(panel_id, file_path="/vids/take.mp4")
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    purged = db.replace_storyboard_structure(
        storyboard_id,
        {"subjects": [], "scenes": [{"name": "N", "panels": [{"action": "a"}]}]},
        purge_images=True,
    )
    assert len(purged) == 2
    with db.lock, db._get_connection() as conn:
        for path in (media_paths[0], "/vids/take.mp4"):
            assert (
                conn.execute(
                    "SELECT 1 FROM media WHERE file_path = ?", (path,)
                ).fetchone()
                is None
            )


def test_delete_panel_releases_beat_images(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="b")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    ok, _ = db.delete_panel(panel_id)
    assert ok is True
    assert _hidden(db, media_paths[0]) == 0


def test_tree_beats_carry_images_and_subject_ids(
    db, panel_id, media_paths, storyboard_id
):
    beat_id = db.create_beat(panel_id, action="b", subject_ids=[3])
    db.create_beat_image(beat_id, file_path=media_paths[0])
    tree = db.get_storyboard_tree(storyboard_id)
    beat = tree["scenes"][0]["panels"][0]["beats"][0]
    assert beat["subject_ids"] == [3]
    assert len(beat["images"]) == 1
    assert "subject_ids" not in tree["scenes"][0]["panels"][0]


def test_purge_spares_media_referenced_by_scene(db, panel_id, media_paths):
    """A beat image whose file is also a scene's reference_path must
    survive a purge delete -- deleting the media row would silently null
    scenes.reference_path's FK (spec §4)."""
    scene_id = db.get_panel(panel_id)["scene_id"]
    db.update_scene(scene_id, reference_path=media_paths[0])
    beat_id = db.create_beat(panel_id, action="a beat")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)

    ok, purged = db.delete_beat(beat_id, purge_images=True)

    assert ok is True
    assert purged == []
    assert _hidden(db, media_paths[0]) == 0
    with db._get_connection() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM media WHERE file_path = ?", (media_paths[0],)
            ).fetchone()
            is not None
        )
