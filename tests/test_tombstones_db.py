"""Tombstones written on every media delete path (taste-rating capture)."""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

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


def test_untouched_item_leaves_no_tombstone(db):
    db.save_media(_media("/p/a.png"))
    db.delete_media(Path("/p/a.png"))
    assert db.list_tombstones() == []


def test_judged_item_tombstone_has_judgment_and_embedding(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "fail")
    db.set_reason("/p/a.png", "composition", True)
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-medium"))
    db.delete_media(Path("/p/a.png"))
    [t] = db.list_tombstones()
    assert (t["grade"], json.loads(t["reasons"])) == ("fail", ["composition"])
    # The pre-grades reject flag is not a column any more.
    assert "passed" not in t
    # The vector is kept per CLIP model, under the model it came from; the
    # legacy single-vector columns stay empty.
    assert db.get_tombstone_vectors(t["id"]) == {"clip-medium": vec.tobytes()}
    assert t["embedding"] is None and t["embedding_model"] is None
    assert db.list_judgment_events("/p/a.png")  # event log survives the delete


def test_lookup_failure_does_not_block_delete(db):
    db.save_media(_media("/p/a.png"))
    db.set_favorite(Path("/p/a.png"), True)
    db.set_grade("/p/a.png", "pass")
    db.set_embedding_lookup(lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert db.delete_media(Path("/p/a.png")) is True
    [t] = db.list_tombstones()
    assert db.get_tombstone_vectors(t["id"]) == {}
    assert (t["is_favorite"], t["grade"]) == (1, "pass")


def test_lookup_none_leaves_embedding_null(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass")
    db.set_embedding_lookup(lambda p: None)
    db.delete_media(Path("/p/a.png"))
    assert db.get_tombstone_vectors(db.list_tombstones()[0]["id"]) == {}


def test_t2i_delete_writes_one_tombstone_with_lineage(db):
    db.save_media(_media("/p/gen.png"))
    image_id = db.create_t2i_image(
        file_path="/p/gen.png",
        batch_id="b1",
        model="krea2",
        prompt_used="synthetic prompt",
        writer_id="base",
        form_state={"x": 1},
    )
    db.delete_t2i_image(image_id)
    [t] = db.list_tombstones()
    assert t["lineage"]["t2i"]["writer_id"] == "base"
    assert "form_state" not in t["lineage"]["t2i"]
    assert "i2v" not in t["lineage"]


def test_i2v_delete_writes_one_tombstone(db):
    db.save_media(_media("/p/src.png"))
    db.save_media(_media("/p/clip.mp4"))
    vid = db.create_i2v_video(
        source_path="/p/src.png",
        file_path="/p/clip.mp4",
        prompt_used="synthetic",
        writer_id="w1",
        form_state={"x": 1},
    )
    db.delete_i2v_video(vid)
    [t] = db.list_tombstones()
    assert t["file_path"] == "/p/clip.mp4"
    assert t["lineage"]["i2v"]["writer_id"] == "w1"
    assert "form_state" not in t["lineage"]["i2v"]


def test_i2v_source_image_is_not_tombstoned(db):
    db.save_media(_media("/p/src.png"))
    db.save_media(_media("/p/clip.mp4"))
    db.create_i2v_video(source_path="/p/src.png", file_path="/p/clip.mp4")
    db.delete_media(Path("/p/src.png"))
    assert db.list_tombstones() == []


def test_batch_delete_tombstones_only_judged(db):
    for n in ("a", "b", "c"):
        db.save_media(_media(f"/p/{n}.png"))
    db.set_grade("/p/b.png", "pass")
    n = db.delete_media_batch([Path(f"/p/{x}.png") for x in "abc"])
    assert n == 3
    [t] = db.list_tombstones()
    assert t["file_path"] == "/p/b.png"


def test_storyboard_purge_released_path_has_no_tombstone(db):
    sb = db.create_storyboard(
        name="Yard", target_model="sd", architecture="t2i", base_seed=42
    )
    sc = db.create_scene(sb, name="s", sort_order=0)
    pa = db.create_panel(sc, action="a", sort_order=0)
    beat_id = db.create_beat(pa, action="a beat")
    db.save_media(_media("/p/gen.png"))
    db.set_grade("/p/gen.png", "pass")
    image_id = db.create_t2i_image(file_path="/p/gen.png", writer_id="base")
    db.create_beat_image(beat_id, file_path="/p/gen.png")
    deleted, purged = db.delete_t2i_image(image_id)
    assert deleted is True and purged == []
    assert db.list_tombstones() == []
    assert db.get_judgment("/p/gen.png") is not None  # media survived


def test_purged_path_is_tombstoned(db):
    db.save_media(_media("/p/gen.png"))
    db.set_grade("/p/gen.png", "fail")
    image_id = db.create_t2i_image(file_path="/p/gen.png", writer_id="base")
    deleted, purged = db.delete_t2i_image(image_id)
    assert deleted is True and len(purged) == 1
    assert len(db.list_tombstones()) == 1


def test_category_link_makes_tombstone_with_names(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO media_categories (file_path, category_id, source, "
            "assigned_at) VALUES (?, ?, 'bulk', 'now')",
            ("/p/a.png", cid),
        )
        conn.commit()
    db.delete_media(Path("/p/a.png"))
    [t] = db.list_tombstones()
    assert json.loads(t["categories"]) == ["Anime"]


def test_categorized_only_item_tombstoned_with_names(db):
    db.save_media(_media("/p/a.png"))
    cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "bulk")
    db.delete_media(Path("/p/a.png"))
    assert json.loads(db.list_tombstones()[0]["categories"]) == ["Anime"]


# ---- deleting keeps the signal (grades spec, items 1-4) ----------------------


def _library_image(db, path: str, prompt: str, models) -> None:
    """A library image (no T2I row) with a metadata prompt and model names."""
    db.save_media(_media(path))
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media SET data = json_set(data, '$.prompt', ?) "
            "WHERE file_path = ?",
            (prompt, path),
        )
        conn.executemany(
            "INSERT INTO indices (index_type, index_key, file_path) "
            "VALUES ('model', ?, ?)",
            [(m, path) for m in models],
        )
        conn.commit()


def test_deleted_pass_keeps_its_brief_and_library_prompt(db):
    # A ✓ library image: brief, its model, the metadata prompt and model
    # names (what hindsight needs) and the archived thumbnail's path.
    _library_image(db, "/p/lib.png", "SYNTH library prompt", ["t5xxl", "krea2_dev"])
    db.set_grade("/p/lib.png", "pass")
    db.set_hindsight_brief("/p/lib.png", "a fox in the snow", "qwen3vl-4b")
    # A ✓ T2I image: its brief; the lineage already carries its prompt.
    db.save_media(_media("/p/gen.png"))
    image_id = db.create_t2i_image(
        file_path="/p/gen.png", model="krea2", prompt_used="SYNTH t2i", writer_id="base"
    )
    db.set_grade("/p/gen.png", "pass")
    db.set_hindsight_brief("/p/gen.png", "a hare at night", "qwen3vl-4b")
    # ✗ (briefed while ✓) and seen-only library images: the brief stays with
    # the image, the prompt is kept for ✓ only.
    _library_image(db, "/p/fail.png", "SYNTH failed prompt", ["krea2_dev"])
    db.set_grade("/p/fail.png", "pass")
    db.set_hindsight_brief("/p/fail.png", "a cat on a sofa", "qwen3vl-4b")
    db.set_grade("/p/fail.png", "fail")
    _library_image(db, "/p/seen.png", "SYNTH seen prompt", ["krea2_dev"])
    db.mark_seen("/p/seen.png")

    assert db.delete_media(Path("/p/lib.png"), "archive/thumbs/aaa.jpg") is True
    assert db.delete_t2i_image(image_id, {"/p/gen.png": "archive/thumbs/bbb.jpg"})[0]
    assert (
        db.delete_media_batch(
            [Path("/p/fail.png"), Path("/p/seen.png")],
            {"/p/fail.png": "archive/thumbs/ccc.jpg"},
        )
        == 2
    )
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    lib = tombs["/p/lib.png"]
    assert (
        lib["grade"],
        lib["brief"],
        lib["brief_model"],
        lib["prompt"],
        json.loads(lib["model_name"]),
        lib["thumb_path"],
    ) == (
        "pass",
        "a fox in the snow",
        "qwen3vl-4b",
        "SYNTH library prompt",
        ["krea2_dev", "t5xxl"],
        "archive/thumbs/aaa.jpg",
    )
    gen = tombs["/p/gen.png"]
    assert (gen["brief"], gen["brief_model"], gen["thumb_path"]) == (
        "a hare at night",
        "qwen3vl-4b",
        "archive/thumbs/bbb.jpg",
    )
    assert gen["prompt"] is None and gen["model_name"] is None
    assert gen["lineage"]["t2i"]["prompt_used"] == "SYNTH t2i"
    failed = tombs["/p/fail.png"]
    assert (failed["grade"], failed["brief"], failed["thumb_path"]) == (
        "fail",
        "a cat on a sofa",
        "archive/thumbs/ccc.jpg",
    )
    assert failed["prompt"] is None and failed["model_name"] is None
    seen = tombs["/p/seen.png"]
    assert (seen["grade"], seen["brief"], seen["prompt"], seen["thumb_path"]) == (
        None,
        None,
        None,
        None,
    )
    # The briefs table cascaded with the media rows: the tombstones keep them.
    assert db.get_hindsight_briefs() == {}


def test_stale_cleanup_keeps_everything_but_the_thumbnail(db):
    _library_image(db, "/p/lib.png", "SYNTH library prompt", ["krea2_dev"])
    db.set_grade("/p/lib.png", "pass")
    db.set_hindsight_brief("/p/lib.png", "a fox in the snow", "qwen3vl-4b")
    assert db.delete_media_batch([Path("/p/lib.png")]) == 1
    [t] = db.list_tombstones()
    assert (t["grade"], t["brief"], t["prompt"], t["thumb_path"]) == (
        "pass",
        "a fox in the snow",
        "SYNTH library prompt",
        None,
    )


def test_a_library_prompt_that_is_not_json_never_blocks_the_delete(db):
    db.save_media(_media("/p/odd.png"))
    with db.lock, db._get_connection() as conn:
        conn.execute("UPDATE media SET data = '{oops' WHERE file_path = '/p/odd.png'")
        conn.commit()
    db.set_grade("/p/odd.png", "pass")
    assert db.delete_media(Path("/p/odd.png")) is True
    [t] = db.list_tombstones()
    assert (t["grade"], t["prompt"]) == ("pass", None)


def test_set_tombstone_brief(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass")
    db.delete_media(Path("/p/a.png"))
    [t] = db.list_tombstones()
    assert db.set_tombstone_brief(t["id"], "a quiet harbour", "qwen3vl-4b") is True
    [t] = db.list_tombstones()
    assert (t["brief"], t["brief_model"]) == ("a quiet harbour", "qwen3vl-4b")
    assert db.set_tombstone_brief(t["id"] + 1, "nothing", "qwen3vl-4b") is False


def test_tombstone_records_its_scorecard_id(db):
    for name, card in (("card", "card-1"), ("plain", None)):
        db.save_media(_media(f"/p/{name}.png"))
        db.create_t2i_image(
            file_path=f"/p/{name}.png", writer_id="base", scorecard_id=card
        )
        db.set_grade(f"/p/{name}.png", "pass")
        db.delete_media(Path(f"/p/{name}.png"))
    db.save_media(_media("/p/lib.png"))
    db.set_grade("/p/lib.png", "fail")
    db.delete_media(Path("/p/lib.png"))
    got = {t["file_path"]: t["scorecard_id"] for t in db.list_tombstones()}
    assert got == {"/p/card.png": "card-1", "/p/plain.png": None, "/p/lib.png": None}
