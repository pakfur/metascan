"""Verify Qwen3-VL rows appear in /api/models/status."""

from fastapi.testclient import TestClient

from backend.main import create_app


def test_models_status_includes_vlm_rows():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    assert r.status_code == 200
    rows = r.json()["models"]
    ids = [m["id"] for m in rows]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in ids

    vlm_rows = [r for r in rows if r.get("is_vlm")]
    assert {r["id"] for r in vlm_rows} == {
        "qwen3vl-2b",
        "qwen3vl-4b",
        "qwen3vl-8b",
        "qwen3vl-30b-a3b",
        "qwen38-27b",
    }


def test_models_status_includes_vlm_gates():
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/models/status")
    gates = r.json()["gates"]
    for mid in ("qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"):
        assert mid in gates
