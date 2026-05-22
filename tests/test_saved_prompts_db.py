"""CRUD tests for the saved_prompts table.

Asserts side-channel discipline: saving a prompt does NOT touch the
indices table, so tag-search behavior is unaffected by playground use.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        m = DatabaseManager(Path(d))
        # Minimal media row so the FK passes (see tests/test_folders_api.py).
        m.save_media(
            Media(
                file_path=Path("/tmp/img.jpg"),
                file_size=1,
                width=1,
                height=1,
                format="jpg",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        yield m


def test_save_then_list_returns_inserted_row(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="anime variant",
        prompt="masterpiece, anime girl, blue eyes",
        target_model="sdxl",
        architecture="t2i",
        styles=["anime", "cinematic"],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id="qwen3vl-4b",
    )
    assert isinstance(new_id, int) and new_id > 0
    rows = db.list_saved_prompts("/tmp/img.jpg")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == new_id
    assert r["name"] == "anime variant"
    assert r["styles"] == ["anime", "cinematic"]  # JSON-decoded
    assert r["mode"] == "generate"
    assert r["negative"] is None


def test_list_returns_newest_first(db):
    a = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="a",
        prompt="p1",
        target_model="sdxl",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )
    b = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="b",
        prompt="p2",
        target_model="flux-chroma",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )
    rows = db.list_saved_prompts("/tmp/img.jpg")
    assert [r["id"] for r in rows] == [b, a]  # DESC by created_at / id


def test_get_returns_single_row_or_none(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="x",
        prompt="p",
        target_model="sdxl",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )
    assert db.get_saved_prompt(new_id)["id"] == new_id
    assert db.get_saved_prompt(99999) is None


def test_delete_returns_bool_and_removes_row(db):
    new_id = db.save_prompt(
        file_path="/tmp/img.jpg",
        name="x",
        prompt="p",
        target_model="sdxl",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )
    assert db.delete_saved_prompt(new_id) is True
    assert db.delete_saved_prompt(new_id) is False  # idempotent / missing
    assert db.list_saved_prompts("/tmp/img.jpg") == []


def test_save_does_not_touch_indices_table(db):
    """Side-channel discipline: saving a prompt must NOT emit tag rows.

    The fixture's save_media call already writes non-tag indices (ext, path).
    We snapshot that count before calling save_prompt and verify it is
    unchanged afterwards — proving save_prompt is a pure side-channel write
    that never touches the inverted index.
    """
    with db.lock, db._get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM indices WHERE file_path=?",
            ("/tmp/img.jpg",),
        ).fetchone()["n"]

    db.save_prompt(
        file_path="/tmp/img.jpg",
        name="x",
        prompt="cyberpunk neon city, raining, octane render",
        target_model="sdxl",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )

    with db.lock, db._get_connection() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM indices WHERE file_path=?",
            ("/tmp/img.jpg",),
        ).fetchone()["n"]
    assert after == before  # save_prompt must not add any index rows


def test_delete_media_cascades_saved_prompts(db):
    db.save_prompt(
        file_path="/tmp/img.jpg",
        name="x",
        prompt="p",
        target_model="sdxl",
        architecture="t2i",
        styles=[],
        temperature=0.6,
        max_tokens=250,
        source_prompt=None,
        mode="generate",
        negative=None,
        vlm_model_id=None,
    )
    db.delete_media(Path("/tmp/img.jpg"))
    assert db.list_saved_prompts("/tmp/img.jpg") == []
