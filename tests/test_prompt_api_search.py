"""Endpoint tests for POST /api/prompt/search.

Reuses the client_with_db pattern from test_prompt_api_crud.py — we
override backend.dependencies._db_singleton directly because get_db is
a singleton accessor, not a FastAPI dependency-injected function.
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
def client_with_db():
    """Same shape as tests/test_prompt_api_crud.py — yields (TestClient, DB)
    backed by a temp SQLite. Restores the singleton on teardown."""
    saved = deps._db_singleton
    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(Path(d))
        for path in ("/data/a/img1.png", "/data/a/img2.png"):
            db.save_media(_media(path))
        db.create_folder(
            "fld_a",
            kind="manual",
            name="A",
            items=["/data/a/img1.png", "/data/a/img2.png"],
        )
        db.create_folder("fld_smart", kind="smart", name="Smart", rules={"any": []})
        db.save_prompt(
            file_path="/data/a/img1.png",
            name="hero",
            prompt="p1",
            target_model="qwen",
            architecture="qwen",
            styles=[],
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            mode="generate",
            negative=None,
            vlm_model_id=None,
        )
        db.save_prompt(
            file_path="/data/a/img2.png",
            name="cinematic",
            prompt="p2",
            target_model="sd",
            architecture="sd",
            styles=[],
            temperature=None,
            max_tokens=None,
            source_prompt=None,
            mode="generate",
            negative="n2",
            vlm_model_id=None,
        )
        deps._db_singleton = db
        app = FastAPI()
        app.include_router(prompt_api.router)
        try:
            with TestClient(app) as c:
                yield c, db
        finally:
            deps._db_singleton = saved


def test_search_returns_matching_rows(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": "fld_a",
            "target_model": "qwen",
            "name": None,
            "limit": 100,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["prompts"]) == 1
    assert body["prompts"][0]["name"] == "hero"


def test_search_with_all_null_filters_returns_everything(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": None,
            "target_model": None,
            "name": None,
            "limit": 100,
        },
    )
    assert r.status_code == 200
    assert len(r.json()["prompts"]) == 2


def test_search_smart_folder_returns_400(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": "fld_smart",
            "target_model": None,
            "name": None,
            "limit": 100,
        },
    )
    assert r.status_code == 400
    assert "smart" in r.json().get("detail", "").lower()


def test_search_unknown_folder_returns_400(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": "fld_missing",
            "target_model": None,
            "name": None,
            "limit": 100,
        },
    )
    assert r.status_code == 400


def test_search_limit_default_is_100(client_with_db):
    """Pydantic default fills in when the field is omitted."""
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": None,
            "target_model": None,
            "name": None,
        },
    )
    assert r.status_code == 200


def test_search_limit_capped_at_500(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": None,
            "target_model": None,
            "name": None,
            "limit": 10_000,
        },
    )
    # 422 from Pydantic ge/le validator — not a 200 with silent cap.
    # Server enforces the spec contract loudly.
    assert r.status_code == 422


def test_search_response_includes_negative_field(client_with_db):
    c, _ = client_with_db
    r = c.post(
        "/api/prompt/search",
        json={
            "folder_id": None,
            "target_model": "sd",
            "name": None,
            "limit": 100,
        },
    )
    assert r.status_code == 200
    prompts = r.json()["prompts"]
    assert len(prompts) == 1
    assert prompts[0]["negative"] == "n2"
