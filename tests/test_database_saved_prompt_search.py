"""Tests for DatabaseManager.search_saved_prompts.

Uses ephemeral DB + Media seeding via db.save_media (NOT
insert_or_update_media — that method doesn't exist). DatabaseManager
takes a Path, not a string.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=1,
        height=1,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        manager = DatabaseManager(Path(d))
        # Seed 3 media rows — saved_prompts.file_path REFERENCES media.file_path.
        for p in ("/data/a/img1.png", "/data/a/img2.png", "/data/b/img3.png"):
            manager.save_media(_media(p))

        # Two manual folders, one smart.
        manager.create_folder(
            "fld_a",
            kind="manual",
            name="A",
            items=["/data/a/img1.png", "/data/a/img2.png"],
        )
        manager.create_folder(
            "fld_b", kind="manual", name="B", items=["/data/b/img3.png"]
        )
        manager.create_folder(
            "fld_smart", kind="smart", name="Smart", rules={"any": []}
        )

        # Saved prompts: 2 against fld_a/img1 (one qwen, one sd), 1 against fld_a/img2 (qwen),
        # 1 against fld_b/img3 (qwen).
        manager.save_prompt(
            file_path="/data/a/img1.png",
            name="hero",
            prompt="p1",
            target_model="qwen",
            architecture="qwen",
            styles=[],
            mode="generate",
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            negative=None,
            vlm_model_id=None,
        )
        manager.save_prompt(
            file_path="/data/a/img1.png",
            name="alt",
            prompt="p2",
            target_model="sd",
            architecture="sd",
            styles=[],
            mode="generate",
            negative="n2",
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            vlm_model_id=None,
        )
        manager.save_prompt(
            file_path="/data/a/img2.png",
            name="cinematic",
            prompt="p3",
            target_model="qwen",
            architecture="qwen",
            styles=[],
            mode="generate",
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            negative=None,
            vlm_model_id=None,
        )
        manager.save_prompt(
            file_path="/data/b/img3.png",
            name="landscape",
            prompt="p4",
            target_model="qwen",
            architecture="qwen",
            styles=[],
            mode="generate",
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            negative=None,
            vlm_model_id=None,
        )
        yield manager


def test_search_no_filters_returns_all(db):
    rows = db.search_saved_prompts(
        folder_id=None, target_model=None, name=None, limit=100
    )
    assert len(rows) == 4


def test_search_by_folder_returns_only_that_folders_prompts(db):
    rows = db.search_saved_prompts(
        folder_id="fld_a", target_model=None, name=None, limit=100
    )
    names = sorted(r["name"] for r in rows)
    assert names == ["alt", "cinematic", "hero"]


def test_search_by_target_model_filters_globally(db):
    rows = db.search_saved_prompts(
        folder_id=None, target_model="qwen", name=None, limit=100
    )
    names = sorted(r["name"] for r in rows)
    assert names == ["cinematic", "hero", "landscape"]


def test_search_by_name_exact_match(db):
    rows = db.search_saved_prompts(
        folder_id=None, target_model=None, name="hero", limit=100
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "hero"


def test_search_combining_filters(db):
    rows = db.search_saved_prompts(
        folder_id="fld_a", target_model="qwen", name=None, limit=100
    )
    names = sorted(r["name"] for r in rows)
    assert names == ["cinematic", "hero"]


def test_search_smart_folder_raises(db):
    with pytest.raises(ValueError, match="smart"):
        db.search_saved_prompts(
            folder_id="fld_smart", target_model=None, name=None, limit=100
        )


def test_search_unknown_folder_raises(db):
    with pytest.raises(ValueError, match="not found|unknown"):
        db.search_saved_prompts(
            folder_id="fld_does_not_exist", target_model=None, name=None, limit=100
        )


def test_limit_caps_returned_rows(db):
    rows = db.search_saved_prompts(
        folder_id=None, target_model=None, name=None, limit=2
    )
    assert len(rows) == 2


def test_returned_rows_include_negative_field(db):
    rows = db.search_saved_prompts(
        folder_id=None, target_model="sd", name=None, limit=100
    )
    assert len(rows) == 1
    assert rows[0]["negative"] == "n2"


def test_returned_rows_have_styles_deserialized_to_list(db):
    """Mirror list_saved_prompts: styles column is stored as JSON string,
    returned as a Python list. The list_saved_prompts row shape is the
    canonical contract; search must match."""
    rows = db.search_saved_prompts(
        folder_id=None, target_model="qwen", name="hero", limit=10
    )
    assert len(rows) == 1
    assert rows[0]["styles"] == []
    assert isinstance(rows[0]["styles"], list)
