"""Route contracts for compose + beats (spec V1 5).

Reuses the client/db/StubRunner fixture machinery from
tests/test_storyboard_api.py (same app factory, same runner stubbing via
monkeypatching backend.main.StoryboardRunner) so runner-backed compose
routes go through a stub while beat/PATCH routes go through the real
StoryboardService + a real temp DatabaseManager.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, List

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.main import create_app
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import ConfirmRequiredError, StoryboardError


class StubRunner:
    """Records compose calls; also stands in for StoryboardRunner in the
    lifespan (constructor + on_event/handle_job_event/aclose required)."""

    def __init__(self, db=None, comfy=None, get_vlm=None, output_root=None, **kwargs):
        self.db = db
        self.compose_calls: List[Any] = []
        self.gate_error: Any = None

    def on_event(self, cb):
        pass

    def handle_job_event(self, event, payload):
        pass

    async def check_compose_gates(self, sb_id, stages, scene_ids, confirm):
        if self.gate_error:
            raise self.gate_error

    async def compose_story(self, sb_id, **kw):
        self.compose_calls.append((sb_id, kw))
        return {"outline": 1}

    async def aclose(self):
        return None


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp))
        stubs: List[StubRunner] = []

        def make_stub(*args, **kwargs):
            stub = StubRunner(db=db)
            stubs.append(stub)
            return stub

        monkeypatch.setattr("backend.dependencies.get_db", lambda: db)
        monkeypatch.setattr("backend.api.storyboard.get_db", lambda: db)
        monkeypatch.setattr("backend.main.StoryboardRunner", make_stub)

        app = create_app()
        with TestClient(app) as c:
            assert stubs, "lifespan did not construct a StoryboardRunner"
            c.stub = stubs[0]
            c.db = db
            yield c
        storyboard_api.set_storyboard_runner(None)


@pytest.fixture
def stub_runner(client):
    return client.stub


@pytest.fixture
def board_id(client) -> int:
    r = client.post(
        "/api/storyboard",
        json={"name": "My Storyboard", "target_model": "sd", "aspect_ratio": "16:9"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def panel_id(client, board_id) -> int:
    scene_id = client.post(
        f"/api/storyboard/{board_id}/scenes", json={"name": "Scene 1"}
    ).json()["id"]
    return client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "walks in"}
    ).json()["id"]


@pytest.fixture
def subject_id(client, board_id) -> int:
    r = client.post(
        f"/api/storyboard/{board_id}/subjects",
        json={"name": "Alice", "description": "the protagonist"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---- compose ----------------------------------------------------------------


def test_compose_returns_202_and_fires_task(client, stub_runner, board_id):
    r = client.post(f"/api/storyboard/{board_id}/compose", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started"}


def test_compose_409_on_confirm_required(client, stub_runner, board_id):
    stub_runner.gate_error = ConfirmRequiredError("has outline")
    r = client.post(f"/api/storyboard/{board_id}/compose", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirm_required"


def test_compose_400_on_unknown_stage(client, stub_runner, board_id):
    stub_runner.gate_error = StoryboardError("unknown stages: bogus")
    r = client.post(f"/api/storyboard/{board_id}/compose", json={"stages": ["bogus"]})
    assert r.status_code == 400


def test_compose_without_runner_is_503(client, board_id):
    storyboard_api.set_storyboard_runner(None)
    r = client.post(f"/api/storyboard/{board_id}/compose", json={})
    assert r.status_code == 503


# ---- beats --------------------------------------------------------------------


def test_beat_crud_routes(client, panel_id):
    r = client.post(
        f"/api/storyboard/panels/{panel_id}/beats",
        json={"action": "she kneels", "duration_s": 3.0},
    )
    assert r.status_code == 200
    bid = r.json()["id"]
    r = client.patch(
        f"/api/storyboard/beats/{bid}",
        json={
            "action": "she stands",
            "dialog": [
                {
                    "subject_id": None,
                    "voice": "low voice",
                    "delivery": None,
                    "language": "English",
                    "text": "Up.",
                }
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["action"] == "she stands"
    assert r.json()["dialog"][0]["text"] == "Up."
    r = client.patch(f"/api/storyboard/beats/{bid}", json={"action": None})
    assert r.status_code == 400  # null for NOT NULL column
    r = client.delete(f"/api/storyboard/beats/{bid}")
    assert r.json() == {"status": "deleted"}
    assert client.delete(f"/api/storyboard/beats/{bid}").status_code == 404


def test_beat_create_404_on_unknown_panel(client):
    r = client.post("/api/storyboard/panels/999999/beats", json={"action": "x"})
    assert r.status_code == 404


def test_patch_unknown_beat_is_404(client):
    r = client.patch("/api/storyboard/beats/999999", json={"action": "x"})
    assert r.status_code == 404


def test_delete_unknown_beat_is_404(client):
    assert client.delete("/api/storyboard/beats/999999").status_code == 404


# ---- PATCH extensions ---------------------------------------------------------


def test_patch_extensions(client, board_id, panel_id, subject_id):
    assert (
        client.patch(
            f"/api/storyboard/{board_id}", json={"outline": '{"logline": "x"}'}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/storyboard/panels/{panel_id}", json={"duration_s": 9.5}
        ).json()["duration_s"]
        == 9.5
    )
    assert (
        client.patch(
            f"/api/storyboard/panels/{panel_id}", json={"duration_s": None}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/storyboard/subjects/{subject_id}", json={"voice": "warm alto"}
        ).status_code
        == 200
    )
