"""Route contracts for the H3 video-prompt compiler (spec V1 5).

Reuses the client/db/StubRunner fixture machinery pattern from
tests/test_storyboard_api.py (same app factory, same runner stubbing via
monkeypatching backend.main.StoryboardRunner) so the compile route goes
through a stub while storyboard/panel PATCH routes go through the real
StoryboardService + a real temp DatabaseManager.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.main import create_app
from metascan.core.database_sqlite import DatabaseManager


class StubRunner:
    """Records compile_video calls; also stands in for StoryboardRunner in
    the lifespan (constructor + on_event/handle_job_event/aclose
    required)."""

    def __init__(self, db=None, comfy=None, get_vlm=None, output_root=None, **kwargs):
        self.db = db
        self.calls: List[Any] = []
        self.compile_result: Dict[str, int] = {
            "compiled": 0,
            "failed": 0,
            "skipped_locked": 0,
        }
        self.compile_exc: Optional[Exception] = None

    def on_event(self, cb):
        pass

    def handle_job_event(self, event, payload):
        pass

    async def compile_video(
        self, storyboard_id, panel_ids=None, force=False, deterministic_only=False
    ):
        self.calls.append(
            ("compile_video", storyboard_id, panel_ids, force, deterministic_only)
        )
        if self.compile_exc is not None:
            raise self.compile_exc
        return self.compile_result

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


def _panel_from_tree(client, sid: int, panel_id: int) -> Dict[str, Any]:
    tree = client.get(f"/api/storyboard/{sid}").json()
    for scene in tree["scenes"]:
        for panel in scene["panels"]:
            if panel["id"] == panel_id:
                return panel
    raise AssertionError(f"panel {panel_id} not found in tree")


# ---- POST /compile ----------------------------------------------------------


def test_compile_202_and_total_counts_unlocked(client):
    sid = _create_storyboard(client)
    r = client.patch(f"/api/storyboard/{sid}", json={"video_target": "minimax"})
    assert r.status_code == 200, r.text

    scene_id = _make_scene(client, sid)
    p1 = _make_panel(client, scene_id, "walks")
    p2 = _make_panel(client, scene_id, "runs")

    # Neither panel is locked yet -- total counts both.
    r = client.post(f"/api/storyboard/{sid}/compile", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 2}

    # Lock p1 via a user-supplied video_prompt.
    r = client.patch(
        f"/api/storyboard/panels/{p1}", json={"video_prompt": "drone push-in"}
    )
    assert r.status_code == 200
    assert r.json()["video_prompt_locked"] == 1

    # Locked panels are excluded from the default (unforced) count.
    r = client.post(f"/api/storyboard/{sid}/compile", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 1}

    # Explicitly targeting only the locked panel without force -> 0.
    r = client.post(f"/api/storyboard/{sid}/compile", json={"panel_ids": [p1]})
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 0}

    # Same, but forced -> the locked panel counts again.
    r = client.post(
        f"/api/storyboard/{sid}/compile", json={"panel_ids": [p1], "force": True}
    )
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 1}
    assert p2  # keep flake8 happy while documenting intent


def test_compile_400_when_target_not_minimax(client):
    sid = _create_storyboard(client)
    scene_id = _make_scene(client, sid)
    _make_panel(client, scene_id)

    # video_target defaults to NULL -- never set.
    r = client.post(f"/api/storyboard/{sid}/compile", json={})
    assert r.status_code == 400
    assert "minimax" in r.json()["detail"]

    # A raw DB value the guarded PATCH would itself reject (simulating
    # legacy/pre-migration data) must still be rejected by the route.
    client.db.update_storyboard(sid, video_target="ltx")
    r = client.post(f"/api/storyboard/{sid}/compile", json={})
    assert r.status_code == 400
    assert "minimax" in r.json()["detail"]


def test_compile_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/compile", json={})
    assert r.status_code == 503


def test_compile_unknown_storyboard_is_404(client):
    r = client.post("/api/storyboard/9999/compile", json={})
    assert r.status_code == 404


# ---- PATCH /storyboard video_target / video_mode ----------------------------


def test_patch_storyboard_video_target_validation(client):
    sid = _create_storyboard(client)

    r = client.patch(f"/api/storyboard/{sid}", json={"video_target": "minimax"})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_target"] == "minimax"

    r = client.patch(f"/api/storyboard/{sid}", json={"video_target": "ltx"})
    assert r.status_code == 400
    assert "ltx" in r.json()["detail"]

    # ltx rejection must not have partially applied.
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_target"] == "minimax"

    r = client.patch(f"/api/storyboard/{sid}", json={"video_target": None})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_target"] is None


def test_patch_storyboard_video_mode_validation(client):
    sid = _create_storyboard(client)

    r = client.patch(f"/api/storyboard/{sid}", json={"video_mode": "ref2va"})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_mode"] == "ref2va"

    r = client.patch(f"/api/storyboard/{sid}", json={"video_mode": "bogus"})
    assert r.status_code == 400

    r = client.patch(f"/api/storyboard/{sid}", json={"video_mode": None})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["video_mode"] is None


# ---- PATCH /panels video_prompt ---------------------------------------------


def test_patch_panel_video_prompt_locks_server_side(client):
    sid = _create_storyboard(client)
    scene_id = _make_scene(client, sid)
    panel_id = _make_panel(client, scene_id)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={"video_prompt": "slow dolly forward", "video_prompt_locked": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["video_prompt"] == "slow dolly forward"
    assert body["video_prompt_locked"] == 1
    assert body["video_prompt_source"] == "user"

    # And it's really in the DB, not just the response.
    panel = _panel_from_tree(client, sid, panel_id)
    assert panel["video_prompt_locked"] == 1
    assert panel["video_prompt_source"] == "user"


def test_patch_panel_video_prompt_null_clears_all(client):
    sid = _create_storyboard(client)
    scene_id = _make_scene(client, sid)
    panel_id = _make_panel(client, scene_id)

    # Seed a compiled prompt with warnings, as the runner would.
    client.db.update_panel(
        panel_id,
        video_prompt="a compiled prompt",
        video_prompt_source="compiled",
        video_prompt_locked=0,
        video_prompt_warnings='["shot exceeds cap"]',
    )

    r = client.patch(f"/api/storyboard/panels/{panel_id}", json={"video_prompt": None})
    assert r.status_code == 200
    body = r.json()
    assert body["video_prompt"] is None
    assert body["video_prompt_source"] is None
    assert body["video_prompt_locked"] == 0
    assert body["video_prompt_warnings"] == []

    panel = _panel_from_tree(client, sid, panel_id)
    assert panel["video_prompt"] is None
    assert panel["video_prompt_source"] is None
    assert panel["video_prompt_locked"] == 0
    assert panel["video_prompt_warnings"] == []


def test_patch_panel_unlock_only(client):
    sid = _create_storyboard(client)
    scene_id = _make_scene(client, sid)
    panel_id = _make_panel(client, scene_id)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={"video_prompt": "handheld tracking shot"},
    )
    assert r.status_code == 200
    assert r.json()["video_prompt_locked"] == 1

    # Unlocking alone must not touch video_prompt/source.
    r = client.patch(
        f"/api/storyboard/panels/{panel_id}", json={"video_prompt_locked": 0}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["video_prompt_locked"] == 0
    assert body["video_prompt"] == "handheld tracking shot"
    assert body["video_prompt_source"] == "user"
