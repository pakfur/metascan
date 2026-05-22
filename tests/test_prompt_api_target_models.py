"""Endpoint test for GET /api/prompt/target-models."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.dependencies as deps
from backend.api import prompt as prompt_api
from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def client():
    """Bare TestClient — this endpoint touches no DB, but we still need
    a valid singleton so backend.dependencies stays consistent during
    the test."""
    saved = deps._db_singleton
    with tempfile.TemporaryDirectory() as d:
        deps._db_singleton = DatabaseManager(Path(d))
        app = FastAPI()
        app.include_router(prompt_api.router)
        try:
            with TestClient(app) as c:
                yield c
        finally:
            deps._db_singleton = saved


def test_target_models_returns_canonical_seven(client: TestClient):
    r = client.get("/api/prompt/target-models")
    assert r.status_code == 200
    assert r.json() == {
        "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
    }
