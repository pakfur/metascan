"""Endpoint tests for the saved-prompt CRUD surface.

``get_db`` is a singleton accessor (not a FastAPI dependency-injected
function), so the override pattern is to set ``backend.dependencies.
_db_singleton`` directly to a temp DB. This mirrors
``tests/test_folders_api.py:setUp``.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.dependencies as deps
from backend.api import prompt as prompt_api
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def client_with_db():
    """Yield (TestClient, DB) backed by a temp SQLite file. Restores the
    singleton on teardown so other tests see no spillover."""
    saved = deps._db_singleton
    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(Path(d))
        db.save_media(
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
        deps._db_singleton = db
        app = FastAPI()
        app.include_router(prompt_api.router)
        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            deps._db_singleton = saved


def test_save_then_list_returns_inserted_row(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/save",
        json={
            "file_path": "/tmp/img.jpg",
            "name": "my anime variant",
            "prompt": "masterpiece, anime girl",
            "target_model": "sdxl",
            "architecture": "t2i",
            "styles": ["anime"],
            "temperature": 0.6,
            "max_tokens": 250,
            "source_prompt": None,
            "mode": "generate",
            "negative": None,
            "vlm_model_id": "qwen3vl-4b",
        },
    )
    assert r.status_code == 200
    new_id = r.json()["id"]

    r2 = c.get(
        "/api/prompt/by-image",
        params={"file_path": "/tmp/img.jpg"},
    )
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["id"] == new_id
    assert rows[0]["name"] == "my anime variant"
    assert rows[0]["styles"] == ["anime"]


def test_delete_removes_row(client_with_db):
    c, _ = client_with_db
    new_id = c.post(
        "/api/prompt/save",
        json={
            "file_path": "/tmp/img.jpg",
            "name": "x",
            "prompt": "p",
            "target_model": "sdxl",
            "architecture": "t2i",
            "styles": [],
            "temperature": 0.6,
            "max_tokens": 250,
            "source_prompt": None,
            "mode": "generate",
            "negative": None,
            "vlm_model_id": None,
        },
    ).json()["id"]

    r = c.delete(f"/api/prompt/{new_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"

    r2 = c.delete(f"/api/prompt/{new_id}")
    assert r2.status_code == 404
