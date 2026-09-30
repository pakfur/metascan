"""The kind-level validator registry in workflow_validation, and its first
entry: kind "t2i" warns when a workflow cannot take LoRAs or a negative
prompt. Also proves the routes that surface those warnings."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import comfy as comfy_api
from metascan.core.comfy_bindings import resolve_bindings
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.workflow_validation import (
    _KIND_VALIDATORS,
    Finding,
    validate_workflow,
)


def _node(title: Optional[str], inputs: Optional[Dict[str, Any]] = None) -> dict:
    node: Dict[str, Any] = {"class_type": "Stub", "inputs": inputs or {}}
    if title is not None:
        node["_meta"] = {"title": title}
    return node


def _t2i_workflow(*, stack: bool = True, negative: bool = True) -> Dict[str, Any]:
    """The four required titles, plus whichever optional ones are asked for."""
    wf: Dict[str, Any] = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"seed": 0}),
        "3": _node("MS_LATENT", {"width": 512, "height": 512, "batch_size": 1}),
        "4": _node("MS_SAVE"),
        "9": _node(None),  # untitled bystander
    }
    if negative:
        wf["5"] = _node("MS_NEGATIVE", {"text": ""})
    if stack:
        wf["6"] = _node("MS_LORA_STACK")
    return wf


def _codes(report, level: Optional[str] = None) -> List[str]:
    return [f.code for f in report.findings if level is None or f.level == level]


# ---- the t2i kind validator ---------------------------------------------


def test_a_workflow_with_a_lora_stack_and_a_negative_is_clean():
    report = validate_workflow(_t2i_workflow(), "t2i")
    assert report.ok
    assert report.findings == []
    assert report.fixes == []


def test_a_missing_lora_stack_warns():
    report = validate_workflow(_t2i_workflow(stack=False), "t2i")
    assert _codes(report) == ["no_lora_stack"]
    finding = report.findings[0]
    assert finding.level == "warning"
    assert "MS_LORA_STACK" in finding.message
    assert finding.node_id is None


def test_a_missing_negative_warns():
    report = validate_workflow(_t2i_workflow(negative=False), "t2i")
    assert _codes(report) == ["no_negative"]
    finding = report.findings[0]
    assert finding.level == "warning"
    assert "MS_NEGATIVE" in finding.message
    assert finding.node_id is None


def test_missing_both_warns_twice_in_a_stable_order():
    report = validate_workflow(_t2i_workflow(stack=False, negative=False), "t2i")
    assert _codes(report) == ["no_lora_stack", "no_negative"]


def test_these_are_warnings_so_registration_is_never_blocked():
    report = validate_workflow(_t2i_workflow(stack=False, negative=False), "t2i")
    assert report.ok
    assert _codes(report, "error") == []


def test_a_missing_required_title_is_still_an_error_next_to_the_warnings():
    wf = _t2i_workflow(stack=False, negative=False)
    del wf["3"]  # MS_LATENT
    report = validate_workflow(wf, "t2i")
    assert not report.ok
    assert _codes(report, "error") == ["missing_required"]
    assert _codes(report, "warning") == ["no_lora_stack", "no_negative"]


def test_a_lora_stack_or_negative_titled_with_a_typo_still_warns():
    wf = _t2i_workflow(stack=False, negative=False)
    wf["6"] = _node("MS_LORA_STAK")
    report = validate_workflow(wf, "t2i")
    assert "no_lora_stack" in _codes(report, "warning")
    assert "unknown_title" in _codes(report, "warning")
    assert [f.title for f in report.fixes] == ["MS_LORA_STACK"]


def test_the_kind_warnings_run_whatever_the_target_and_mode():
    wf = _t2i_workflow(stack=False, negative=False)
    plain = validate_workflow(wf, "t2i")
    # A registered dialect adds its own warnings after the kind's.
    ref2va = validate_workflow(wf, "t2i", "minimax", "ref2va")
    assert _codes(ref2va)[:2] == ["no_lora_stack", "no_negative"]
    assert "no_ref_image_slot" in _codes(ref2va)
    # An unregistered pair adds only its no_validator note.
    fl2va = validate_workflow(wf, "t2i", "minimax", "fl2va")
    assert _codes(fl2va) == ["no_lora_stack", "no_negative", "no_validator"]
    assert _codes(plain) == ["no_lora_stack", "no_negative"]


# ---- other kinds are untouched ------------------------------------------


def test_ref2v_and_minimax_ref2va_behave_exactly_as_before():
    wf = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"noise_seed": 0}),
        "3": _node("MS_SAVE"),
    }
    assert validate_workflow(wf, "ref2v").findings == []
    report = validate_workflow(wf, "ref2v", "minimax", "ref2va")
    assert _codes(report) == ["no_ref_image_slot", "no_audio_slot", "no_duration"]


def test_ref2v_minimax_i2va_still_wants_its_own_things_only():
    wf = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"noise_seed": 0}),
        "3": _node("MS_SAVE"),
        "4": _node("MS_FIRST_FRAME", {"image": ""}),
    }
    report = validate_workflow(wf, "ref2v", "minimax", "i2va")
    assert _codes(report) == ["no_duration", "no_resolution", "no_lora_stack"]
    assert "no_negative" not in _codes(report)


def test_the_ref_kind_gets_no_t2i_warnings():
    wf = _t2i_workflow(stack=False, negative=False)
    wf["7"] = _node("MS_REF_IMAGE", {"image": ""})
    report = validate_workflow(wf, "ref")
    assert report.findings == []


def test_only_t2i_has_a_kind_validator():
    assert set(_KIND_VALIDATORS) == {"t2i"}


def test_a_registered_kind_validator_sees_the_workflow_and_the_title_map():
    seen: List[Any] = []

    def probe(workflow: Dict[str, Any], found: Dict[str, str]) -> List[Finding]:
        seen.append((workflow, dict(found)))
        return [Finding("warning", "probe", "probe ran")]

    wf = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"noise_seed": 0}),
        "3": _node("MS_SAVE"),
    }
    with patch.dict(_KIND_VALIDATORS, {"ref2v": probe}):
        report = validate_workflow(wf, "ref2v", "minimax", "ref2va")

    assert seen == [
        (wf, {"MS_POSITIVE": "1", "MS_SEED": "2", "MS_SAVE": "3"}),
    ]
    # after the generic pass, before the dialect's
    assert _codes(report)[0] == "probe"
    assert "no_ref_image_slot" in _codes(report)


# ---- the routes ---------------------------------------------------------


@pytest.fixture
def app_client():
    app = FastAPI()
    app.include_router(comfy_api.router)
    return TestClient(app)


def test_the_validate_route_returns_the_two_t2i_warnings(app_client):
    r = app_client.post(
        "/api/comfy/presets/validate",
        json={"kind": "t2i", "workflow": _t2i_workflow(stack=False, negative=False)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert [f["code"] for f in body["findings"]] == ["no_lora_stack", "no_negative"]
    assert {f["level"] for f in body["findings"]} == {"warning"}
    assert body["fixes"] == []
    assert "fixed_workflow" not in body


def test_the_validate_route_is_quiet_for_a_complete_t2i_workflow(app_client):
    r = app_client.post(
        "/api/comfy/presets/validate",
        json={"kind": "t2i", "workflow": _t2i_workflow()},
    )
    assert r.status_code == 200
    assert r.json()["findings"] == []


def test_the_validate_route_still_defaults_to_ref2v_without_the_t2i_warnings(
    app_client,
):
    wf = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"seed": 0}),
        "3": _node("MS_SAVE"),
    }
    r = app_client.post("/api/comfy/presets/validate", json={"workflow": wf})
    assert r.status_code == 200
    assert r.json()["findings"] == []


class _StubComfy:
    """Just enough ComfyClient for POST /api/comfy/presets."""

    def __init__(self) -> None:
        self.registered: List[Any] = []

    async def register_preset(
        self, name, kind, workflow, video_target=None, video_mode=None
    ):
        self.registered.append((name, kind, video_target, video_mode))
        return 41


def test_registering_a_t2i_preset_returns_the_warnings_and_saves_it(app_client):
    stub = _StubComfy()
    comfy_api.set_comfy_client(stub)
    try:
        r = app_client.post(
            "/api/comfy/presets",
            json={
                "name": "Krea2 T2I API",
                "kind": "t2i",
                "workflow": _t2i_workflow(stack=False, negative=True),
            },
        )
    finally:
        comfy_api.set_comfy_client(None)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 41
    assert len(body["warnings"]) == 1
    assert "MS_LORA_STACK" in body["warnings"][0]
    assert stub.registered == [("Krea2 T2I API", "t2i", None, None)]


def test_registering_a_t2i_preset_with_a_missing_required_title_is_refused(
    app_client,
):
    stub = _StubComfy()
    comfy_api.set_comfy_client(stub)
    wf = _t2i_workflow()
    del wf["3"]
    try:
        r = app_client.post(
            "/api/comfy/presets", json={"name": "x", "kind": "t2i", "workflow": wf}
        )
    finally:
        comfy_api.set_comfy_client(None)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "validation_failed"
    assert stub.registered == []


def test_updating_a_t2i_preset_in_place_reports_the_warnings_too():
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp))
        full = _t2i_workflow()
        preset = db.create_workflow_preset(
            "Krea2 T2I API",
            "t2i",
            json.dumps(full),
            resolve_bindings(full, "t2i").to_json(),
        )
        app = FastAPI()
        app.include_router(comfy_api.router)
        with patch("backend.api.comfy.get_db", lambda: db):
            client = TestClient(app)
            bare = _t2i_workflow(stack=False, negative=False)
            r = client.put(f"/api/comfy/presets/{preset}", json={"workflow": bare})
            assert r.status_code == 200, r.text
            assert len(r.json()["warnings"]) == 2
            r = client.put(f"/api/comfy/presets/{preset}", json={"workflow": full})
            assert r.status_code == 200, r.text
            assert r.json()["warnings"] == []
        stored = db.get_workflow_preset(preset)
        assert json.loads(stored["workflow_json"]) == full
        assert stored["kind"] == "t2i"
