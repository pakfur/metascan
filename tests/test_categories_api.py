"""REST tests for /api/categories."""

from tests._taste_api_fixtures import client, seeded  # noqa: F401


def test_category_crud_and_items(client, seeded):
    cid = client.post("/api/categories", json={"name": "Sci-Fi"}).json()["id"]
    assert client.post("/api/categories", json={"name": "sci-fi"}).status_code == 409
    assert client.post(
        f"/api/categories/{cid}/items",
        json={"file_paths": ["/p/a.png"], "source": "bulk"},
    ).json() == {"added": 1}
    assert client.get("/api/categories").json()[0]["item_count"] == 1
    r = client.request(
        "DELETE", f"/api/categories/{cid}/items", json={"file_paths": ["/p/a.png"]}
    )
    assert r.json() == {"status": "deleted", "removed": 1}
    assert client.delete(f"/api/categories/{cid}").json() == {"status": "deleted"}


def test_validation_codes(client):
    r = client.post("/api/categories", json={"name": "  "})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_name"
    client.post("/api/categories", json={"name": "A"})
    r = client.post("/api/categories", json={"name": "a"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_name"


def test_rename_and_unknown(client):
    cid = client.post("/api/categories", json={"name": "A"}).json()["id"]
    assert client.patch(f"/api/categories/{cid}", json={"name": "B"}).json() == {
        "id": cid,
        "name": "B",
    }
    assert client.patch("/api/categories/999", json={"name": "C"}).status_code == 404
    assert client.delete("/api/categories/999").status_code == 404
    r = client.post(
        "/api/categories/999/items", json={"file_paths": [], "source": "viewer"}
    )
    assert r.status_code == 404


def test_internal_sources_rejected(client, seeded):
    cid = client.post("/api/categories", json={"name": "A"}).json()["id"]
    for src in ("generation", "sticky"):
        r = client.post(
            f"/api/categories/{cid}/items",
            json={"file_paths": ["/p/a.png"], "source": src},
        )
        assert r.status_code == 422


def test_mutation_broadcasts(client, seeded, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "backend.api.categories.ws_manager.broadcast_sync", lambda *a: sent.append(a)
    )
    client.post("/api/categories", json={"name": "A"})
    assert sent == [("categories", "categories_changed", {})]
