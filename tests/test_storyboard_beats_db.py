"""Beat CRUD + migrations for the story engine (spec V1 §3)."""

import pytest

from metascan.core.database_sqlite import DatabaseManager


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
    assert db.delete_beat(bid) is True
    assert db.get_beat(bid) is None
    assert db.delete_beat(bid) is False


def test_update_beat_rejects_unknown_field(db, panel_id):
    bid = db.create_beat(panel_id, action="x")
    with pytest.raises(ValueError, match="Not updatable"):
        db.update_beat(bid, panel_id=999)


def test_replace_panel_beats_is_transactional(db, panel_id):
    db.create_beat(panel_id, action="old one")
    ids = db.replace_panel_beats(
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
