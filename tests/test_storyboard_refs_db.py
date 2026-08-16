"""Second subject reference + scene setting reference (spec V2 §3)."""

from __future__ import annotations

import sqlite3
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
def board(db):
    return db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )


def _add_media(db, posix_path):
    # Copied from tests/test_storyboard_db.py's `_media()` helper +
    # save_media() call -- satisfies the media FK prerequisite for
    # reference_path / reference_path_2 columns.
    db.save_media(
        Media(
            file_path=Path(posix_path),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )


def test_subject_second_reference_roundtrip_posix(db, board):
    # save_media() POSIX-normalizes on write, and a colon-drive path
    # normalizes to /mnt/<drive>/... regardless of its original slash
    # style (see to_posix_path) -- use an already-normalized path here so
    # the stored media row matches what create_subject's own
    # normalization of the backslash form below produces.
    _add_media(db, "/mnt/c/pics/a.png")
    sid = db.create_subject(
        board,
        name="Maya",
        description="d",
        reference_path_2="C:\\pics\\a.png",
    )
    subj = db.get_subject(sid)
    assert subj["reference_path_2"] == "/mnt/c/pics/a.png"  # stored POSIX
    db.update_subject(sid, reference_path_2=None)
    assert db.get_subject(sid)["reference_path_2"] is None


def test_scene_reference_roundtrip_and_set_null(db, board):
    _add_media(db, "/mnt/c/pics/set.png")
    scene_id = db.create_scene(board, name="Yard", reference_path="C:\\pics\\set.png")
    assert db.get_scene(scene_id)["reference_path"] == "/mnt/c/pics/set.png"
    # deleting the media row nulls the reference, not the scene
    assert db.delete_media(Path("/mnt/c/pics/set.png")) is True
    assert db.get_scene(scene_id)["reference_path"] is None


def test_unknown_reference_raises_integrity_error(db, board):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_scene(board, name="Yard", reference_path="C:/nope.png")
    sid = db.create_subject(board, name="M", description="d")
    with pytest.raises(sqlite3.IntegrityError):
        db.update_subject(sid, reference_path_2="C:/nope.png")


def test_get_subject_get_scene_unknown_ids(db):
    assert db.get_subject(999) is None
    assert db.get_scene(999) is None
