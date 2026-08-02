"""Route tests for /api/comfy/*, against a stubbed ComfyClient.

Follows tests/test_lifespan_vlm.py: the client singleton is installed
directly rather than by constructing a real one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import comfy as comfy_api
from backend.main import create_app
from metascan.core.comfy_bindings import BindingError
from metascan.core.database_sqlite import DatabaseManager


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {}),
    }


class StubComfy:
    """Records calls; performs no network I/O.

    Also stands in for ComfyClient inside the lifespan (see the fixture),
    so it must accept the same constructor kwargs and expose start /
    shutdown / on_job_event.
    """

    def __init__(self, db=None, **kwargs):
        self.db = db
        self.base_url = "http://stub:8188"
        self.submitted = []
        self.cancelled = []
        self.listeners = []
        self.raise_on_register = None

    async def start(self):
        return None

    async def shutdown(self):
        return None

    def on_job_event(self, cb):
        self.listeners.append(cb)

    def snapshot(self):
        return {"base_url": self.base_url, "client_id": "stub", "in_flight": 2}

    async def register_preset(self, name, kind, workflow):
        if self.raise_on_register:
            raise self.raise_on_register
        from metascan.core.comfy_bindings import resolve_bindings
        import json as _json

        bindings = resolve_bindings(workflow, kind)
        return self.db.create_workflow_preset(
            name, kind, _json.dumps(workflow), bindings.to_json()
        )

    async def submit(self, preset_id, params, panel_id=None, priority=False):
        self.submitted.append((preset_id, params, panel_id, priority))
        return self.db.create_generation_job(preset_id, params.to_json(), panel_id)

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.db.update_generation_job(job_id, state="cancelled")


@pytest.fixture
def client(monkeypatch):
    """Install StubComfy *as* ComfyClient before the app starts.

    The lifespan constructs a ComfyClient and calls set_comfy_client
    itself, so installing a stub beforehand would simply be overwritten.
    Patching the class in backend.main is what makes the stub survive
    startup.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp))
        stubs = []

        def make_stub(*args, **kwargs):
            stub = StubComfy(db=db)
            stubs.append(stub)
            return stub

        monkeypatch.setattr("backend.dependencies.get_db", lambda: db)
        monkeypatch.setattr("backend.api.comfy.get_db", lambda: db)
        monkeypatch.setattr("backend.main.ComfyClient", make_stub)

        app = create_app()
        with TestClient(app) as c:
            assert stubs, "lifespan did not construct a ComfyClient"
            c.stub = stubs[0]
            c.db = db
            yield c
        comfy_api.set_comfy_client(None)


def test_status_reports_the_configured_server(client):
    r = client.get("/api/comfy/status")
    assert r.status_code == 200
    assert r.json()["base_url"] == "http://stub:8188"


def test_status_without_a_client_is_not_an_error(client):
    comfy_api.set_comfy_client(None)
    r = client.get("/api/comfy/status")
    assert r.status_code == 200
    assert r.json()["base_url"] is None


def test_register_a_preset(client):
    r = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)


def test_registering_an_unbindable_workflow_returns_400_with_the_missing_titles(client):
    client.stub.raise_on_register = BindingError(
        "Workflow is missing required node title(s) for kind 't2i': MS_SAVE"
    )
    r = client.post(
        "/api/comfy/presets",
        json={"name": "broken", "kind": "t2i", "workflow": {}},
    )
    assert r.status_code == 400
    assert "MS_SAVE" in r.json()["detail"]


def test_list_presets_omits_the_workflow_blob(client):
    client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    )
    rows = client.get("/api/comfy/presets").json()
    assert rows[0]["name"] == "sdxl"
    assert "workflow_json" not in rows[0]


def test_delete_a_preset_returns_a_json_body(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    r = client.delete(f"/api/comfy/presets/{pid}")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted"}


def test_delete_a_missing_preset_is_404(client):
    assert client.delete("/api/comfy/presets/9999").status_code == 404


def test_submit_returns_a_job_id(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]

    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 42,
            "width": 1024,
            "height": 576,
            "batch_size": 2,
        },
    )
    assert r.status_code == 200
    assert isinstance(r.json()["job_id"], int)
    assert client.stub.submitted[0][0] == pid


def test_submit_without_a_client_is_503(client):
    comfy_api.set_comfy_client(None)
    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": 1,
            "positive": "x",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    )
    assert r.status_code == 503


def test_get_and_list_jobs(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    job_id = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    ).json()["job_id"]

    assert client.get(f"/api/comfy/jobs/{job_id}").json()["id"] == job_id
    assert client.get("/api/comfy/jobs").json()[0]["id"] == job_id
    assert client.get("/api/comfy/jobs/9999").status_code == 404


def test_cancel_a_job(client):
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]
    job_id = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    ).json()["job_id"]

    r = client.post(f"/api/comfy/jobs/{job_id}/cancel")
    assert r.json() == {"status": "cancelled"}
    assert client.stub.cancelled == [job_id]
