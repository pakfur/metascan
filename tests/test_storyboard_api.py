"""Route tests for /api/storyboard/*.

Follows tests/test_comfy_api.py: a stub is installed *as* the class the
lifespan constructs (here, StoryboardRunner) so it survives app startup,
and get_db is patched to a temp DB shared with the router module. CRUD
routes go through the real StoryboardService + a real temp DatabaseManager;
runner-backed routes (parse/synthesize/generate/cancel) go through a
StubRunner that records calls and returns/raises whatever the test wants --
it must never reimplement runner validation logic itself.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.main import create_app
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.storyboard_parse import ParseError
from metascan.core.storyboard_runner import ConfirmRequiredError, StoryboardError


class StubRunner:
    """Records calls; returns/raises whatever the test pre-loads.

    Also stands in for StoryboardRunner inside the lifespan (see the
    fixture), so its constructor must accept the same kwargs and expose
    on_event / handle_job_event / aclose.
    """

    def __init__(self, db=None, comfy=None, get_vlm=None, output_root=None, **kwargs):
        self.db = db
        self.calls: List[Any] = []
        self.listeners: List[Any] = []

        self.parse_result: Dict[str, Any] = {"id": 1, "scenes": []}
        self.parse_exc: Optional[Exception] = None

        self.synthesize_result: Dict[str, int] = {
            "synthesized": 0,
            "fallback": 0,
            "skipped_locked": 0,
        }
        self.synthesize_exc: Optional[Exception] = None

        self.generate_result: List[int] = []
        self.generate_exc: Optional[Exception] = None

        self.cancel_result: int = 0
        self.cancel_exc: Optional[Exception] = None

    def on_event(self, cb):
        self.listeners.append(cb)

    def handle_job_event(self, event, payload):
        pass

    async def parse(self, storyboard_id, text, confirm=False):
        self.calls.append(("parse", storyboard_id, text, confirm))
        if self.parse_exc is not None:
            raise self.parse_exc
        return self.parse_result

    async def synthesize(self, storyboard_id, panel_ids=None, force=False):
        self.calls.append(("synthesize", storyboard_id, panel_ids, force))
        if self.synthesize_exc is not None:
            raise self.synthesize_exc
        return self.synthesize_result

    async def generate(self, storyboard_id, panel_ids=None, only_failed=False):
        self.calls.append(("generate", storyboard_id, panel_ids, only_failed))
        if self.generate_exc is not None:
            raise self.generate_exc
        return self.generate_result

    async def cancel(self, storyboard_id):
        self.calls.append(("cancel", storyboard_id))
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return self.cancel_result

    async def aclose(self):
        return None


@pytest.fixture
def client(monkeypatch):
    """Install StubRunner *as* StoryboardRunner before the app starts.

    The lifespan constructs a StoryboardRunner and calls
    set_storyboard_runner itself, so installing a stub beforehand would
    simply be overwritten. Patching the class in backend.main is what
    makes the stub survive startup.
    """
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


# ---- storyboard CRUD round-trip -------------------------------------------


def test_create_get_patch_delete_round_trip(client):
    sid = _create_storyboard(client)

    tree = client.get(f"/api/storyboard/{sid}")
    assert tree.status_code == 200
    body = tree.json()
    assert body["name"] == "My Storyboard"
    assert body["scenes"] == []
    assert body["subjects"] == []
    assert isinstance(body["base_seed"], int)

    r = client.patch(f"/api/storyboard/{sid}", json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json() == {"status": "updated"}

    tree2 = client.get(f"/api/storyboard/{sid}").json()
    assert tree2["name"] == "Renamed"

    r = client.delete(f"/api/storyboard/{sid}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}

    assert client.get(f"/api/storyboard/{sid}").status_code == 404


def test_get_unknown_storyboard_is_404(client):
    assert client.get("/api/storyboard/9999").status_code == 404


def test_patch_unknown_storyboard_is_404(client):
    r = client.patch("/api/storyboard/9999", json={"name": "x"})
    assert r.status_code == 404


def test_delete_unknown_storyboard_is_404(client):
    assert client.delete("/api/storyboard/9999").status_code == 404


def test_list_storyboards(client):
    _create_storyboard(client, name="A")
    _create_storyboard(client, name="B")
    rows = client.get("/api/storyboard").json()
    assert {r["name"] for r in rows} == {"A", "B"}


def test_create_storyboard_bad_aspect_ratio_is_400(client):
    r = client.post(
        "/api/storyboard",
        json={"name": "Bad", "target_model": "sd", "aspect_ratio": "21:9"},
    )
    assert r.status_code == 400
    assert "21:9" in r.json()["detail"]


def test_patch_storyboard_bad_aspect_ratio_is_400(client):
    sid = _create_storyboard(client)
    r = client.patch(f"/api/storyboard/{sid}", json={"aspect_ratio": "21:9"})
    assert r.status_code == 400


def test_create_storyboard_defaults_base_seed_and_clamps_batch_size(client):
    r = client.post(
        "/api/storyboard",
        json={
            "name": "Defaults",
            "target_model": "sd",
            "batch_size": 999,
        },
    )
    sid = r.json()["id"]
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert isinstance(tree["base_seed"], int)
    assert tree["batch_size"] == 16


# ---- parse / synthesize / generate / cancel ---------------------------------


def test_parse_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/parse", json={"text": "a story"})
    assert r.status_code == 503


def test_parse_confirm_required_is_409(client):
    sid = _create_storyboard(client)
    client.stub.parse_exc = ConfirmRequiredError("scenes already exist")
    r = client.post(f"/api/storyboard/{sid}/parse", json={"text": "a story"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "confirm_required"


def test_parse_error_is_422(client):
    sid = _create_storyboard(client)
    client.stub.parse_exc = ParseError("bad grammar output")
    r = client.post(f"/api/storyboard/{sid}/parse", json={"text": "a story"})
    assert r.status_code == 422


def test_parse_no_vlm_is_503(client):
    sid = _create_storyboard(client)
    client.stub.parse_exc = StoryboardError("no VLM client -- parsing requires a VLM")
    r = client.post(f"/api/storyboard/{sid}/parse", json={"text": "a story"})
    assert r.status_code == 503


def test_parse_success_returns_tree_and_records_call(client):
    sid = _create_storyboard(client)
    client.stub.parse_result = {"id": sid, "scenes": [], "subjects": []}
    r = client.post(
        f"/api/storyboard/{sid}/parse", json={"text": "a story", "confirm": True}
    )
    assert r.status_code == 200
    assert r.json() == {"id": sid, "scenes": [], "subjects": []}
    assert client.stub.calls == [("parse", sid, "a story", True)]


def test_synthesize_returns_202_started_with_total(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"}
    ).json()["id"]
    p1 = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "walks"}
    ).json()["id"]
    p2 = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "runs"}
    ).json()["id"]

    r = client.post(f"/api/storyboard/{sid}/synthesize", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 2}

    r2 = client.post(f"/api/storyboard/{sid}/synthesize", json={"panel_ids": [p1]})
    assert r2.status_code == 202
    assert r2.json() == {"status": "started", "total": 1}
    assert p2  # keep flake8 happy about unused var while documenting intent


def test_synthesize_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/synthesize", json={})
    assert r.status_code == 503


def test_synthesize_unknown_storyboard_is_404(client):
    r = client.post("/api/storyboard/9999/synthesize", json={})
    assert r.status_code == 404


def test_generate_maps_storyboard_error_to_400(client):
    sid = _create_storyboard(client)
    client.stub.generate_exc = StoryboardError("storyboard has no workflow preset")
    r = client.post(f"/api/storyboard/{sid}/generate", json={})
    assert r.status_code == 400


def test_generate_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/generate", json={})
    assert r.status_code == 503


def test_generate_success_returns_job_ids(client):
    sid = _create_storyboard(client)
    client.stub.generate_result = [1, 2, 3]
    r = client.post(f"/api/storyboard/{sid}/generate", json={"only_failed": True})
    assert r.status_code == 200
    assert r.json() == {"jobs": [1, 2, 3]}
    assert client.stub.calls == [("generate", sid, None, True)]


def test_cancel_returns_count(client):
    sid = _create_storyboard(client)
    client.stub.cancel_result = 4
    r = client.post(f"/api/storyboard/{sid}/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": 4}


def test_cancel_without_runner_is_503(client):
    storyboard_api.set_storyboard_runner(None)
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/cancel")
    assert r.status_code == 503


# ---- subjects ---------------------------------------------------------------


def test_subject_crud(client):
    sid = _create_storyboard(client)
    r = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "the protagonist"},
    )
    assert r.status_code == 200
    subj_id = r.json()["id"]

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["subjects"][0]["name"] == "Alice"

    r = client.patch(f"/api/storyboard/subjects/{subj_id}", json={"name": "Bob"})
    assert r.status_code == 200
    assert r.json() == {"status": "updated"}
    tree2 = client.get(f"/api/storyboard/{sid}").json()
    assert tree2["subjects"][0]["name"] == "Bob"

    r = client.delete(f"/api/storyboard/subjects/{subj_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


def test_create_subject_unknown_reference_path_is_400(client):
    sid = _create_storyboard(client)
    r = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x", "reference_path": "/nope.png"},
    )
    assert r.status_code == 400
    assert "reference image" in r.json()["detail"]


def test_patch_subject_unknown_reference_path_is_400(client):
    sid = _create_storyboard(client)
    subj_id = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]
    r = client.patch(
        f"/api/storyboard/subjects/{subj_id}",
        json={"reference_path": "/nope.png"},
    )
    assert r.status_code == 400
    assert "reference image" in r.json()["detail"]


def test_create_subject_unknown_storyboard_is_404(client):
    r = client.post(
        "/api/storyboard/9999/subjects",
        json={"name": "Alice", "description": "x"},
    )
    assert r.status_code == 404


def test_patch_unknown_subject_is_404(client):
    r = client.patch("/api/storyboard/subjects/9999", json={"name": "x"})
    assert r.status_code == 404


def test_delete_unknown_subject_is_404(client):
    assert client.delete("/api/storyboard/subjects/9999").status_code == 404


# ---- scenes -----------------------------------------------------------------


def test_scene_crud(client):
    sid = _create_storyboard(client)
    r = client.post(f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"})
    assert r.status_code == 200
    scene_id = r.json()["id"]

    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}", json={"name": "Scene 1 renamed"}
    )
    assert r.status_code == 200
    assert r.json() == {"status": "updated"}

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["name"] == "Scene 1 renamed"

    r = client.delete(f"/api/storyboard/scenes/{scene_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


def test_create_scene_unknown_storyboard_is_404(client):
    r = client.post("/api/storyboard/9999/scenes", json={"name": "x"})
    assert r.status_code == 404


def test_patch_unknown_scene_is_404(client):
    r = client.patch("/api/storyboard/scenes/9999", json={"name": "x"})
    assert r.status_code == 404


def test_delete_unknown_scene_is_404(client):
    assert client.delete("/api/storyboard/scenes/9999").status_code == 404


# ---- panels -----------------------------------------------------------------


def _make_panel(client, sid: int) -> int:
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"}
    ).json()["id"]
    return client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "walks in"}
    ).json()["id"]


def test_panel_crud(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["panels"][0]["action"] == "walks in"

    r = client.patch(f"/api/storyboard/panels/{panel_id}", json={"action": "runs away"})
    assert r.status_code == 200
    assert r.json()["action"] == "runs away"

    r = client.delete(f"/api/storyboard/panels/{panel_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


def test_create_panel_unknown_scene_is_404(client):
    r = client.post("/api/storyboard/scenes/9999/panels", json={"action": "walks"})
    assert r.status_code == 404


def test_patch_unknown_panel_is_404(client):
    r = client.patch("/api/storyboard/panels/9999", json={"action": "x"})
    assert r.status_code == 404


def test_delete_unknown_panel_is_404(client):
    assert client.delete("/api/storyboard/panels/9999").status_code == 404


def test_panel_patch_with_prompt_locks_it(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={
            "prompt": "a cat on a wall",
            "prompt_locked": False,
            "prompt_source": "llm",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "a cat on a wall"
    assert body["prompt_locked"] == 1
    assert body["prompt_source"] == "user"

    # And it's really in the DB, not just the response.
    tree = client.get(f"/api/storyboard/{sid}").json()
    panel = tree["scenes"][0]["panels"][0]
    assert panel["prompt_locked"] == 1
    assert panel["prompt_source"] == "user"


# ---- select -----------------------------------------------------------------


def test_select_panel_image(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    # No media/panel_images rows exist yet -- selecting a real id that
    # doesn't belong to this panel is a 404.
    r = client.post(
        f"/api/storyboard/panels/{panel_id}/select", json={"image_id": 9999}
    )
    assert r.status_code == 404


def test_select_panel_image_none_clears_selection(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.post(
        f"/api/storyboard/panels/{panel_id}/select", json={"image_id": None}
    )
    assert r.status_code == 200
    assert r.json()["id"] == panel_id
    assert r.json()["selected_image_id"] is None


def test_select_unknown_panel_is_404(client):
    r = client.post("/api/storyboard/panels/9999/select", json={"image_id": None})
    assert r.status_code == 404


def test_select_image_from_a_different_panel_is_404(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"}
    ).json()["id"]
    panel_a = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "a"}
    ).json()["id"]
    panel_b = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "b"}
    ).json()["id"]

    # panel_images.file_path is FK'd to media(file_path), so a real media
    # row has to exist before create_panel_image will take it.
    client.db.save_media(
        Media(
            file_path=Path("/tmp/does-not-matter.png"),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    # Insert a real panel_images row for panel_a directly via the DB, then
    # try to select it from panel_b.
    image_id = client.db.create_panel_image(
        panel_a, file_path="/tmp/does-not-matter.png"
    )

    r = client.post(
        f"/api/storyboard/panels/{panel_b}/select", json={"image_id": image_id}
    )
    assert r.status_code == 404
