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
from metascan.core.comfy_client import PresetNotFoundError
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
        self.loras = []
        self.raise_on_register = None

    async def list_loras(self):
        return list(self.loras)

    async def start(self):
        return None

    async def shutdown(self):
        return None

    def on_job_event(self, cb):
        self.listeners.append(cb)

    def snapshot(self):
        return {"base_url": self.base_url, "client_id": "stub", "in_flight": 2}

    async def register_preset(
        self, name, kind, workflow, video_target=None, video_mode=None
    ):
        if self.raise_on_register:
            raise self.raise_on_register
        from metascan.core.comfy_bindings import resolve_bindings
        import json as _json

        bindings = resolve_bindings(workflow, kind)
        return self.db.create_workflow_preset(
            name,
            kind,
            _json.dumps(workflow),
            bindings.to_json(),
            video_target,
            video_mode,
        )

    async def submit(self, preset_id, params, panel_id=None, priority=False):
        # Mirrors ComfyClient.submit()'s eager validation (Task 11 fix
        # round 1): load the preset for real and run the same pure
        # apply_overrides() binding check the client uses, so tests
        # against this stub exercise the actual PresetNotFoundError /
        # BindingError contract instead of a stub that always succeeds.
        from metascan.core.comfy_bindings import Bindings, apply_overrides
        import json as _json

        row = self.db.get_workflow_preset(preset_id)
        if row is None:
            raise PresetNotFoundError(f"No workflow preset with id {preset_id}")
        workflow = _json.loads(row["workflow_json"])
        bindings = Bindings.from_json(row["bindings"])
        apply_overrides(workflow, bindings, params)  # raises BindingError

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


def test_list_loras_returns_client_result(client):
    client.stub.loras = ["style.safetensors"]
    r = client.get("/api/comfy/loras")
    assert r.status_code == 200
    assert r.json() == ["style.safetensors"]


def test_list_loras_without_a_client_is_empty(client):
    comfy_api.set_comfy_client(None)
    r = client.get("/api/comfy/loras")
    assert r.status_code == 200
    assert r.json() == []


def test_register_a_preset(client):
    r = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)


def test_registering_an_unbindable_workflow_returns_400_with_the_missing_titles(client):
    # Route-level validation now catches this before register_preset: the
    # detail is a structured report rather than a bare BindingError string.
    r = client.post(
        "/api/comfy/presets",
        json={"name": "broken", "kind": "t2i", "workflow": {}},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "validation_failed"
    assert "MS_SAVE" in str(detail["findings"])


def _ref2v_wf(**extra):
    wf = {
        "1": {"_meta": {"title": "MS_POSITIVE"}, "inputs": {"text": ""}},
        "2": {"_meta": {"title": "MS_SEED"}, "inputs": {"seed": 0}},
        "3": {"_meta": {"title": "MS_SAVE"}, "inputs": {}},
    }
    wf.update(extra)
    return wf


def test_validate_preset_endpoint_reports_findings_and_fixes(client):
    wf = _ref2v_wf(ref={"_meta": {"title": "MS_REF_IMGE"}, "inputs": {"image": ""}})
    r = client.post(
        "/api/comfy/presets/validate",
        json={
            "kind": "ref2v",
            "workflow": wf,
            "video_target": "minimax",
            "video_mode": "ref2va",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    codes = [f["code"] for f in body["findings"]]
    assert "unknown_title" in codes and "no_audio_slot" in codes
    assert body["fixes"][0]["title"] == "MS_REF_IMAGE"
    assert body["fixed_workflow"]["ref"]["_meta"]["title"] == "MS_REF_IMAGE"


def test_register_preset_stores_target_mode_and_returns_warnings(client):
    r = client.post(
        "/api/comfy/presets",
        json={
            "name": "h3",
            "kind": "ref2v",
            "workflow": _ref2v_wf(),
            "video_target": "minimax",
            "video_mode": "ref2va",
        },
    )
    assert r.status_code == 200
    assert any("MS_REF_IMAGE" in w for w in r.json()["warnings"])
    row = next(x for x in client.get("/api/comfy/presets").json() if x["name"] == "h3")
    assert row["video_target"] == "minimax" and row["video_mode"] == "ref2va"


def test_register_preset_rejects_unknown_target_and_validation_errors(client):
    r = client.post(
        "/api/comfy/presets",
        json={
            "name": "x",
            "kind": "ref2v",
            "workflow": _ref2v_wf(),
            "video_target": "wan",
        },
    )
    assert r.status_code == 400
    assert "video_target" in r.json()["detail"]

    wf = _ref2v_wf()
    del wf["2"]
    r = client.post(
        "/api/comfy/presets",
        json={"name": "broken2", "kind": "ref2v", "workflow": wf},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "validation_failed"
    assert any(f["code"] == "missing_required" for f in detail["findings"])


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


def test_deleting_a_preset_that_has_jobs_is_409(client):
    """Regression guard for final-review I2.

    `generation_jobs.preset_id` is NOT NULL REFERENCES
    workflow_presets(id) with PRAGMA foreign_keys = ON and no ON DELETE
    clause, so the DELETE raises sqlite3.IntegrityError the moment any
    job row exists — and nothing caught it, so the route 500'd for any
    preset that had ever been used.

    Task 3 tested deletion on a bare DB and Task 11 tested the route
    with a stub that created no jobs, so this combination -- a preset
    with real job rows, deleted through the real route -- never existed
    in one test before.
    """
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]

    for _ in range(2):
        assert (
            client.post(
                "/api/comfy/submit",
                json={
                    "preset_id": pid,
                    "positive": "a cat",
                    "seed": 1,
                    "width": 512,
                    "height": 512,
                    "batch_size": 1,
                },
            ).status_code
            == 200
        )

    r = client.delete(f"/api/comfy/presets/{pid}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "2" in detail  # names how many jobs are in the way
    assert str(pid) in detail
    # And the preset (with its history) is still there.
    assert any(row["id"] == pid for row in client.get("/api/comfy/presets").json())
    assert len(client.get("/api/comfy/jobs").json()) == 2


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


def test_submit_against_a_missing_preset_is_404(client):
    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": 9999,
            "positive": "a cat",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    )
    assert r.status_code == 404
    assert client.stub.submitted == []


def test_submit_with_an_unbindable_parameter_is_400(client):
    # t2i_workflow() has no MS_NEGATIVE node, so a negative prompt can't
    # be bound. Must fail synchronously with 400, not succeed and fail
    # the job later with a message that blames ComfyUI.
    pid = client.post(
        "/api/comfy/presets",
        json={"name": "sdxl", "kind": "t2i", "workflow": t2i_workflow()},
    ).json()["id"]

    r = client.post(
        "/api/comfy/submit",
        json={
            "preset_id": pid,
            "positive": "a cat",
            "negative": "blurry",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
        },
    )
    assert r.status_code == 400
    assert "MS_NEGATIVE" in r.json()["detail"]
    assert client.stub.submitted == []


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
