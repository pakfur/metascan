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

    async def synthesize(self, storyboard_id, beat_ids=None, force=False):
        self.calls.append(("synthesize", storyboard_id, beat_ids, force))
        if self.synthesize_exc is not None:
            raise self.synthesize_exc
        return self.synthesize_result

    async def generate(self, storyboard_id, beat_ids=None, only_failed=False):
        self.calls.append(("generate", storyboard_id, beat_ids, only_failed))
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


@pytest.fixture
def storyboard_id(client) -> int:
    return _create_storyboard(client)


@pytest.fixture
def panel_id(client, storyboard_id) -> int:
    return _make_panel(client, storyboard_id)


@pytest.fixture
def beat_id(client, panel_id) -> int:
    return _make_beat(client, panel_id)


@pytest.fixture
def beat_image_id(client, beat_id) -> int:
    path = "/tmp/beat-fixture-image.png"
    client.db.save_media(
        Media(
            file_path=Path(path),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    return client.db.create_beat_image(beat_id, file_path=path)


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


def test_patch_storyboard_null_negative_clears_it(client):
    sid = _create_storyboard(client, negative="ugly, blurry")
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["negative"] == "ugly, blurry"

    r = client.patch(f"/api/storyboard/{sid}", json={"negative": None})
    assert r.status_code == 200

    tree2 = client.get(f"/api/storyboard/{sid}").json()
    assert tree2["negative"] is None


def test_patch_storyboard_null_name_is_400(client):
    sid = _create_storyboard(client)
    r = client.patch(f"/api/storyboard/{sid}", json={"name": None})
    assert r.status_code == 400
    assert "name" in r.json()["detail"]


def test_storyboard_patch_notes(client, storyboard_id):
    r = client.patch(f"/api/storyboard/{storyboard_id}", json={"notes": "n"})
    assert r.status_code == 200


def test_storyboard_create_persists_notes(client):
    sid = _create_storyboard(client, notes="n")
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["notes"] == "n"


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
    b1 = client.post(
        f"/api/storyboard/panels/{p1}/beats", json={"action": "she kneels"}
    ).json()["id"]
    client.post(f"/api/storyboard/panels/{p2}/beats", json={"action": "she stands"})

    r = client.post(f"/api/storyboard/{sid}/synthesize", json={})
    assert r.status_code == 202
    assert r.json() == {"status": "started", "total": 2}

    r2 = client.post(f"/api/storyboard/{sid}/synthesize", json={"beat_ids": [b1]})
    assert r2.status_code == 202
    assert r2.json() == {"status": "started", "total": 1}


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

    r = client.get(f"/api/storyboard/subjects/{subj_id}/references")
    assert r.status_code == 200
    assert r.json() == {"beat_ids": [], "image_count": 0}

    r = client.delete(f"/api/storyboard/subjects/{subj_id}", params={"mode": "bogus"})
    assert r.status_code == 400

    r = client.delete(f"/api/storyboard/subjects/{subj_id}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}
    assert (
        client.get(f"/api/storyboard/subjects/{subj_id}/references").status_code == 404
    )


def test_delete_subject_modes_via_api(client, panel_id):
    tree_id = client.get("/api/storyboard").json()[0]["id"]
    subj = client.post(
        f"/api/storyboard/{tree_id}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]
    r = client.post(
        f"/api/storyboard/panels/{panel_id}/beats",
        json={"action": "waves", "sort_order": 5, "subject_ids": [subj]},
    )
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    refs = client.get(f"/api/storyboard/subjects/{subj}/references").json()
    assert refs["beat_ids"] == [bid]

    r = client.delete(f"/api/storyboard/subjects/{subj}", params={"mode": "content"})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{tree_id}").json()
    assert tree["subjects"] == []
    beat_ids = [
        b["id"] for s in tree["scenes"] for p in s["panels"] for b in p["beats"]
    ]
    assert bid not in beat_ids


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


def test_patch_subject_null_lora_name_clears_it(client):
    sid = _create_storyboard(client)
    subj_id = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x", "lora_name": "alice_lora"},
    ).json()["id"]

    r = client.patch(f"/api/storyboard/subjects/{subj_id}", json={"lora_name": None})
    assert r.status_code == 200

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["subjects"][0]["lora_name"] is None


def test_patch_subject_null_name_is_400(client):
    sid = _create_storyboard(client)
    subj_id = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]
    r = client.patch(f"/api/storyboard/subjects/{subj_id}", json={"name": None})
    assert r.status_code == 400
    assert "name" in r.json()["detail"]


def test_patch_subject_sheet_ref(client):
    sid = _create_storyboard(client)
    subj_id = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]
    r = client.patch(f"/api/storyboard/subjects/{subj_id}", json={"sheet_ref": 1})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["subjects"][0]["sheet_ref"] == 1
    assert (
        client.patch(
            f"/api/storyboard/subjects/{subj_id}", json={"sheet_ref": 2}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/storyboard/subjects/{subj_id}", json={"sheet_ref": None}
        ).status_code
        == 400
    )


def test_patch_subject_pov_ref(client):
    sid = _create_storyboard(client)
    subj_id = client.post(
        f"/api/storyboard/{sid}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]
    r = client.patch(f"/api/storyboard/subjects/{subj_id}", json={"pov_ref": 1})
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["subjects"][0]["pov_ref"] == 1
    assert (
        client.patch(
            f"/api/storyboard/subjects/{subj_id}", json={"pov_ref": 2}
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/storyboard/subjects/{subj_id}", json={"pov_ref": None}
        ).status_code
        == 400
    )


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


def test_patch_scene_null_notes_clears_it(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1", "notes": "foggy"}
    ).json()["id"]

    r = client.patch(f"/api/storyboard/scenes/{scene_id}", json={"notes": None})
    assert r.status_code == 200

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["notes"] is None


def test_scene_subtitle_and_setting_roundtrip(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes",
        json={
            "name": "Scene 1",
            "subtitle": "the reunion",
            "setting": "rain-slick rooftop garden at night",
        },
    ).json()["id"]

    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["subtitle"] == "the reunion"
    assert tree["scenes"][0]["setting"] == "rain-slick rooftop garden at night"

    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}",
        json={"subtitle": "the parting", "setting": "sun-bleached desert highway"},
    )
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["subtitle"] == "the parting"
    assert tree["scenes"][0]["setting"] == "sun-bleached desert highway"

    # Explicit nulls clear both nullable columns.
    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}",
        json={"subtitle": None, "setting": None},
    )
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sid}").json()
    assert tree["scenes"][0]["subtitle"] is None
    assert tree["scenes"][0]["setting"] is None


def test_patch_scene_null_name_is_400(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"}
    ).json()["id"]
    r = client.patch(f"/api/storyboard/scenes/{scene_id}", json={"name": None})
    assert r.status_code == 400
    assert "name" in r.json()["detail"]


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


def test_patch_panel_null_action_is_400(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(f"/api/storyboard/panels/{panel_id}", json={"action": None})
    assert r.status_code == 400
    assert "action" in r.json()["detail"]


def test_patch_panel_round_trips_lora_lists(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={
            "image_loras": [{"name": "style.safetensors", "strength": 0.7}],
            "video_loras": [{"name": "motion.safetensors", "strength": 1.0}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["image_loras"] == [{"name": "style.safetensors", "strength": 0.7}]
    assert body["video_loras"] == [{"name": "motion.safetensors", "strength": 1.0}]


def test_patch_panel_lora_strength_defaults_to_one(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={"image_loras": [{"name": "style.safetensors"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["image_loras"] == [{"name": "style.safetensors", "strength": 1.0}]


def test_patch_panel_null_lora_list_is_400(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(f"/api/storyboard/panels/{panel_id}", json={"image_loras": None})
    assert r.status_code == 400
    assert "image_loras" in r.json()["detail"]


def test_patch_panel_lora_entry_without_name_is_422(client):
    sid = _create_storyboard(client)
    panel_id = _make_panel(client, sid)

    r = client.patch(
        f"/api/storyboard/panels/{panel_id}",
        json={"video_loras": [{"strength": 0.5}]},
    )
    assert r.status_code == 422


def test_panel_patch_rejects_dropped_fields(client, panel_id):
    r = client.patch(f"/api/storyboard/panels/{panel_id}", json={"shot_size": "CU"})
    # Pydantic ignores unknown fields by default -> field silently absent;
    # assert the response carries no shot_size key
    assert "shot_size" not in r.json()


# ---- beats --------------------------------------------------------------------


def _make_beat(client, panel_id: int, action: str = "she kneels") -> int:
    r = client.post(f"/api/storyboard/panels/{panel_id}/beats", json={"action": action})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_beat_patch_server_wins_lock(client, beat_id):
    r = client.patch(
        f"/api/storyboard/beats/{beat_id}",
        json={"prompt": "hand-written", "prompt_locked": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt_locked"] == 1 and body["prompt_source"] == "user"


def test_patch_beat_null_shot_size_clears_column(client, beat_id):
    r = client.patch(f"/api/storyboard/beats/{beat_id}", json={"shot_size": "CU"})
    assert r.status_code == 200
    assert r.json()["shot_size"] == "CU"

    r = client.patch(f"/api/storyboard/beats/{beat_id}", json={"shot_size": None})
    assert r.status_code == 200
    assert r.json()["shot_size"] is None


# ---- select -----------------------------------------------------------------


def test_beat_select_route(client, beat_id, beat_image_id):
    r = client.post(
        f"/api/storyboard/beats/{beat_id}/select", json={"image_id": beat_image_id}
    )
    assert r.status_code == 200
    assert r.json()["selected_image_id"] == beat_image_id


def test_select_beat_image_unknown_id_is_404(client, beat_id):
    # No media/beat_images rows exist yet -- selecting a real id that
    # doesn't belong to this beat is a 404.
    r = client.post(f"/api/storyboard/beats/{beat_id}/select", json={"image_id": 9999})
    assert r.status_code == 404


def test_select_beat_image_none_clears_selection(client, beat_id):
    r = client.post(f"/api/storyboard/beats/{beat_id}/select", json={"image_id": None})
    assert r.status_code == 200
    assert r.json()["id"] == beat_id
    assert r.json()["selected_image_id"] is None


def test_select_unknown_beat_is_404(client):
    r = client.post("/api/storyboard/beats/9999/select", json={"image_id": None})
    assert r.status_code == 404


def test_select_image_from_a_different_beat_is_404(client, panel_id):
    beat_a = _make_beat(client, panel_id, "a")
    beat_b = _make_beat(client, panel_id, "b")

    # beat_images.file_path is FK'd to media(file_path), so a real media
    # row has to exist before create_beat_image will take it.
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
    # Insert a real beat_images row for beat_a directly via the DB, then
    # try to select it from beat_b.
    image_id = client.db.create_beat_image(beat_a, file_path="/tmp/does-not-matter.png")

    r = client.post(
        f"/api/storyboard/beats/{beat_b}/select", json={"image_id": image_id}
    )
    assert r.status_code == 404


def test_delete_beat_purge_flag(client, beat_id):
    r = client.delete(f"/api/storyboard/beats/{beat_id}?purge_images=true")
    assert r.status_code == 200 and r.json() == {"status": "deleted"}


# ---- purge_images + folder removal ----------------------------------------


def _seed_panel_with_image(client, path: str):
    sid = _create_storyboard(client)
    scene_id = client.post(f"/api/storyboard/{sid}/scenes", json={"name": "S"}).json()[
        "id"
    ]
    panel_id = client.post(
        f"/api/storyboard/scenes/{scene_id}/panels", json={"action": "a"}
    ).json()["id"]
    beat_id = client.post(
        f"/api/storyboard/panels/{panel_id}/beats", json={"action": "a"}
    ).json()["id"]
    client.db.save_media(
        Media(
            file_path=Path(path),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    client.db.set_media_hidden(path, True)
    client.db.create_beat_image(beat_id, file_path=path)
    return sid, scene_id, panel_id


def _media_paths(client) -> set:
    return {
        r["file_path"] for r in client.db.get_all_media_summaries(include_hidden=True)
    }


def test_delete_panel_purge_images_removes_media_and_trashes_file(
    client, monkeypatch, tmp_path
):
    img = tmp_path / "purge-me.png"
    img.write_bytes(b"x")
    trashed = []
    monkeypatch.setattr(
        "metascan.utils.trash.send2trash",
        lambda p: trashed.append(p),
    )
    _sid, _scene_id, panel_id = _seed_panel_with_image(client, str(img))

    r = client.delete(f"/api/storyboard/panels/{panel_id}?purge_images=true")

    assert r.status_code == 200
    assert str(img) not in _media_paths(client)
    assert trashed == [str(img)]


def test_delete_panel_default_keeps_and_unhides_media(client):
    _sid, _scene_id, panel_id = _seed_panel_with_image(client, "/tmp/keep-me.png")

    r = client.delete(f"/api/storyboard/panels/{panel_id}")

    assert r.status_code == 200
    rows = {
        r2["file_path"]: r2["hidden"]
        for r2 in client.db.get_all_media_summaries(include_hidden=True)
    }
    assert rows["/tmp/keep-me.png"] is False


def test_delete_storyboard_purge_images_via_query_param(client, monkeypatch, tmp_path):
    img = tmp_path / "board-img.png"
    img.write_bytes(b"x")
    trashed = []
    monkeypatch.setattr(
        "metascan.utils.trash.send2trash",
        lambda p: trashed.append(p),
    )
    sid, _scene_id, _panel_id = _seed_panel_with_image(client, str(img))

    r = client.delete(f"/api/storyboard/{sid}?purge_images=true")

    assert r.status_code == 200
    assert str(img) not in _media_paths(client)
    assert trashed == [str(img)]


def test_delete_storyboard_removes_folder(client):
    sid, _scene_id, _panel_id = _seed_panel_with_image(client, "/tmp/folder-img.png")
    folder = client.db.create_folder(
        "33333333-3333-3333-3333-333333333333", "manual", "Storyboard: My Storyboard"
    )
    client.db.update_storyboard(sid, folder_id=folder["id"])
    client.db.add_folder_items(folder["id"], ["/tmp/folder-img.png"])

    r = client.delete(f"/api/storyboard/{sid}")

    assert r.status_code == 200
    assert client.db.get_folder(folder["id"]) is None


# ---- cinematic fields (pacing / spine / visual grammar) ----------------------


def test_patch_cinematic_fields(client):
    sb = _create_storyboard(client)
    r = client.patch(f"/api/storyboard/{sb}", json={"pacing": "propulsive"})
    assert r.status_code == 200
    assert client.get(f"/api/storyboard/{sb}").json()["pacing"] == "propulsive"
    assert (
        client.patch(f"/api/storyboard/{sb}", json={"pacing": "glacial"}).status_code
        == 400
    )
    assert (
        client.patch(f"/api/storyboard/{sb}", json={"pacing": None}).status_code == 400
    )
    r = client.patch(f"/api/storyboard/{sb}", json={"story_scale": "extended"})
    assert r.status_code == 200
    assert client.get(f"/api/storyboard/{sb}").json()["story_scale"] == "extended"
    assert (
        client.patch(f"/api/storyboard/{sb}", json={"story_scale": "epic"}).status_code
        == 400
    )
    assert (
        client.patch(f"/api/storyboard/{sb}", json={"story_scale": None}).status_code
        == 400
    )

    scene = client.post(f"/api/storyboard/{sb}/scenes", json={"name": "S"}).json()["id"]
    r = client.patch(
        f"/api/storyboard/scenes/{scene}",
        json={"arc_beats": ["setup", "turn"], "charge_in": -1, "charge_out": 3},
    )
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sb}").json()
    assert tree["scenes"][0]["arc_beats"] == ["setup", "turn"]
    assert tree["scenes"][0]["charge_out"] == 3
    assert (
        client.patch(
            f"/api/storyboard/scenes/{scene}", json={"arc_beats": None}
        ).status_code
        == 400
    )

    panel = client.post(
        f"/api/storyboard/scenes/{scene}/panels", json={"action": "a"}
    ).json()["id"]
    r = client.patch(
        f"/api/storyboard/panels/{panel}",
        json={"is_turn": 1, "subtext": "hidden meaning"},
    )
    assert r.status_code == 200
    assert r.json()["is_turn"] == 1 and r.json()["subtext"] == "hidden meaning"
    assert (
        client.patch(
            f"/api/storyboard/panels/{panel}", json={"is_turn": None}
        ).status_code
        == 400
    )

    beat = client.post(
        f"/api/storyboard/panels/{panel}/beats", json={"action": "b"}
    ).json()["id"]
    r = client.patch(
        f"/api/storyboard/beats/{beat}",
        json={
            "composition": "centered",
            "light_quality": "soft",
            "emotional_intent": "jaw set",
            "reveals": "the door",
            "movement_motivation": "she leans in",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["composition"] == "centered"
    assert body["movement_motivation"] == "she leans in"
    # explicit null clears a nullable field
    r = client.patch(f"/api/storyboard/beats/{beat}", json={"reveals": None})
    assert r.status_code == 200 and r.json()["reveals"] is None


def test_patch_scene_template_id_and_brief(client):
    sid = _create_storyboard(client)
    scene_id = client.post(
        f"/api/storyboard/{sid}/scenes", json={"name": "Scene 1"}
    ).json()["id"]

    r = client.patch(f"/api/storyboard/scenes/{scene_id}", json={"template_id": "nope"})
    assert r.status_code == 400 and "unknown template" in r.json()["detail"]

    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}",
        json={"template_id": "two_party_negotiation_18", "brief": "b"},
    )
    assert r.status_code == 200
    scene = client.db.get_scene(scene_id)
    assert scene["template_id"] == "two_party_negotiation_18" and scene["brief"] == "b"

    r = client.patch(f"/api/storyboard/scenes/{scene_id}", json={"template_id": None})
    assert r.status_code == 200
    assert client.db.get_scene(scene_id)["template_id"] is None
