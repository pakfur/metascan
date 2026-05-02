"""Verify Qwen3-VL rows appear in /api/models/status."""

from fastapi.testclient import TestClient

from backend.main import create_app


def test_models_status_includes_vlm_rows():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["models"]]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in ids


def test_models_status_includes_vlm_gates():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    gates = r.json()["gates"]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in gates
