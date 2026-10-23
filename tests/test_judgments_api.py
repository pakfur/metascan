"""REST tests for /api/judgments."""

from tests._taste_api_fixtures import client, seeded  # noqa: F401


def test_patch_verdict_roundtrip(client, seeded):
    r = client.patch("/api/judgments//p/a.png", json={"verdict": "pass"})
    assert r.status_code == 200 and r.json()["verdict"] == "pass"
    assert r.json()["is_favorite"] is False


def test_reason_without_verdict_is_409(client, seeded):
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_verdict"


def test_unknown_reason_is_422(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"verdict": "pass"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "zzz", "on": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_reason"


def test_reason_roundtrip(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"verdict": "favorite"})
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 200 and r.json()["reasons"] == ["light"]


def test_both_verdict_and_reason_is_422(client, seeded):
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"verdict": "pass", "reason": "light", "on": True},
    )
    assert r.status_code == 422


def test_neither_verdict_nor_reason_is_422(client, seeded):
    assert client.patch("/api/judgments//p/a.png", json={}).status_code == 422


def test_null_verdict_clears(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"verdict": "favorite"})
    r = client.patch("/api/judgments//p/a.png", json={"verdict": None})
    assert r.status_code == 200 and r.json()["verdict"] is None


def test_seen_and_get(client, seeded):
    assert client.post("/api/judgments/seen", json={"file_path": "/p/a.png"}).json()[
        "seen_at"
    ]
    assert client.get("/api/judgments//p/a.png").json()["seen_at"]


def test_unknown_media_404(client):
    assert client.get("/api/judgments//nope.png").status_code == 404
    assert (
        client.post("/api/judgments/seen", json={"file_path": "/nope.png"}).status_code
        == 404
    )
    assert (
        client.patch("/api/judgments//nope.png", json={"verdict": "pass"}).status_code
        == 404
    )


def test_sticky_category_via_verdict(client, seeded):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"verdict": "favorite", "sticky_category_id": cid},
    )
    assert r.json()["categories"] == [{"id": cid, "name": "Anime"}]


def test_unknown_sticky_category_404(client, seeded):
    r = client.patch(
        "/api/judgments//p/a.png",
        json={"verdict": "favorite", "sticky_category_id": 999},
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_category"


def test_sticky_broadcasts_once(client, seeded, monkeypatch):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    sent = []
    monkeypatch.setattr(
        "backend.api.judgments.ws_manager.broadcast_sync", lambda *a: sent.append(a)
    )
    body = {"verdict": "favorite", "sticky_category_id": cid}
    client.patch("/api/judgments//p/a.png", json=body)
    client.patch("/api/judgments//p/a.png", json=body)
    assert sent == [("categories", "categories_changed", {})]


def test_legacy_media_patch_still_works(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"verdict": "pass"})
    client.patch("/api/media//p/a.png", json={"is_favorite": False})
    assert client.get("/api/judgments//p/a.png").json()["verdict"] == "pass"


class _FakeFaiss:
    def __init__(self, loaded, vec_for):
        self.is_loaded = loaded
        self.meta = {"model_key": "small"}
        self._vec_for = vec_for

    def get_embedding(self, key):
        return self._vec_for.get(key)


def test_faiss_embedding_lookup(monkeypatch):
    import numpy as np

    from backend.services.judgments_service import faiss_embedding_lookup

    vec = np.ones(32, dtype=np.float32)
    fake = _FakeFaiss(True, {"/p/a.png": vec})
    monkeypatch.setattr("backend.api.similarity._faiss_manager", fake)
    got = faiss_embedding_lookup("/p/a.png")
    assert got is not None and got[1] == "small" and got[0] is vec
    assert faiss_embedding_lookup("/p/missing.png") is None
    fake.is_loaded = False
    assert faiss_embedding_lookup("/p/a.png") is None


def test_faiss_embedding_lookup_never_constructs_or_loads(monkeypatch):
    from backend.services.judgments_service import faiss_embedding_lookup

    def boom(*a, **k):
        raise AssertionError("must not construct/load")

    monkeypatch.setattr("backend.api.similarity._faiss_manager", None)
    monkeypatch.setattr("backend.api.similarity._get_faiss_manager", boom)
    monkeypatch.setattr("backend.api.similarity._get_embedding_queue", boom)
    monkeypatch.setattr("backend.api.similarity.FaissIndexManager", boom)
    assert faiss_embedding_lookup("/p/a.png") is None


def test_faiss_embedding_lookup_failure_logs_warning(monkeypatch, caplog):
    from backend.services.judgments_service import faiss_embedding_lookup

    class Broken:
        is_loaded = True
        meta = {"model_key": "small"}

        def get_embedding(self, key):
            raise RuntimeError("bad index")

    monkeypatch.setattr("backend.api.similarity._faiss_manager", Broken())
    with caplog.at_level("WARNING"):
        assert faiss_embedding_lookup("/p/a.png") is None
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_seen_records_the_score_shown(client, seeded):
    from backend.dependencies import get_db

    r = client.post(
        "/api/judgments/seen", json={"file_path": "/p/a.png", "score_shown": 0.42}
    )
    assert r.status_code == 200 and r.json()["seen_at"]
    events = get_db().list_judgment_events("/p/a.png")
    seen = [e for e in events if e["event"] == "seen"]
    assert len(seen) == 1 and seen[0]["score_shown"] == 0.42


def test_seen_without_score_stores_null(client, seeded):
    from backend.dependencies import get_db

    client.post("/api/judgments/seen", json={"file_path": "/p/a.png"})
    seen = [
        e for e in get_db().list_judgment_events("/p/a.png") if e["event"] == "seen"
    ]
    assert len(seen) == 1 and seen[0]["score_shown"] is None
