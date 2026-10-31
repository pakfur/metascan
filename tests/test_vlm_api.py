"""TestClient coverage of the /api/vlm endpoints."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.api import vlm as vlm_api


def _make_stub() -> MagicMock:
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
    return stub


@pytest.fixture
def app_with_stub_vlm():
    """Yields (TestClient, stub).

    The stub is installed *after* the lifespan runs so it supersedes the
    real VlmClient constructed in the lifespan.  Tests use the returned
    TestClient directly rather than opening their own context.
    """
    app = create_app()
    stub = _make_stub()
    client = TestClient(app)
    client.__enter__()
    # Install stub after lifespan has run (lifespan sets the real client).
    vlm_api.set_vlm_client(stub)
    yield client, stub
    vlm_api.set_vlm_client(None)
    client.__exit__(None, None, None)


def test_status_returns_snapshot(app_with_stub_vlm):
    c, _ = app_with_stub_vlm
    r = c.get("/api/vlm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ready"
    assert body["model_id"] == "qwen3vl-4b"


def test_status_returns_idle_when_unset():
    vlm_api.set_vlm_client(None)
    app = create_app()
    with TestClient(app) as c:
        # Lifespan installs real VlmClient (state "idle"); clear it so the
        # endpoint returns the unset-client response shape.
        vlm_api.set_vlm_client(None)
        r = c.get("/api/vlm/status")
    assert r.status_code == 200
    assert r.json()["state"] == "idle"


def test_tag_endpoint_returns_tags(app_with_stub_vlm, tmp_path: Path):
    c, stub = app_with_stub_vlm
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    r = c.post("/api/vlm/tag", json={"path": str(img)})
    assert r.status_code == 200
    assert r.json() == {"tags": ["red", "blue"]}
    stub.ensure_started.assert_awaited()
    stub.generate_tags.assert_awaited()


def test_tag_endpoint_404_for_missing_file(app_with_stub_vlm):
    c, _ = app_with_stub_vlm
    r = c.post("/api/vlm/tag", json={"path": "/no/such/file.jpg"})
    assert r.status_code == 404


def test_tag_endpoint_503_when_no_client():
    app = create_app()
    with TestClient(app) as c:
        # Clear the client installed by the lifespan to exercise the 503 path.
        vlm_api.set_vlm_client(None)
        r = c.post("/api/vlm/tag", json={"path": "/x.jpg"})
    assert r.status_code == 503


@pytest.fixture
def queue_db(tmp_path: Path, monkeypatch):
    """A temp library with two images; the retag route reads this DB."""
    from datetime import datetime

    from metascan.core.database_sqlite import DatabaseManager
    from metascan.core.media import Media

    db = DatabaseManager(tmp_path / "db")
    paths = []
    for name in ("a.jpg", "b.jpg"):
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff\xd9")
        db.save_media(
            Media(
                file_path=p,
                file_size=4,
                width=1,
                height=1,
                format="jpeg",
                created_at=datetime(2024, 1, 1),
                modified_at=datetime(2024, 1, 1),
            )
        )
        paths.append(p)
    monkeypatch.setattr(vlm_api, "get_db", lambda: db)
    # The drain is covered by test_vlm_tag_pump; keep it off here.
    from backend.api import similarity

    monkeypatch.setattr(
        similarity, "_schedule_vlm_drain_after_complete", lambda *a, **k: None
    )
    return db, paths


def test_retag_returns_job_id(app_with_stub_vlm, queue_db):
    c, _ = app_with_stub_vlm
    db, (a, b) = queue_db
    r = c.post(
        "/api/vlm/retag",
        json={"scope": "paths", "paths": [str(a), str(b)]},
    )
    assert r.status_code == 202
    assert "job_id" in r.json()
    assert r.json()["total"] == 2
    assert db.tag_queue_count() == 2


def test_retag_cancel_endpoint(app_with_stub_vlm, queue_db):
    c, _ = app_with_stub_vlm
    db, (a, b) = queue_db
    db.enqueue_tags([b])  # queued by someone else: not this job's to drop
    r = c.post(
        "/api/vlm/retag",
        json={"scope": "paths", "paths": [str(a), str(b)]},
    )
    job_id = r.json()["job_id"]
    r2 = c.delete(f"/api/vlm/retag/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"
    assert db.next_tag_batch(10) == [str(b)]


async def test_active_endpoint_calls_swap(app_with_stub_vlm):
    c, stub = app_with_stub_vlm
    stub.swap_model = AsyncMock()
    # /active refuses a model that is not installed (409); this host may
    # have no weights.
    with patch.object(vlm_api, "vlm_model_installed", return_value=True):
        r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-8b"})
    assert r.status_code == 200
    stub.swap_model.assert_awaited_with("qwen3vl-8b")


def test_active_endpoint_400_on_unknown_model(app_with_stub_vlm):
    c, _ = app_with_stub_vlm
    r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-bogus"})
    assert r.status_code == 400


def test_unload_of_the_general_model_is_an_owner_pause_of_tagging(app_with_stub_vlm):
    c, stub = app_with_stub_vlm
    stub.shutdown = AsyncMock(return_value=None)
    r = c.post("/api/vlm/unload", json={"model_id": "qwen3vl-4b"})
    assert r.status_code == 200
    stub.shutdown.assert_awaited_once()
    st = next(
        p for p in c.get("/api/background").json()["processes"] if p["id"] == "tagging"
    )
    assert st["state"] == "paused"
    assert st["activity"] == "Paused: model unloaded"
    # Leave no persisted intent behind for the next test's lifespan.
    c.post("/api/background/tagging/resume")


def test_a_forced_retag_clears_the_exhausted_mark(app_with_stub_vlm, queue_db):
    c, _ = app_with_stub_vlm
    db, (a, b) = queue_db
    db.mark_tag_exhausted(a)
    assert db.images_without_vlm_tags(for_hygiene=True) != []
    assert len(db.images_without_vlm_tags(for_hygiene=True)) == 1  # only b
    r = c.post(
        "/api/vlm/retag",
        json={"scope": "paths", "paths": [str(a)], "force": True},
    )
    assert r.status_code == 202
    assert len(db.images_without_vlm_tags(for_hygiene=True)) == 2
