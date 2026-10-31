"""REST transport of the background-process registry."""

from pathlib import Path
from typing import List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import background as background_api
from backend.dependencies import set_registry
from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry


class Fake:
    holds = ()

    def __init__(self, pid="p1", can_pause=True):
        self.id = pid
        self.label = pid.upper()
        self.order = 1
        self._caps = Capabilities(can_start=False, can_pause=can_pause, can_stop=False)
        self.calls: List[str] = []

    def capabilities(self):
        return self._caps

    def report(self):
        return ProcessReport(State.RUNNING, None, "working")

    async def start(self):
        self.calls.append("start")

    async def pause(self):
        self.calls.append("pause")

    async def resume(self):
        self.calls.append("resume")

    async def stop(self):
        self.calls.append("stop")


@pytest.fixture
def env(tmp_path: Path):
    sent = []
    reg = ProcessRegistry(tmp_path / "state.json", sent.append)
    fake = Fake()
    reg.register(fake)
    set_registry(reg)
    app = FastAPI()
    app.include_router(background_api.router)
    with TestClient(app) as client:
        yield client, reg, fake, sent
    set_registry(None)


def test_list_shape(env):
    client, _, _, _ = env
    body = client.get("/api/background").json()
    assert isinstance(body["server_time"], float)
    assert [p["id"] for p in body["processes"]] == ["p1"]
    p = body["processes"][0]
    assert p["state"] == "running" and p["capabilities"]["can_pause"] is True


def test_pause_resume_round_trip(env):
    client, _, fake, sent = env
    r = client.post("/api/background/p1/pause")
    assert r.status_code == 200 and r.json()["state"] == "paused"
    r = client.post("/api/background/p1/resume")
    assert r.status_code == 200 and r.json()["state"] == "running"
    assert fake.calls == ["pause", "resume"]
    assert sent  # the registry's notify callback fired


def test_unknown_id_404(env):
    client, _, _, _ = env
    assert client.post("/api/background/nope/pause").status_code == 404


def test_refused_409_with_code(env):
    client, _, _, _ = env
    r = client.post("/api/background/p1/stop")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_stoppable"


def test_broadcast_on_process_updated():
    from unittest.mock import patch

    with patch.object(background_api.ws_manager, "broadcast_sync") as bs:
        st = Fake().report()
        assert st  # sanity
        from backend.services.background.protocol import ProcessStatus

        status = ProcessStatus(
            "p1", "P1", 1, State.RUNNING, None, "", [], None, 0, Fake().capabilities()
        )
        background_api.publish_status(status)
    chan, event, data = bs.call_args[0]
    assert (chan, event) == ("background", "process_updated")
    assert data["id"] == "p1" and data["state"] == "running"
