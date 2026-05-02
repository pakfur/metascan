"""Tests for the spec §7.4 tag-merge matrix.

Each test names the (existing source × incoming source) cell it covers.
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "test.db"
        manager = DatabaseManager(db_file)
        yield manager
        manager.close()


def _register_path(db: DatabaseManager, path: Path) -> None:
    """Insert a minimal media row so FK constraints are satisfied."""
    media = Media(
        file_path=path,
        file_size=100,
        width=64,
        height=64,
        format="jpeg",
        created_at=datetime(2024, 1, 1),
        modified_at=datetime(2024, 1, 1),
    )
    db.save_media(media)


def _read_tag_rows(db: DatabaseManager, path: Path):
    """Return [(index_key, source)] for tag rows on path, ordered by key."""
    posix = path.as_posix()
    with db.lock:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT index_key, source FROM indices "
                "WHERE index_type='tag' AND file_path=? "
                "ORDER BY index_key",
                (posix,),
            ).fetchall()
    return [(r["index_key"], r["source"]) for r in rows]


def test_add_vlm_tags_to_empty_inserts_vlm(db):
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["red", "blue"], source="vlm")
    assert _read_tag_rows(db, p) == [("blue", "vlm"), ("red", "vlm")]


def test_add_vlm_tags_over_clip_replaces_clip(db):
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["red", "blue"], source="clip")
    db.add_tag_indices(p, ["green", "yellow"], source="vlm")
    rows = _read_tag_rows(db, p)
    sources = {s for _, s in rows}
    assert "clip" not in sources
    keys = {k for k, _ in rows}
    assert keys == {"green", "yellow"}


def test_add_vlm_tags_overlapping_with_prompt_upserts_to_vlm_prompt(db):
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["red"], source="prompt")
    db.add_tag_indices(p, ["red", "blue"], source="vlm")
    rows = dict(_read_tag_rows(db, p))
    assert rows["red"] == "vlm+prompt"
    assert rows["blue"] == "vlm"


def test_add_clip_tags_does_not_overwrite_vlm(db):
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["red"], source="vlm")
    db.add_tag_indices(p, ["blue"], source="clip")
    rows = dict(_read_tag_rows(db, p))
    assert rows == {"red": "vlm"}


def test_add_vlm_tags_replaces_previous_vlm(db):
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["old1", "old2"], source="vlm")
    db.add_tag_indices(p, ["new1", "new2"], source="vlm")
    rows = dict(_read_tag_rows(db, p))
    assert rows == {"new1": "vlm", "new2": "vlm"}


def test_add_clip_tags_with_existing_prompt_unchanged_by_design(db):
    """Existing behavior: prompt × clip → 'both' (clip+prompt). Don't break."""
    p = Path("/img/a.jpg")
    _register_path(db, p)
    db.add_tag_indices(p, ["red"], source="prompt")
    db.add_tag_indices(p, ["red"], source="clip")
    rows = dict(_read_tag_rows(db, p))
    assert rows["red"] == "both"
