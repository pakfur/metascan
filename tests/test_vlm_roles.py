"""Writer and general VLM roles (taste project 3, task 1).

The writer role serves T2I / I2V prompt writing; the general role serves
tagging, retag, chat, the prompt playground and storyboards. Both roles
naming the same model share one VlmClient (one llama-server). No real
llama-server is ever started here: clients use ``spawn_override`` or fakes.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import vlm as vlm_api
from backend.services.vlm_roles import (
    VlmRoles,
    WriterModelNotAllowed,
    persist_role_model,
    resolve_role_models,
)
from metascan.core.vlm_client import VlmClient
from tests._fake_llama_server import FakeLlamaServer


class _FakeVlm:
    """Duck-typed VlmClient: records shutdowns, never spawns anything."""

    def __init__(self) -> None:
        self.preferred_model_id: Optional[str] = None
        self.model_id: Optional[str] = None
        self.state = "idle"
        self.shutdowns = 0

    async def swap_model(self, model_id: str) -> None:
        self.model_id = model_id
        self.state = "ready"

    async def shutdown(self) -> None:
        self.shutdowns += 1
        self.model_id = None
        self.state = "stopped"

    def snapshot(self) -> Dict[str, Any]:
        return {"state": self.state, "model_id": self.model_id}


def _fake_roles(writer: str, general: str, **kw: Any) -> VlmRoles:
    return VlmRoles(writer=writer, general=general, client_factory=_FakeVlm, **kw)


def _loaded(roles: VlmRoles) -> None:
    """Mark every distinct fake client as having its model loaded."""
    for c in roles.distinct_clients():
        c.model_id = c.preferred_model_id
        c.state = "ready"


@pytest.fixture
def roles() -> VlmRoles:
    return VlmRoles(
        writer="qwen3vl-8b",
        general="qwen3vl-8b",
        client_factory=lambda: VlmClient(spawn_override=lambda mid: "http://x"),
    )


# ---------------------------------------------------------------------------
# Sharing and distinct clients
# ---------------------------------------------------------------------------


def test_same_model_shares_one_client(roles: VlmRoles) -> None:
    assert roles.client("writer") is roles.client("general")
    assert len(roles.distinct_clients()) == 1
    assert roles.model_id("writer") == roles.model_id("general") == "qwen3vl-8b"
    assert roles.snapshot()["shared"] is True
    # The client knows which model its role(s) want, for pick_vlm_model.
    assert roles.client("writer").preferred_model_id == "qwen3vl-8b"


async def test_two_models_two_clients() -> None:
    async with FakeLlamaServer() as fake_a, FakeLlamaServer() as fake_b:
        urls = {"qwen3vl-8b": fake_a.base_url, "qwen38-27b": fake_b.base_url}
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=lambda: VlmClient(spawn_override=lambda mid: urls[mid]),
        )
        try:
            writer, general = roles.client("writer"), roles.client("general")
            assert writer is not general
            assert len(roles.distinct_clients()) == 2
            await writer.ensure_started(roles.model_id("writer"))
            await general.ensure_started(roles.model_id("general"))
            assert writer.base_url != general.base_url
            assert writer.model_id == "qwen3vl-8b"
            assert general.model_id == "qwen38-27b"
            assert roles.snapshot()["shared"] is False
        finally:
            await roles.shutdown_all()


async def test_set_model_rebuilds_so_equal_ids_share() -> None:
    saved: List[tuple] = []
    roles = _fake_roles(
        "qwen3vl-8b", "qwen38-27b", save_model=lambda r, m: saved.append((r, m))
    )
    old_general = roles.client("general")
    _loaded(roles)
    await roles.set_model("general", "qwen3vl-8b")
    assert roles.client("general") is roles.client("writer")
    assert len(roles.distinct_clients()) == 1
    # The client no role needs any more is shut down (its VRAM freed).
    assert old_general.shutdowns == 1
    assert roles.client("writer").shutdowns == 0
    assert saved == [("general", "qwen3vl-8b")]

    # Splitting again builds a fresh client for the new id.
    await roles.set_model("general", "qwen3vl-4b")
    assert roles.client("general") is not roles.client("writer")
    assert roles.client("general").preferred_model_id == "qwen3vl-4b"


async def test_set_model_rejects_27b_writer_and_unknown_ids() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen3vl-8b")
    with pytest.raises(WriterModelNotAllowed):
        await roles.set_model("writer", "qwen38-27b")
    with pytest.raises(KeyError):
        await roles.set_model("general", "no-such-model")
    assert roles.model_id("writer") == "qwen3vl-8b"
    # 27B is fine for the general role.
    await roles.set_model("general", "qwen38-27b")
    assert roles.model_id("general") == "qwen38-27b"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_resolve_defaults_writer_to_8b_when_installed() -> None:
    r = resolve_role_models(
        None, None, installed=lambda m: True, general_default=lambda: "qwen38-27b"
    )
    assert (r.writer, r.general, r.writer_falls_back) == (
        "qwen3vl-8b",
        "qwen38-27b",
        False,
    )


def test_resolve_writer_falls_back_to_general_without_8b() -> None:
    r = resolve_role_models(
        None,
        None,
        installed=lambda m: m != "qwen3vl-8b",
        general_default=lambda: "qwen3vl-4b",
    )
    assert (r.writer, r.general, r.writer_falls_back) == (
        "qwen3vl-4b",
        "qwen3vl-4b",
        True,
    )


def test_resolve_never_makes_27b_the_writer() -> None:
    # Configured 27B writer is ignored; the fallback-to-general path also
    # refuses the 27B general model.
    r = resolve_role_models(
        "qwen38-27b",
        "qwen38-27b",
        installed=lambda m: m != "qwen3vl-8b",
        general_default=lambda: None,
    )
    assert r.writer != "qwen38-27b"
    assert r.general == "qwen38-27b"


def test_resolve_honours_configured_ids() -> None:
    r = resolve_role_models(
        "qwen3vl-4b",
        "qwen3vl-2b",
        installed=lambda m: True,
        general_default=lambda: "qwen38-27b",
    )
    assert (r.writer, r.general, r.writer_falls_back) == (
        "qwen3vl-4b",
        "qwen3vl-2b",
        False,
    )


def test_pick_vlm_model_uses_the_role_preference() -> None:
    from metascan.core.vlm_select import pick_vlm_model

    vlm = SimpleNamespace(model_id=None, preferred_model_id="qwen3vl-4b")
    assert pick_vlm_model(vlm, role="writer") == "qwen3vl-4b"
    # Without a preference the writer never lands on a writer-excluded model.
    bare = SimpleNamespace(model_id=None)
    with mock.patch(
        "metascan.core.vlm_select._recommended_vlm_gate", return_value="qwen38-27b"
    ):
        assert pick_vlm_model(bare, role="writer") == "qwen3vl-8b"
        assert pick_vlm_model(bare) == "qwen38-27b"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    roles = _fake_roles("qwen3vl-8b", "qwen3vl-8b", save_model=lambda r, m: None)
    app = FastAPI()
    app.include_router(vlm_api.router)
    vlm_api.set_vlm_roles(roles)
    try:
        with mock.patch.object(vlm_api, "vlm_model_installed", return_value=True):
            yield TestClient(app), roles
    finally:
        vlm_api.set_vlm_client(None)


def test_27b_rejected_as_writer(client) -> None:
    c, roles = client
    r = c.post("/api/vlm/roles", json={"role": "writer", "model_id": "qwen38-27b"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "writer_model_not_allowed"
    assert roles.model_id("writer") == "qwen3vl-8b"


def test_roles_get_and_post(client) -> None:
    c, roles = client
    body = c.get("/api/vlm/roles").json()
    assert body["writer"]["model_id"] == "qwen3vl-8b"
    assert body["general"]["model_id"] == "qwen3vl-8b"
    assert body["shared"] is True
    assert "state" in body["writer"] and "state" in body["general"]

    r = c.post("/api/vlm/roles", json={"role": "general", "model_id": "qwen38-27b"})
    assert r.status_code == 200
    assert r.json()["general"]["model_id"] == "qwen38-27b"
    assert r.json()["shared"] is False

    r = c.post("/api/vlm/roles", json={"role": "general", "model_id": "nope"})
    assert r.status_code == 404

    with mock.patch.object(vlm_api, "vlm_model_installed", return_value=False):
        r = c.post("/api/vlm/roles", json={"role": "writer", "model_id": "qwen3vl-4b"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "model_not_installed"


def test_active_without_role_targets_general(client) -> None:
    c, roles = client
    r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-4b"})
    assert r.status_code == 200
    assert r.json()["model_id"] == "qwen3vl-4b"
    assert roles.model_id("general") == "qwen3vl-4b"
    assert roles.model_id("writer") == "qwen3vl-8b"
    r = c.post("/api/vlm/active", json={"model_id": "qwen38-27b", "role": "writer"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_t2i_uses_writer_tagging_uses_general() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b")
    vlm_api.set_vlm_roles(roles)
    try:
        assert vlm_api.get_writer_vlm() is roles.client("writer")
        assert vlm_api.get_vlm_client() is roles.client("general")
        assert vlm_api.get_vlm_client("writer") is roles.client("writer")
        assert vlm_api.get_all_vlm_clients() == roles.distinct_clients()
    finally:
        vlm_api.set_vlm_client(None)

    import backend.main as main

    source = inspect.getsource(main.lifespan)
    t2i = source[source.index("T2iRunner(") : source.index("t2i_runner.on_event")]
    i2v = source[source.index("I2vRunner(") : source.index("i2v_runner.on_event")]
    sb = source[
        source.index("StoryboardRunner(") : source.index("storyboard_runner.on_event")
    ]
    assert "get_vlm=get_writer_vlm" in t2i
    assert "get_all_vlms=get_all_vlm_clients" in t2i
    assert "get_vlm=get_writer_vlm" in i2v
    assert "get_vlm=get_vlm_client" in sb
    assert "get_all_vlms=get_all_vlm_clients" in sb

    # Chat, the prompt playground, storyboard describe and the tag pump all
    # ask for the default (general) client.
    import backend.api.chat as chat
    import backend.api.prompt as prompt
    import backend.api.similarity as similarity

    for mod in (chat, prompt):
        assert "get_vlm_client()" in inspect.getsource(mod._require_ready_client)
    assert "get_vlm_client()" in inspect.getsource(
        similarity._schedule_vlm_drain_after_complete
    )


# ---------------------------------------------------------------------------
# Unload for ComfyUI
# ---------------------------------------------------------------------------


def _t2i_runner(roles: VlmRoles) -> Any:
    from metascan.core.t2i_runner import T2iRunner

    return T2iRunner(
        db=None,
        comfy=None,
        get_vlm=lambda: roles.client("writer"),
        get_all_vlms=roles.distinct_clients,
        captions=None,  # type: ignore[arg-type]
        library=None,  # type: ignore[arg-type]
        output_root=Path("."),
        get_config=lambda: {},
    )


@pytest.mark.parametrize(
    "writer,general,expected_shutdowns",
    [("qwen3vl-8b", "qwen3vl-8b", [1]), ("qwen3vl-8b", "qwen38-27b", [1, 1])],
)
async def test_unload_shuts_each_distinct_client_once(
    writer: str, general: str, expected_shutdowns: List[int]
) -> None:
    roles = _fake_roles(writer, general)
    _loaded(roles)
    runner = _t2i_runner(roles)
    batch = SimpleNamespace(mode="random", prompting=False)
    await runner._unload_vlm(batch)
    assert [c.shutdowns for c in roles.distinct_clients()] == expected_shutdowns


@pytest.mark.parametrize("shared", [True, False])
async def test_unload_guard_covers_writer(shared: bool) -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen3vl-8b" if shared else "qwen38-27b")
    _loaded(roles)
    runner = _t2i_runner(roles)
    batch = SimpleNamespace(mode="random", prompting=False)
    other = SimpleNamespace(mode="random", prompting=True)
    runner._batches["other"] = other
    await runner._unload_vlm(batch)
    # Another Random batch is still writing prompts: the writer stays up.
    assert roles.client("writer").shutdowns == 0
    if not shared:
        # The general instance is not writing prompts: it still goes.
        assert roles.client("general").shutdowns == 1


async def test_shutdown_all_shuts_each_distinct_client_once() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen3vl-8b")
    await roles.shutdown_all()
    assert roles.client("writer").shutdowns == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"models": {"preload_at_startup": ["clip-small"]}}))
    with mock.patch("backend.config.get_config_path", return_value=path):
        yield path


async def test_roles_persist_to_config(tmp_config: Path) -> None:
    from backend.config import get_vlm_roles_config, load_app_config

    roles = _fake_roles("qwen3vl-8b", "qwen3vl-8b", save_model=persist_role_model)
    await roles.set_model("general", "qwen3vl-4b")
    await roles.set_model("writer", "qwen3vl-2b")
    cfg = json.loads(tmp_config.read_text())
    assert cfg["models"]["general_vlm"] == "qwen3vl-4b"
    assert cfg["models"]["writer_vlm"] == "qwen3vl-2b"
    # Other keys survive.
    assert cfg["models"]["preload_at_startup"] == ["clip-small"]
    assert get_vlm_roles_config(load_app_config()) == {
        "writer_vlm": "qwen3vl-2b",
        "general_vlm": "qwen3vl-4b",
    }


def test_roles_config_missing_keys_are_none() -> None:
    from backend.config import get_vlm_roles_config

    assert get_vlm_roles_config({}) == {"writer_vlm": None, "general_vlm": None}


# ---------------------------------------------------------------------------
# Models tab status
# ---------------------------------------------------------------------------


def test_status_rows_expose_roles() -> None:
    from backend.api import models as models_api

    roles = _fake_roles("qwen3vl-4b", "qwen3vl-4b", writer_falls_back=True)
    vlm_api.set_vlm_roles(roles)
    try:
        rows = {r["id"]: r for r in models_api._vlm_status_rows([])}
        payload = models_api._vlm_roles_payload()
    finally:
        vlm_api.set_vlm_client(None)
    assert rows["qwen3vl-4b"]["roles"] == ["writer", "general"]
    assert rows["qwen3vl-8b"]["roles"] == []
    assert rows["qwen38-27b"]["writer_allowed"] is False
    assert rows["qwen3vl-8b"]["writer_allowed"] is True
    assert payload["writer_falls_back"] is True
    assert payload["note"] == "writer falls back to general"
    assert payload["writer"]["model_id"] == "qwen3vl-4b"


def test_from_config_general_defaults_to_the_preloaded_vlm() -> None:
    # Before roles, a preloaded VLM was the loaded model pick_vlm_model
    # chose; an unset general role keeps that pick.
    cfg = {"models": {"preload_at_startup": ["clip-small", "qwen3vl-4b"]}}
    with mock.patch(
        "backend.services.vlm_roles.default_general_model",
        return_value="qwen38-27b",
    ), mock.patch("metascan.core.vlm_select.vlm_model_installed", return_value=True):
        roles = VlmRoles.from_config(cfg, client_factory=_FakeVlm, save_model=None)
    assert roles.model_id("general") == "qwen3vl-4b"
    assert roles.model_id("writer") == "qwen3vl-8b"


def test_from_config_survives_a_failing_hardware_probe() -> None:
    with mock.patch(
        "backend.services.vlm_roles.default_general_model",
        side_effect=RuntimeError("probe failed"),
    ), mock.patch("metascan.core.vlm_select.vlm_model_installed", return_value=False):
        roles = VlmRoles.from_config({}, client_factory=_FakeVlm, save_model=None)
    assert roles.model_id("general") is None
    assert roles.model_id("writer") == "qwen3vl-8b"
