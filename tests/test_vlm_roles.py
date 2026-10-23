"""Writer and general VLM roles (taste project 3, task 1).

The writer role serves T2I / I2V prompt writing; the general role serves
tagging, retag, chat, the prompt playground and storyboards. Both roles
naming the same model share one VlmClient (one llama-server). No real
llama-server is ever started here: clients use ``spawn_override`` or fakes.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
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
from metascan.core.vlm_client import VlmClient, VlmError
from tests._fake_llama_server import FakeLlamaServer, fake_llama_command


class _FakeVlm:
    """Duck-typed VlmClient: records shutdowns, never spawns anything.

    ``ensure_started`` honours ``retired`` and calls ``before_spawn`` like
    the real client; ``begin``/``end`` simulate an in-flight request."""

    def __init__(self) -> None:
        self.preferred_model_id: Optional[str] = None
        self.model_id: Optional[str] = None
        self.state = "idle"
        self.shutdowns = 0
        self.starts = 0
        self.retired = False
        self.swapped_out = False
        self.before_spawn: Any = None
        self.in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def ensure_started(self, model_id: str) -> None:
        if self.retired:
            raise VlmError("client retired")
        if self.model_id == model_id and self.state == "ready":
            return
        if self.before_spawn is not None:
            await self.before_spawn(self)
        self.starts += 1
        self.model_id = model_id
        self.state = "ready"

    async def swap_model(self, model_id: str) -> None:
        await self.ensure_started(model_id)

    def begin(self) -> None:
        self.in_flight += 1
        self._idle.clear()

    def end(self) -> None:
        self.in_flight -= 1
        if self.in_flight == 0:
            self._idle.set()

    async def wait_loaded(self, timeout: float) -> bool:
        return True

    async def wait_idle(self, timeout: float, grace: float = 0.0) -> bool:
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def shutdown(self) -> None:
        self.shutdowns += 1
        self.model_id = None
        self.state = "stopped"
        self.swapped_out = False

    async def evict(self) -> None:
        await self.shutdown()
        self.swapped_out = True

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
    # Configured 27B writer is ignored; with the 8B missing the writer does
    # not fall back to the 27B general model either: it stays the (missing)
    # 8B, flagged so the UI and I2V can say "download it".
    r = resolve_role_models(
        "qwen38-27b",
        "qwen38-27b",
        installed=lambda m: m != "qwen3vl-8b",
        general_default=lambda: None,
    )
    assert r.writer == "qwen3vl-8b"
    assert r.general == "qwen38-27b"
    assert r.writer_falls_back is False
    assert r.writer_missing is True


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

    roles = VlmRoles(
        writer=None,
        general="qwen3vl-4b",
        client_factory=_FakeVlm,
        installed=lambda m: m != "qwen3vl-8b",
    )
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
    assert payload is not None
    assert payload["writer_falls_back"] is True
    assert "writer falls back to general" in payload["notes"]
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


# ---------------------------------------------------------------------------
# Fix round 1: retired clients, swap on demand, missing writer, /active
# ---------------------------------------------------------------------------


async def test_a_retired_client_never_spawns() -> None:
    spawned: List[str] = []

    def spawn(mid: str) -> str:
        spawned.append(mid)
        return "http://127.0.0.1:1"

    client = VlmClient(spawn_override=spawn)
    client.retired = True
    with pytest.raises(VlmError, match="client retired"):
        await client.ensure_started("qwen3vl-8b")
    with pytest.raises(VlmError, match="client retired"):
        await client.start("qwen3vl-8b", wait_ready=False)
    assert spawned == []


async def test_set_model_retires_the_replaced_client() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b")
    old_general = roles.client("general")
    await roles.set_model("general", "qwen3vl-8b")
    assert old_general.retired is True
    assert roles.client("general").retired is False
    # A caller still holding the old client cannot bring it back.
    with pytest.raises(VlmError):
        await old_general.ensure_started("qwen38-27b")
    assert old_general.starts == 0


async def test_real_client_calls_before_spawn() -> None:
    seen: List[Any] = []

    async def hook(c: Any) -> None:
        seen.append(c)

    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.before_spawn = hook
    await client.start("qwen3vl-8b", wait_ready=False)
    try:
        assert seen == [client]
    finally:
        await client.shutdown()


async def test_real_client_wait_idle_tracks_requests() -> None:
    client = VlmClient()
    assert await client.wait_idle(0.01) is True
    client._begin_request()
    assert client.in_flight == 1
    assert await client.wait_idle(0.01) is False
    client._end_request()
    assert await client.wait_idle(0.01) is True


async def test_two_models_that_fit_stay_co_resident() -> None:
    # 9.5 + 22.0 = 31.5 GB <= 40 - 2.
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b", vram_gb=40.0)
    assert roles.co_resident is True
    await roles.client("writer").ensure_started("qwen3vl-8b")
    await roles.client("general").ensure_started("qwen38-27b")
    assert roles.client("writer").shutdowns == 0
    assert roles.client("general").shutdowns == 0
    assert roles.snapshot()["co_resident"] is True


async def test_two_models_that_do_not_fit_take_turns() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b", vram_gb=24.0)
    assert roles.co_resident is False
    writer, general = roles.client("writer"), roles.client("general")
    await writer.ensure_started("qwen3vl-8b")
    await general.ensure_started("qwen38-27b")
    assert writer.shutdowns == 1 and writer.state == "stopped"
    assert general.state == "ready"
    await writer.ensure_started("qwen3vl-8b")
    assert general.shutdowns == 1 and general.state == "stopped"
    # Neither role changed model: only residency.
    assert roles.model_id("writer") == "qwen3vl-8b"
    assert roles.model_id("general") == "qwen38-27b"
    snap = roles.snapshot()
    assert snap["co_resident"] is False
    assert (
        "writer and tagging models take turns — they don't fit in VRAM together"
        in snap["notes"]
    )


def test_unknown_vram_is_not_co_resident_but_shared_is() -> None:
    assert _fake_roles("qwen3vl-8b", "qwen3vl-4b", vram_gb=None).co_resident is False
    assert _fake_roles("qwen3vl-8b", "qwen3vl-8b", vram_gb=None).co_resident is True


async def test_taking_turns_waits_for_in_flight_requests() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b", vram_gb=24.0)
    writer, general = roles.client("writer"), roles.client("general")
    await general.ensure_started("qwen38-27b")
    general.begin()

    async def finish() -> None:
        await asyncio.sleep(0.05)
        assert general.shutdowns == 0  # not shut down mid-request
        general.end()

    await asyncio.gather(writer.ensure_started("qwen3vl-8b"), finish())
    assert general.shutdowns == 1
    assert writer.state == "ready"


async def test_the_in_flight_wait_is_bounded() -> None:
    roles = _fake_roles("qwen3vl-8b", "qwen38-27b", vram_gb=24.0, idle_timeout_s=0.05)
    writer, general = roles.client("writer"), roles.client("general")
    await general.ensure_started("qwen38-27b")
    general.begin()  # never ends
    t0 = time.monotonic()
    await writer.ensure_started("qwen3vl-8b")
    assert time.monotonic() - t0 < 1.0
    assert general.shutdowns == 1


def test_vram_budget_from_the_hardware_report() -> None:
    from backend.services.vlm_roles import vram_budget_gb
    from metascan.core.hardware import CudaInfo, HardwareReport

    cuda = HardwareReport(cuda=CudaInfo(name="x", vram_gb=24.0, capability="8.6"))
    assert vram_budget_gb(cuda) == 24.0
    assert vram_budget_gb(HardwareReport(mps=True, ram_gb=64.0)) == 64.0
    assert vram_budget_gb(HardwareReport(ram_gb=64.0)) is None


async def test_missing_writer_is_noted_and_refresh_picks_up_a_download() -> None:
    installed = {"qwen38-27b"}
    roles = VlmRoles(
        writer=None,
        general="qwen38-27b",
        client_factory=_FakeVlm,
        installed=lambda m: m in installed,
    )
    assert roles.model_id("writer") == "qwen3vl-8b"
    assert roles.writer_falls_back is False
    snap = roles.snapshot()
    assert "prompt writer qwen3vl-8b not installed — download it in Models" in (
        snap["notes"]
    )
    installed.add("qwen3vl-8b")
    await roles.refresh()
    assert not any("not installed" in n for n in roles.snapshot()["notes"])


async def test_writer_falls_back_is_dynamic() -> None:
    installed = {"qwen3vl-4b", "qwen3vl-2b"}
    roles = VlmRoles(
        writer=None,
        general="qwen3vl-4b",
        client_factory=_FakeVlm,
        installed=lambda m: m in installed,
    )
    assert roles.writer_falls_back is True
    assert roles.model_id("writer") == "qwen3vl-4b"
    # Re-pointing general moves the unset writer along with it.
    await roles.set_model("general", "qwen3vl-2b")
    assert roles.model_id("writer") == "qwen3vl-2b"
    # Downloading the 8B ends the fallback without a restart.
    installed.add("qwen3vl-8b")
    await roles.refresh()
    assert roles.writer_falls_back is False
    assert roles.model_id("writer") == "qwen3vl-8b"


async def test_i2v_refuses_a_missing_writer_model(tmp_path: Path) -> None:
    from metascan.core.i2v_runner import I2vRunner, I2vUnavailableError

    image = tmp_path / "x.png"
    image.write_bytes(b"not really a png")
    vlm = _FakeVlm()
    vlm.preferred_model_id = "qwen3vl-8b"
    vlm.is_image_path = lambda p: True  # type: ignore[attr-defined]
    runner = I2vRunner(
        db=None,
        comfy=None,
        get_vlm=lambda: vlm,
        output_root=tmp_path,
        vlm_installed=lambda m: False,
    )
    with pytest.raises(I2vUnavailableError, match="download it in Models"):
        await runner.generate_prompt(str(image), "an idea", 6.0)
    assert vlm.starts == 0


async def test_t2i_warning_names_the_missing_writer() -> None:
    roles = VlmRoles(
        writer=None,
        general="qwen38-27b",
        client_factory=_FakeVlm,
        installed=lambda m: m == "qwen38-27b",
    )
    runner = _t2i_runner(roles)
    runner.vlm_installed = lambda m: m == "qwen38-27b"
    model_id, warning = await runner._usable_model(roles.client("writer"))
    assert model_id is None
    assert warning is not None
    assert "prompt writer qwen3vl-8b not installed — download it in Models" in warning


def test_active_checks_installation(client) -> None:
    c, roles = client
    with mock.patch.object(vlm_api, "vlm_model_installed", return_value=False):
        r = c.post("/api/vlm/active", json={"model_id": "qwen3vl-4b"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "model_not_installed"
    assert roles.model_id("general") == "qwen3vl-8b"


async def test_activate_persists_only_after_the_swap_succeeds() -> None:
    saved: List[tuple] = []
    roles = _fake_roles(
        "qwen3vl-8b", "qwen3vl-8b", save_model=lambda r, m: saved.append((r, m))
    )

    async def failing(c: Any) -> None:
        raise RuntimeError("spawn failed")

    with pytest.raises(RuntimeError):
        await roles.activate("general", "qwen3vl-4b", failing)
    assert saved == []
    assert roles.model_id("general") == "qwen3vl-8b"
    assert roles.shared is True

    async def ok(c: Any) -> None:
        await c.swap_model("qwen3vl-4b")

    await roles.activate("general", "qwen3vl-4b", ok)
    assert saved == [("general", "qwen3vl-4b")]
    assert roles.client("general").model_id == "qwen3vl-4b"


# ---------------------------------------------------------------------------
# Fix round 2: general requests survive the writer taking over
# ---------------------------------------------------------------------------


class _BlockingDb:
    """Records tag writes; the first write blocks (in its worker thread)
    until the test lets it go, so the writer can take over mid-batch."""

    def __init__(self) -> None:
        self.tagged: List[tuple] = []
        self.first = threading.Event()
        self.resume = threading.Event()

    def add_tag_indices(self, path: Any, tags: List[str], source: str) -> None:
        self.tagged.append((str(path), list(tags)))
        if len(self.tagged) == 1:
            self.first.set()
            self.resume.wait(10)


async def test_writer_taking_over_mid_tag_batch_loses_no_items(
    tmp_path: Path,
) -> None:
    from PIL import Image

    from backend.services.vlm_tag_pump import VlmTagPump

    images = []
    for i in range(4):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", (8, 8), (i * 40, 0, 0)).save(p)
        images.append(p)
    (tmp_path / "vlm_pending.jsonl").write_text(
        "".join(json.dumps({"path": str(p)}) + "\n" for p in images)
    )
    tags = '["red car", "blue sky"]'
    async with FakeLlamaServer(canned_response=tags) as w, FakeLlamaServer(
        canned_response=tags
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=lambda: VlmClient(spawn_override=lambda mid: urls[mid]),
            vram_gb=24.0,  # 9.5 + 22 does not fit: the roles take turns
            swap_grace_s=0.0,
        )
        writer, general = roles.client("writer"), roles.client("general")
        db = _BlockingDb()
        pump = VlmTagPump(
            queue_dir=tmp_path,
            client=general,
            db=db,
            model_id="qwen38-27b",
            concurrency=1,
        )
        try:
            task = asyncio.create_task(pump.drain_once())
            loop = asyncio.get_running_loop()
            assert await loop.run_in_executor(None, db.first.wait, 10)
            # The writer takes the GPU while the batch is between items.
            await writer.ensure_started("qwen3vl-8b")
            assert general.state == "stopped" and general.swapped_out is True
            await writer.generate_text(system_prompt="s", user_prompt="u")
            db.resume.set()
            await asyncio.wait_for(task, 30)
            assert len(db.tagged) == 4
            assert all(t for _, t in db.tagged), db.tagged  # none failed / empty
            # The general model swapped back in, so the writer went out.
            assert general.state == "ready"
            assert writer.state == "stopped" and writer.swapped_out is True
        finally:
            db.resume.set()
            await roles.shutdown_all()


async def test_a_client_stopped_by_hand_is_not_restarted_by_a_request(
    tmp_path: Path,
) -> None:
    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.preferred_model_id = "qwen3vl-8b"
    with pytest.raises(RuntimeError, match="not ready"):
        await client.generate_tags(tmp_path / "x.png")


async def test_an_eviction_during_a_request_is_retried() -> None:
    from metascan.core.vlm_client import STATE_READY

    client = VlmClient()
    client.preferred_model_id = "qwen3vl-8b"
    client._state = STATE_READY
    started: List[str] = []

    async def ensure(*, since: Optional[int] = None) -> None:
        started.append(str(client.preferred_model_id))
        client._state = STATE_READY
        client._stopping = False

    calls: List[int] = []

    async def impl(path: Any, *, timeout: float = 60.0) -> List[str]:
        calls.append(1)
        if len(calls) == 1:
            await client.evict()  # forced swap while the request ran
            return []
        return ["tag"]

    client._ensure_resident = ensure  # type: ignore[method-assign]
    client._generate_tags_impl = impl  # type: ignore[method-assign]
    assert await client.generate_tags(Path("x.png")) == ["tag"]
    assert started == ["qwen3vl-8b"]
    assert len(calls) == 2


async def test_wait_idle_waits_out_loading_and_the_grace() -> None:
    from metascan.core.vlm_client import STATE_LOADING, STATE_READY

    client = VlmClient()
    client._state = STATE_LOADING
    assert await client.wait_idle(0.05) is False
    client._state = STATE_READY
    client._begin_request()
    client._end_request()
    assert await client.wait_idle(0.01, grace=0.3) is False
    assert await client.wait_idle(1.0, grace=0.05) is True


@pytest.mark.parametrize("module", ["chat", "prompt"])
def test_chat_and_playground_accept_a_swapped_out_client(module: str) -> None:
    import importlib

    from fastapi import HTTPException

    mod = importlib.import_module(f"backend.api.{module}")
    vlm_api.set_vlm_client(SimpleNamespace(state="stopped", swapped_out=True))
    try:
        assert mod._require_ready_client() is not None
        vlm_api.set_vlm_client(SimpleNamespace(state="stopped", swapped_out=False))
        with pytest.raises(HTTPException):
            mod._require_ready_client()
    finally:
        vlm_api.set_vlm_client(None)


# ---------------------------------------------------------------------------
# Fix round 3: real shutdown windows, slow loads, explicit unload wins
# ---------------------------------------------------------------------------

_TAGS = '["red car", "blue sky"]'


def _png(tmp_path: Path) -> Path:
    from PIL import Image

    p = tmp_path / "img.png"
    Image.new("RGB", (8, 8), (200, 0, 0)).save(p)
    return p


def _proc_factory(opts: Dict[str, Dict[str, Any]]) -> Any:
    """Real VlmClients that Popen the fake llama-server (real terminate,
    proc.wait and -- with term_delay_ms -- a slow shutdown)."""

    def make() -> VlmClient:
        c = VlmClient()
        c._build_command = (  # type: ignore[method-assign]
            lambda spec, port: fake_llama_command(port, **opts[spec.model_id])
        )
        return c

    return make


def _slow_loading_factory(urls: Dict[str, str], load_s: float, spawned: List[str]):
    """Real VlmClients on fake servers whose model load takes ``load_s``."""

    def make() -> VlmClient:
        def spawn(mid: str) -> str:
            spawned.append(mid)
            return urls[mid]

        c = VlmClient(spawn_override=spawn)
        probe = c._health_probe_loop

        async def slow_probe() -> None:
            await asyncio.sleep(load_s)
            await probe()

        c._health_probe_loop = slow_probe  # type: ignore[method-assign]
        return c

    return make


async def test_forced_eviction_during_a_slow_shutdown_never_empties_tags(
    tmp_path: Path,
) -> None:
    img = _png(tmp_path)
    opts = {
        "qwen3vl-8b": {"canned_response": '"a prompt"'},
        "qwen38-27b": {
            "canned_response": _TAGS,
            "reply_delay_ms": 1500,
            "term_delay_ms": 1000,
        },
    }
    roles = VlmRoles(
        writer="qwen3vl-8b",
        general="qwen38-27b",
        client_factory=_proc_factory(opts),
        vram_gb=24.0,
        idle_timeout_s=0.2,  # the writer will not wait out the slow reply
        swap_grace_s=0.0,
    )
    writer, general = roles.client("writer"), roles.client("general")
    try:
        await general.ensure_started("qwen38-27b")
        in_flight = asyncio.create_task(general.generate_tags(img))
        await asyncio.sleep(0.3)
        assert general.in_flight == 1
        take_over = asyncio.create_task(writer.ensure_started("qwen3vl-8b"))
        # A request that starts inside the eviction's shutdown window.
        while not general._stopping:
            await asyncio.sleep(0.01)
        late = asyncio.create_task(general.generate_tags(img))
        await asyncio.wait_for(take_over, 30)
        tags_a = await asyncio.wait_for(in_flight, 60)
        tags_b = await asyncio.wait_for(late, 60)
        assert tags_a and tags_b, (tags_a, tags_b)
    finally:
        await roles.shutdown_all()


async def test_a_load_slower_than_the_idle_timeout_is_not_evicted(
    tmp_path: Path,
) -> None:
    img = _png(tmp_path)
    async with FakeLlamaServer(canned_response=_TAGS) as w, FakeLlamaServer(
        canned_response=_TAGS
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        spawned: List[str] = []
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=_slow_loading_factory(urls, 0.6, spawned),
            vram_gb=24.0,
            idle_timeout_s=0.1,  # every load takes 6x the idle timeout
            swap_grace_s=0.05,
        )
        writer, general = roles.client("writer"), roles.client("general")
        try:
            await general.ensure_started("qwen38-27b")

            wrote = asyncio.Event()

            async def tag_loop() -> List[List[str]]:
                # Tagging wants the GPU the whole time the writer loads.
                out: List[List[str]] = []
                while not wrote.is_set() and len(out) < 500:
                    out.append(await general.generate_tags(img))
                for _ in range(2):
                    out.append(await general.generate_tags(img))
                return out

            async def write() -> str:
                try:
                    await writer.ensure_started("qwen3vl-8b")
                    return await writer.generate_text(
                        system_prompt="s", user_prompt="u"
                    )
                finally:
                    wrote.set()

            tags, text = await asyncio.wait_for(asyncio.gather(tag_loop(), write()), 60)
            assert all(tags), tags
            assert text
        finally:
            await roles.shutdown_all()


async def test_an_explicit_unload_during_an_eviction_wins(tmp_path: Path) -> None:
    img = _png(tmp_path)
    opts = {
        "qwen3vl-8b": {"canned_response": '"a prompt"'},
        "qwen38-27b": {"canned_response": _TAGS, "term_delay_ms": 800},
    }
    roles = VlmRoles(
        writer="qwen3vl-8b",
        general="qwen38-27b",
        client_factory=_proc_factory(opts),
        vram_gb=24.0,
        idle_timeout_s=0.2,
        swap_grace_s=0.0,
    )
    writer, general = roles.client("writer"), roles.client("general")
    try:
        await general.ensure_started("qwen38-27b")
        take_over = asyncio.create_task(writer.ensure_started("qwen3vl-8b"))
        while not general._stopping:
            await asyncio.sleep(0.01)
        await general.shutdown()  # unload for ComfyUI, mid-eviction
        await asyncio.wait_for(take_over, 30)
        assert general.swapped_out is False
        # Nothing brings it back behind the render's back.
        with pytest.raises(RuntimeError, match="not ready"):
            await general.generate_tags(img)
        assert general.state == "stopped"
    finally:
        await roles.shutdown_all()


async def test_an_explicit_unload_aborts_a_pending_re_ensure(tmp_path: Path) -> None:
    img = _png(tmp_path)
    async with FakeLlamaServer(canned_response=_TAGS) as w, FakeLlamaServer(
        canned_response=_TAGS
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        spawned: List[str] = []
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=_slow_loading_factory(urls, 0.6, spawned),
            vram_gb=24.0,
            idle_timeout_s=0.1,
            swap_grace_s=0.0,
        )
        writer, general = roles.client("writer"), roles.client("general")
        try:
            await general.ensure_started("qwen38-27b")
            loading = asyncio.create_task(writer.ensure_started("qwen3vl-8b"))
            while not general.swapped_out:
                await asyncio.sleep(0.01)
            # general's request waits for the writer's load to finish ...
            request = asyncio.create_task(general.generate_tags(img))
            await asyncio.sleep(0.2)
            # ... and the unload for ComfyUI lands meanwhile.
            await general.shutdown()
            with pytest.raises(VlmError, match="unloaded"):
                await asyncio.wait_for(request, 30)
            await loading
            assert spawned.count("qwen38-27b") == 1  # never respawned
            assert general.state == "stopped"
        finally:
            await roles.shutdown_all()


# ---------------------------------------------------------------------------
# Fix round 4: unload during a swap's wait, abandoned loads, reload backoff
# ---------------------------------------------------------------------------


def _spawn_counting_factory(urls: Dict[str, str], spawned: List[str]):
    def make() -> VlmClient:
        def spawn(mid: str) -> str:
            spawned.append(mid)
            return urls[mid]

        return VlmClient(spawn_override=spawn)

    return make


async def test_an_explicit_unload_during_the_swap_wait_wins(tmp_path: Path) -> None:
    """ComfyUI unloads general while the writer's _make_room is waiting for
    general's in-flight request: the eviction that follows must not mark
    the stopped client swapped_out, or the tag pump respawns it mid-render."""
    img = _png(tmp_path)
    async with FakeLlamaServer(canned_response=_TAGS) as w, FakeLlamaServer(
        canned_response=_TAGS
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        spawned: List[str] = []
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=_spawn_counting_factory(urls, spawned),
            vram_gb=24.0,
            idle_timeout_s=5.0,
            swap_grace_s=0.0,
        )
        writer, general = roles.client("writer"), roles.client("general")
        try:
            await general.ensure_started("qwen38-27b")
            general._begin_request()  # a tag request in flight
            take_over = asyncio.create_task(writer.ensure_started("qwen3vl-8b"))
            await asyncio.sleep(0.3)  # _make_room sits in wait_idle
            assert general.state == "ready"
            await general.shutdown()  # unload for ComfyUI
            general._end_request()
            await asyncio.wait_for(take_over, 10)
            assert writer.state == "ready"
            assert general.swapped_out is False
            assert general.state == "stopped"
            # The next tag request (the pump's) must not bring it back.
            with pytest.raises(RuntimeError, match="not ready"):
                await general.generate_tags(img)
            assert spawned.count("qwen38-27b") == 1
        finally:
            await roles.shutdown_all()


async def test_evicting_a_stopped_client_does_not_mark_it_swapped_out() -> None:
    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.preferred_model_id = "qwen3vl-8b"
    await client.evict()
    assert client.swapped_out is False
    await client.start("qwen3vl-8b", wait_ready=False)
    await client.shutdown()
    await client.evict()
    assert client.swapped_out is False


async def test_a_request_begun_before_an_explicit_unload_never_respawns(
    tmp_path: Path,
) -> None:
    """The request's own re-ensure (after a forced eviction mid-call) honours
    an explicit unload that landed after the request began."""
    from metascan.core.vlm_client import STATE_READY

    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.preferred_model_id = "qwen3vl-8b"
    client._state = STATE_READY
    calls: List[int] = []

    async def impl(path: Any, *, timeout: float = 60.0) -> List[str]:
        calls.append(1)
        await client.shutdown()  # ComfyUI unload lands mid-request ...
        client._evictions += 1  # ... and so does a forced eviction
        client.swapped_out = True
        return []

    client._generate_tags_impl = impl  # type: ignore[method-assign]
    with pytest.raises(VlmError, match="unloaded"):
        await client.generate_tags(Path("x.png"))
    assert len(calls) == 1
    assert client.state == "stopped"


async def test_a_timed_out_start_is_not_a_zombie_load() -> None:
    async with FakeLlamaServer(health_fails_forever=True) as srv:
        client = VlmClient(spawn_override=lambda mid: srv.base_url)
        with pytest.raises(TimeoutError):
            await client.start("qwen3vl-8b", ready_timeout=0.3)
        assert client.state == "error"
        assert "did not become ready" in (client.last_error or "")
        t0 = time.monotonic()
        assert await client.wait_loaded(5.0) is True
        assert await client.wait_idle(5.0) is True
        assert time.monotonic() - t0 < 1.0
        await client.shutdown()


async def test_a_dead_health_probe_is_not_a_load_in_progress() -> None:
    from metascan.core.vlm_client import STATE_LOADING

    client = VlmClient()
    client._state = STATE_LOADING

    async def noop() -> None:
        return None

    client._health_task = asyncio.create_task(noop())
    await asyncio.sleep(0)
    assert client._health_task.done()
    t0 = time.monotonic()
    assert await client.wait_loaded(5.0) is True
    assert await client.wait_idle(5.0) is True
    assert time.monotonic() - t0 < 1.0


async def test_the_swap_does_not_wait_for_an_abandoned_load() -> None:
    async with FakeLlamaServer(canned_response=_TAGS) as w, FakeLlamaServer(
        health_fails_forever=True
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=lambda: VlmClient(spawn_override=lambda mid: urls[mid]),
            vram_gb=24.0,
            idle_timeout_s=0.2,
            swap_grace_s=0.0,
            load_timeout_s=30.0,
        )
        writer, general = roles.client("writer"), roles.client("general")
        try:
            with pytest.raises(TimeoutError):
                await general.start("qwen38-27b", ready_timeout=0.3)
            t0 = time.monotonic()
            await asyncio.wait_for(writer.ensure_started("qwen3vl-8b"), 10)
            assert time.monotonic() - t0 < 2.0
            assert writer.state == "ready"
        finally:
            await roles.shutdown_all()


async def test_a_failing_reload_backs_off_instead_of_thrashing_the_writer(
    tmp_path: Path,
) -> None:
    from metascan.core import vlm_client as vc

    img = _png(tmp_path)
    async with FakeLlamaServer(canned_response=_TAGS) as w, FakeLlamaServer(
        canned_response=_TAGS
    ) as g:
        urls = {"qwen3vl-8b": w.base_url, "qwen38-27b": g.base_url}
        spawned: List[str] = []
        fail_general_reloads = {"on": True}

        def make() -> VlmClient:
            def spawn(mid: str) -> str:
                spawned.append(mid)
                return urls[mid]

            c = VlmClient(spawn_override=spawn)
            probe = c._health_probe_loop

            async def maybe_failing_probe() -> None:
                # General's reloads crash on load (bad GBNF, OOM, ...).
                if c.preferred_model_id == "qwen38-27b" and fail_general_reloads["on"]:
                    if spawned.count("qwen38-27b") > 1:
                        c._last_error = "crashed on load"
                        c._ready_event.set()
                        c._set_state(vc.STATE_ERROR, error=c._last_error)
                        return
                await probe()

            c._health_probe_loop = maybe_failing_probe  # type: ignore[method-assign]
            return c

        roles = VlmRoles(
            writer="qwen3vl-8b",
            general="qwen38-27b",
            client_factory=make,
            vram_gb=24.0,
            idle_timeout_s=0.2,
            swap_grace_s=0.0,
        )
        writer, general = roles.client("writer"), roles.client("general")
        try:
            await general.ensure_started("qwen38-27b")
            await writer.ensure_started("qwen3vl-8b")
            assert general.swapped_out is True
            # First reload: evicts the writer, then fails on load.
            with pytest.raises(RuntimeError):
                await general.generate_tags(img)
            assert spawned.count("qwen38-27b") == 2
            assert writer._evictions == 1
            assert general.swapped_out is True  # still swappable
            # Inside the backoff: fail fast, no spawn, writer left alone.
            writer_back = asyncio.create_task(writer.ensure_started("qwen3vl-8b"))
            await writer_back
            t0 = time.monotonic()
            with pytest.raises(VlmError, match="retry"):
                await general.generate_tags(img)
            assert time.monotonic() - t0 < 0.5
            assert spawned.count("qwen38-27b") == 2
            assert writer._evictions == 1
            assert writer.state == "ready"
            # After the backoff a reload is tried again and may succeed.
            fail_general_reloads["on"] = False
            await asyncio.sleep(vc._RESPAWN_BACKOFF_SECONDS[0] + 0.1)
            assert await general.generate_tags(img)
            assert spawned.count("qwen38-27b") == 3
            assert general.swapped_out is False
        finally:
            await roles.shutdown_all()


def test_reload_backoff_grows_and_resets() -> None:
    from metascan.core import vlm_client as vc

    client = VlmClient()
    assert client._reload_backoff_remaining() == 0.0
    client._note_reload_failure()
    assert 0 < client._reload_backoff_remaining() <= vc._RESPAWN_BACKOFF_SECONDS[0]
    client._note_reload_failure()
    client._note_reload_failure()
    client._note_reload_failure()
    assert client._reload_backoff_remaining() > vc._RESPAWN_BACKOFF_SECONDS[1]
    client._reload_failures = 0
    client._reload_failed_at = None
    assert client._reload_backoff_remaining() == 0.0


async def test_a_load_evicted_right_after_ready_still_counts_as_loaded() -> None:
    """The other role may evict this client between its /health flipping
    READY and the loader's own waiter resuming (swap_grace_s=0 makes it
    likely): the load succeeded, so the start returns -- the next request
    re-ensures -- instead of raising "ended in state stopped"."""
    from metascan.core.vlm_client import STATE_READY

    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.preferred_model_id = "qwen3vl-8b"
    await client.start("qwen3vl-8b", wait_ready=False)
    # The probe flips READY ...
    client._ready_seen = True
    client._set_state(STATE_READY)
    # ... and the other role evicts it before the waiter resumes.
    await client.evict()
    client._ready_event.set()  # the waiter was already woken
    await asyncio.wait_for(client._wait_ready(5.0), 5)
    assert client.state == "stopped" and client.swapped_out is True


async def test_a_load_evicted_before_ready_still_raises() -> None:
    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    client.preferred_model_id = "qwen3vl-8b"
    await client.start("qwen3vl-8b", wait_ready=False)
    await client.evict()  # a forced eviction of a load that never ended
    client._ready_event.set()
    with pytest.raises(RuntimeError, match="ended in state stopped"):
        await asyncio.wait_for(client._wait_ready(5.0), 5)
