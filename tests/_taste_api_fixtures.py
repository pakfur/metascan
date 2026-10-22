"""Shared fixtures for the judgments/categories REST tests."""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def client(tmp_path):
    os.environ.setdefault("METASCAN_API_KEY", "")
    db = DatabaseManager(tmp_path)
    for p in ["/p/a.png", "/p/b.png"]:
        db.save_media(
            Media(
                file_path=Path(p),
                file_size=1,
                width=1,
                height=1,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
    import backend.dependencies as deps

    old = deps._db_singleton
    deps._db_singleton = db  # type: ignore[attr-defined]
    with patch("backend.dependencies.get_data_dir", return_value=tmp_path):
        from backend.api import categories, judgments, media

        app = FastAPI()
        for r in (media.router, judgments.router, categories.router):
            app.include_router(r)
        yield TestClient(app)
    deps._db_singleton = old  # type: ignore[attr-defined]


@pytest.fixture
def seeded(client):
    return client
