"""ComfyClient tests, driven entirely by the in-process fake ComfyUI."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError, _next_backoff
from metascan.core.database_sqlite import DatabaseManager
from tests._fake_comfy_server import fake_comfy  # noqa: F401


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {"filename_prefix": "ms"}),
    }


def params(**kw) -> GenerationParams:
    base = dict(positive="a cat", seed=42, width=1024, height=576, batch_size=2)
    base.update(kw)
    return GenerationParams(**base)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
async def client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    try:
        yield c
    finally:
        await c.aclose()


async def test_register_preset_stores_workflow_and_bindings(client):
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    row = client.db.get_workflow_preset(pid)
    assert row["name"] == "sdxl"
    assert row["kind"] == "t2i"
    assert '"positive": "6"' in row["bindings"]


async def test_register_preset_rejects_an_unbindable_workflow(client):
    wf = t2i_workflow()
    del wf["9"]
    with pytest.raises(BindingError) as exc:
        await client.register_preset("broken", "t2i", wf)
    assert "MS_SAVE" in str(exc.value)


async def test_submit_now_sends_the_overridden_graph(client, fake_comfy):  # noqa: F811
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    await client.submit_now(pid, params())

    sent = fake_comfy.submitted[-1]["body"]["prompt"]
    assert sent["6"]["inputs"]["text"] == "a cat"
    assert sent["3"]["inputs"]["seed"] == 42
    assert sent["5"]["inputs"]["batch_size"] == 2
    # the stored preset is untouched
    assert '"text": ""' in client.db.get_workflow_preset(pid)["workflow_json"]


async def test_submit_now_records_a_running_job(client):
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await client.submit_now(pid, params())

    job = client.db.get_generation_job(job_id)
    assert job["state"] == "running"
    assert job["comfy_prompt_id"]
    assert job["started_at"]


async def test_submit_now_with_a_missing_preset_raises(client):
    with pytest.raises(ComfyError) as exc:
        await client.submit_now(9999, params())
    assert "9999" in str(exc.value)


async def test_unreachable_server_raises_comfy_error(workspace):
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url="http://127.0.0.1:1",  # nothing listens here
        output_root=workspace / "out",
        db=db,
        request_timeout_s=1.0,
    )
    pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
    try:
        with pytest.raises(ComfyError) as exc:
            await c.submit_now(pid, params())
        assert "127.0.0.1:1" in str(exc.value)
    finally:
        await c.aclose()


async def test_failed_submit_marks_the_job_failed(workspace):
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url="http://127.0.0.1:1",
        output_root=workspace / "out",
        db=db,
        request_timeout_s=1.0,
    )
    pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
    try:
        with pytest.raises(ComfyError):
            await c.submit_now(pid, params())
        jobs = db.list_generation_jobs(states=["failed"])
        assert len(jobs) == 1
        assert jobs[0]["error"]
    finally:
        await c.aclose()


async def test_submit_now_with_no_prompt_id_reports_the_base_url(
    client, fake_comfy  # noqa: F811
):
    fake_comfy.omit_prompt_id = True
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())

    with pytest.raises(ComfyError) as exc:
        await client.submit_now(pid, params())
    assert fake_comfy.base_url in str(exc.value)

    jobs = client.db.list_generation_jobs(states=["failed"])
    assert len(jobs) == 1
    assert jobs[0]["error"] == str(exc.value)


async def test_fetch_history_returns_the_entry_for_a_prompt(
    client, fake_comfy
):  # noqa: F811
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await client.submit_now(pid, params())
    prompt_id = client.db.get_generation_job(job_id)["comfy_prompt_id"]

    entry = None
    for _ in range(50):
        entry = await client.fetch_history(prompt_id)
        if entry:
            break
        await asyncio.sleep(0.02)

    assert entry["outputs"]["9"]["images"]


@pytest.fixture
async def started_client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def test_start_connects_the_websocket(started_client, fake_comfy):  # noqa: F811
    assert started_client.connected is True
    # A stub that merely flips a flag wouldn't produce a real server-side
    # socket or leave the reader task running.
    assert len(fake_comfy._sockets) == 1
    assert started_client._ws_task is not None
    assert not started_client._ws_task.done()


async def test_successful_execution_marks_the_job_done(started_client):
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())

    job = await started_client.wait_for_job(job_id, timeout=5.0)
    assert job["state"] == "done"
    assert job["finished_at"]


async def test_execution_error_marks_the_job_failed_with_node_context(
    started_client, fake_comfy  # noqa: F811
):
    fake_comfy.fail_with = "value not in list: ckpt_name"
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())

    job = await started_client.wait_for_job(job_id, timeout=5.0)
    assert job["state"] == "failed"
    assert "value not in list" in job["error"]
    assert "CheckpointLoaderSimple" in job["error"]


async def test_job_events_are_emitted_to_listeners(started_client):
    seen = []
    started_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())
    await started_client.wait_for_job(job_id, timeout=5.0)

    updates = [p for e, p in seen if e == "job_update"]
    assert any(p["job_id"] == job_id and p["state"] == "done" for p in updates)


async def test_events_for_unknown_prompt_ids_are_ignored(
    started_client, fake_comfy
):  # noqa: F811
    seen = []
    started_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    await fake_comfy.broadcast(
        {"type": "executed", "data": {"prompt_id": "not-ours", "node": "9"}}
    )
    # No submit is in flight, so resolution is a single synchronous dict
    # lookup with no retry sleeps — a handful of short polls is plenty
    # for the reader loop to have actually processed the frame (as
    # opposed to the single 0.1s sleep this used to use, which could
    # pass merely because the reader was still asleep inside a retry).
    for _ in range(20):
        await asyncio.sleep(0.01)

    assert started_client.connected is True  # no crash, no reconnect
    assert seen == []  # never fired a job_update/job_progress for it
    assert "not-ours" not in started_client._prompt_to_job


async def test_reconnects_after_the_server_drops_the_socket(
    started_client, fake_comfy
):  # noqa: F811
    for ws in list(fake_comfy._sockets):
        await ws.close()

    # The dropped connection never delivered a frame and didn't stay open
    # past _STABLE_CONNECTION_SECONDS, so the backoff escalates to the
    # second tier (3s) rather than resetting to the first (1s) — budget
    # generously past that.
    for _ in range(200):
        await asyncio.sleep(0.05)
        if started_client.connected and fake_comfy._sockets:
            break
    assert started_client.connected is True

    # and the reconnected socket still drives jobs to completion
    pid = await started_client.register_preset("sdxl2", "t2i", t2i_workflow())
    job_id = await started_client.submit_now(pid, params())
    assert (await started_client.wait_for_job(job_id, timeout=5.0))["state"] == "done"


async def test_reconnecting_immediately_after_a_flapping_server_does_not_hang(
    workspace, fake_comfy  # noqa: F811
):
    """A server that accepts and immediately closes must still recover.

    This isn't primarily about timing the backoff (see the pure
    _next_backoff tests below for that) — it's a smoke test that the
    reader loop survives a run of connect/instant-drop cycles at all and
    a job submitted afterwards still completes, once the server stops
    flapping.
    """
    fake_comfy.close_after_connect = True
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
    )
    await c.start()
    try:
        await asyncio.sleep(0.2)
        assert c.connected is False  # still flapping, not "connected"

        fake_comfy.close_after_connect = False
        for _ in range(200):  # let the reader actually reconnect and stay up
            await asyncio.sleep(0.05)
            if c.connected:
                break
        assert c.connected is True

        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
        job_id = await c.submit_now(pid, params())
        job = await c.wait_for_job(job_id, timeout=5.0)
        assert job["state"] == "done"
    finally:
        await c.shutdown()


def test_next_backoff_resets_after_a_frame_is_delivered():
    attempt, delay = _next_backoff(2, got_frame=True, survived=False)
    assert (attempt, delay) == (0, 1.0)


def test_next_backoff_resets_after_a_connection_survives_the_stable_window():
    attempt, delay = _next_backoff(2, got_frame=False, survived=True)
    assert (attempt, delay) == (0, 1.0)


def test_next_backoff_escalates_when_the_server_keeps_dropping_instantly():
    attempt, delay = 0, 0.0
    seen = []
    for _ in range(5):
        attempt, delay = _next_backoff(attempt, got_frame=False, survived=False)
        seen.append(delay)
    # Never resets to the 1s tier: it climbs to and then holds at the top.
    assert seen == [3.0, 10.0, 10.0, 10.0, 10.0]
