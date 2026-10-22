"""Generation lineage columns written by create_t2i_image / create_i2v_video."""

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


def test_create_t2i_image_stores_lineage(db):
    db.save_media(_media("/p/g.png"))
    i = db.create_t2i_image(
        file_path="/p/g.png", writer_id="base", variant_group="vg1", variant_index=2
    )
    with db._get_connection() as conn:
        r = conn.execute(
            "SELECT writer_id, variant_group, variant_index FROM t2i_images WHERE id=?",
            (i,),
        ).fetchone()
    assert tuple(r) == ("base", "vg1", 2)


def test_create_t2i_image_lineage_defaults_to_null(db):
    db.save_media(_media("/p/h.png"))
    i = db.create_t2i_image(file_path="/p/h.png")
    with db._get_connection() as conn:
        r = conn.execute(
            "SELECT writer_id, variant_group, variant_index FROM t2i_images WHERE id=?",
            (i,),
        ).fetchone()
    assert tuple(r) == (None, None, None)


def test_create_i2v_video_stores_lineage(db):
    db.save_media(_media("/p/s.png"))
    db.save_media(_media("/p/v.mp4"))
    i = db.create_i2v_video(
        source_path="/p/s.png",
        file_path="/p/v.mp4",
        writer_id="manual",
        variant_group="vg9",
        variant_index=0,
    )
    with db._get_connection() as conn:
        r = conn.execute(
            "SELECT writer_id, variant_group, variant_index FROM i2v_videos WHERE id=?",
            (i,),
        ).fetchone()
    assert tuple(r) == ("manual", "vg9", 0)
