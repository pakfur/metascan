"""Route tests for the video-generation route + video/voice-ref PATCH fields.

Follows tests/test_storyboard_compile_api.py: a local StubRunner stands in
for StoryboardRunner inside the lifespan, and CRUD routes go through the
real StoryboardService + a real temp DatabaseManager.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.main import create_app
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import StoryboardError


class StubRunner:
    """Records generate_video calls; also stands in for StoryboardRunner in
    the lifespan (constructor + on_event/handle_job_event/aclose
    required)."""

    def __init__(self, db=None, comfy=None, get_vlm=None, output_root=None, **kwargs):
        self.db = db
        self.calls: List[Any] = []
        self.generate_video_result: Dict[str, Any] = {"jobs": [], "skipped": []}
        self.generate_video_exc: Optional[Exception] = None

    def on_event(self, cb):
        pass

    def handle_job_event(self, event, payload):
        pass

    async def generate_video(self, storyboard_id, panel_ids=None, only_failed=False):
        self.calls.append(("generate_video", storyboard_id, panel_ids, only_failed))
        if self.generate_video_exc is not None:
            raise self.generate_video_exc
        return self.generate_video_result

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


def _create_storyboard(client, **overrides) -> int:
    body = {
        "name": "My Storyboard",
        "target_model": "sd",
        "aspect_ratio": "16:9",
    }
    body.update(overrides)
    r = client.post("/api/storyboard", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_scene(client, sid: int) -> int:
    r = client.post(f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_panel(client, scene_id: int, action: str = "walks") -> int:
    r = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": action}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def _make_preset(client, kind: str = "ref2v") -> int:
    workflow = {"1": _node("KSampler", "MS_SEED", {"seed": 0})}
    return client.db.create_workflow_preset(
        "Preset", kind, json.dumps(workflow), json.dumps({})
    )


# ---- POST /generate-video ----------------------------------------------------


def test_generate_video_returns_jobs(client):
    sid = _create_storyboard(client)
    client.stub.generate_video_result = {
        "jobs": [1, 2, 3],
        "skipped": [{"panel_id": 9, "error": "boom"}],
    }
    r = client.post(f"/api/storyboard/{sid}/generate-video", json={"only_failed": True})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "jobs": [1, 2, 3],
        "skipped": [{"panel_id": 9, "error": "boom"}],
    }
    assert client.stub.calls == [("generate_video", sid, None, True)]


def test_generate_video_400_maps_storyboard_error_detail(client):
    sid = _create_storyboard(client)
    detail = "panel 1: no compiled video_prompt\npanel 2: no compiled video_prompt"
    client.stub.generate_video_exc = StoryboardError(detail)
    r = client.post(f"/api/storyboard/{sid}/generate-video", json={})
    assert r.status_code == 400
    assert r.json()["detail"] == detail


def test_generate_video_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/generate-video", json={})
    assert r.status_code == 503


def test_generate_video_passes_panel_ids(client):
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/generate-video", json={"panel_ids": [1, 2]})
    assert r.status_code == 200, r.text
    assert client.stub.calls == [("generate_video", sid, [1, 2], False)]


# ---- PATCH /storyboard video_preset_id ---------------------------------------


def test_patch_storyboard_video_preset_id_set_and_clear(client):
    sid = _create_storyboard(client)
    preset_id = _make_preset(client)

    r = client.patch(f"/api/storyboard/{sid}", json={"video_preset_id": preset_id})
    assert r.status_code == 200, r.text
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_preset_id"] == preset_id

    r = client.patch(f"/api/storyboard/{sid}", json={"video_preset_id": None})
    assert r.status_code == 200, r.text
    tree2 = client.get(f"/api/storyboard/{sid}").json()
    assert tree2["video_preset_id"] is None


def test_patch_storyboard_video_preset_id_unknown_is_400(client):
    sid = _create_storyboard(client)
    r = client.patch(f"/api/storyboard/{sid}", json={"video_preset_id": 9999})
    assert r.status_code == 400

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_preset_id"] is None


# ---- PATCH /panels video_anchor ----------------------------------------------


def test_patch_panel_video_anchor_validation(client):
    sid = _create_storyboard(client)
    scene_id = _make_scene(client, sid)
    pid = _make_panel(client, scene_id)

    r = client.patch(f"/api/storyboard/panels/{pid}", json={"video_anchor": "keeper"})
    assert r.status_code == 200, r.text
    assert r.json()["video_anchor"] == "keeper"

    r = client.patch(
        f"/api/storyboard/panels/{pid}", json={"video_anchor": "prev_last"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["video_anchor"] == "prev_last"

    r = client.patch(f"/api/storyboard/panels/{pid}", json={"video_anchor": "bogus"})
    assert r.status_code == 400
    assert "video_anchor" in r.json()["detail"]

    # Rejection must not have partially applied -- still "prev_last" from
    # the last successful patch.
    tree = client.get(f"/api/storyboard/{sid}").json()
    panel = tree["scenes"][0]["panels"][0]
    assert panel["video_anchor"] == "prev_last"

    r = client.patch(f"/api/storyboard/panels/{pid}", json={"video_anchor": None})
    assert r.status_code == 200, r.text
    assert r.json()["video_anchor"] is None


# ---- PATCH /subjects voice_ref_path ------------------------------------------


def test_patch_subject_voice_ref_path(client):
    sid = _create_storyboard(client)
    r = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "the lead"},
    )
    assert r.status_code == 200, r.text
    subject_id = r.json()["id"]

    r = client.patch(
        f"/api/storyboard/subjects/{subject_id}",
        json={"voice_ref_path": "/audio/alice.wav"},
    )
    assert r.status_code == 200, r.text

    tree = client.get(f"/api/storyboard/{sid}").json()
    subject = next(s for s in tree["subjects"] if s["id"] == subject_id)
    assert subject["voice_ref_path"] == "/audio/alice.wav"

    r = client.patch(
        f"/api/storyboard/subjects/{subject_id}", json={"voice_ref_path": None}
    )
    assert r.status_code == 200, r.text
    tree2 = client.get(f"/api/storyboard/{sid}").json()
    subject2 = next(s for s in tree2["subjects"] if s["id"] == subject_id)
    assert subject2["voice_ref_path"] is None
