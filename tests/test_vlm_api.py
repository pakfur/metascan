"""TestClient coverage of the /api/vlm endpoints."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


@pytest.fixture
def app_with_stub_vlm():
    app = create_app()
    stub = MagicMock()
    stub.model_id = "qwen3vl-4b"
    stub.snapshot.return_value = {
        "state": "ready",
        "model_id": "qwen3vl-4b",
        "base_url": "http://127.0.0.1:9999",
        "progress": {},
        "error": None,
    }
    stub.generate_tags = AsyncMock(return_value=["red", "blue"])
    stub.ensure_started = AsyncMock(return_value=None)
    vlm_api.set_vlm_client(stub)
    yield app, stub
    vlm_api.set_vlm_client(None)


def test_status_returns_snapshot(app_with_stub_vlm):
    app, _ = app_with_stub_vlm
    with TestClient(app) as c:
        r = c.get("/api/vlm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ready"
    assert body["model_id"] == "qwen3vl-4b"


def test_status_returns_idle_when_unset():
    vlm_api.set_vlm_client(None)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/vlm/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_tag_endpoint_returns_tags(app_with_stub_vlm, tmp_path: Path):
    app, stub = app_with_stub_vlm
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": str(img)})
    assert r.status_code == 200
    assert r.json() == {"tags": ["red", "blue"]}
    stub.ensure_started.assert_awaited()
    stub.generate_tags.assert_awaited()


def test_tag_endpoint_404_for_missing_file(app_with_stub_vlm):
    app, _ = app_with_stub_vlm
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": "/no/such/file.jpg"})
    assert r.status_code == 404


def test_tag_endpoint_503_when_no_client():
    vlm_api.set_vlm_client(None)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/vlm/tag", json={"path": "/x.jpg"})
    assert r.status_code == 503


def test_retag_returns_job_id(app_with_stub_vlm, tmp_path: Path):
    app, _ = app_with_stub_vlm
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\xff\xd8\xff\xd9")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post(
            "/api/vlm/retag",
            json={"scope": "paths", "paths": [str(a), str(b)]},
        )
    assert r.status_code == 202
    assert "job_id" in r.json()
    assert r.json()["total"] == 2


def test_retag_cancel_endpoint(app_with_stub_vlm, tmp_path: Path):
    app, _ = app_with_stub_vlm
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\xff\xd8\xff\xd9")
    with TestClient(app) as c:
        r = c.post(
            "/api/vlm/retag",
            json={"scope": "paths", "paths": [str(a)]},
        )
        job_id = r.json()["job_id"]
        r2 = c.delete(f"/api/vlm/retag/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"
