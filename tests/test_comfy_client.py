"""ComfyClient tests, driven entirely by the in-process fake ComfyUI."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
from pathlib import Path

import pytest
from PIL import Image

from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import (
    ComfyClient,
    ComfyError,
    PresetNotFoundError,
    _next_backoff,
)
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
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


# ---- Task 8: queue discipline, cancellation, priority ---------------------


async def test_submit_raises_for_a_missing_preset_without_enqueuing(client):
    # Regression test for the real ComfyClient.submit(), not the router or
    # the tests/test_comfy_api.py stub: this must fail if the
    # PresetNotFoundError raise in _load_preset (used by submit()) is ever
    # removed or weakened, independent of anything backend/api/comfy.py
    # does with the exception.
    with pytest.raises(PresetNotFoundError) as exc:
        await client.submit(9999, params())
    assert "9999" in str(exc.value)
    assert client.queue_depth() == 0
    assert client.db.list_generation_jobs() == []


async def test_submit_raises_for_an_unbindable_parameter_without_enqueuing(client):
    # Regression test for ComfyClient.submit()'s eager apply_overrides()
    # call: a negative prompt against a workflow with no MS_NEGATIVE node
    # must raise BindingError synchronously and enqueue nothing, rather
    # than succeeding here and failing later inside _dispatch.
    pid = await client.register_preset("sdxl", "t2i", t2i_workflow())
    with pytest.raises(BindingError) as exc:
        await client.submit(pid, params(negative="blurry"))
    assert "MS_NEGATIVE" in str(exc.value)
    assert client.queue_depth() == 0
    assert client.db.list_generation_jobs() == []


async def test_submit_enqueues_and_returns_immediately(
    started_client, fake_comfy
):  # noqa: F811
    fake_comfy.execution_delay = 0.3
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    job_ids = [await started_client.submit(pid, params()) for _ in range(5)]

    assert len(set(job_ids)) == 5
    await asyncio.sleep(0.1)
    assert len(fake_comfy.submitted) <= started_client.in_flight

    for jid in job_ids:
        assert (await started_client.wait_for_job(jid, timeout=10.0))["state"] == "done"
    assert len(fake_comfy.submitted) == 5


async def test_priority_jobs_jump_the_queue(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.2
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    for _ in range(4):
        await started_client.submit(pid, params(positive="bulk"))
    await asyncio.sleep(0.05)
    urgent = await started_client.submit(pid, params(positive="urgent"), priority=True)

    await started_client.wait_for_job(urgent, timeout=10.0)
    order = [s["body"]["prompt"]["6"]["inputs"]["text"] for s in fake_comfy.submitted]
    # in_flight=2: one bulk pair is already dispatched (indices 0-1) by the
    # time the priority submit lands, so "urgent" must be exactly next.
    assert order.index("urgent") == 2


async def test_cancel_a_queued_job_never_reaches_comfyui(
    started_client, fake_comfy
):  # noqa: F811
    fake_comfy.execution_delay = 0.4
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    for _ in range(2):
        await started_client.submit(pid, params(positive="bulk"))
    victim = await started_client.submit(pid, params(positive="victim"))
    await started_client.cancel(victim)

    await asyncio.sleep(1.2)
    sent = [s["body"]["prompt"]["6"]["inputs"]["text"] for s in fake_comfy.submitted]
    assert "victim" not in sent
    assert started_client.db.get_generation_job(victim)["state"] == "cancelled"


async def test_cancel_a_running_job_interrupts_comfyui(
    started_client, fake_comfy
):  # noqa: F811
    fake_comfy.execution_delay = 1.0
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    for _ in range(100):
        await asyncio.sleep(0.02)
        if (
            started_client.db.get_generation_job(job_id)["state"] == "running"
            and fake_comfy.running_prompt
        ):
            break

    prompt_id = started_client.db.get_generation_job(job_id)["comfy_prompt_id"]
    await started_client.cancel(job_id)

    assert fake_comfy.interrupted == 1
    # Scoped, not global: the interrupt named this prompt, and because it
    # really was the running one, ComfyUI acted on it.
    assert fake_comfy.interrupt_requests == [prompt_id]
    assert fake_comfy.interrupted_prompts == [prompt_id]
    assert started_client.db.get_generation_job(job_id)["state"] == "cancelled"


async def test_cancel_all_clears_the_queue(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.5
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await started_client.submit(pid, params()) for _ in range(6)]

    await asyncio.sleep(0.05)
    await started_client.cancel_all()

    assert started_client.queue_depth() == 0

    # cancel_all() does NOT resolve every job before returning: a job
    # whose _dispatch is parked mid-POST is left flagged in `_cancelled`
    # and finishes slightly later, when _dispatch observes the flag (see
    # the handshake in cancel()/_dispatch). What it does guarantee is
    # that no job is *missed* -- every one reaches a terminal state
    # shortly after, and none is left running or queued indefinitely.
    for _ in range(200):
        states = {started_client.db.get_generation_job(j)["state"] for j in job_ids}
        if states <= {"cancelled", "done", "failed"}:
            break
        await asyncio.sleep(0.02)

    assert states <= {"cancelled", "done"}
    assert "queued" not in states
    assert "running" not in states


async def test_a_failing_job_does_not_stall_its_siblings(
    started_client, fake_comfy
):  # noqa: F811
    fake_comfy.fail_with = "boom"
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await started_client.submit(pid, params()) for _ in range(4)]

    for jid in job_ids:
        assert (await started_client.wait_for_job(jid, timeout=10.0))[
            "state"
        ] == "failed"
    assert len(fake_comfy.submitted) == 4


async def test_dispatch_wraps_its_post_in_track_submit(
    started_client, fake_comfy
):  # noqa: F811
    """Regression guard: _dispatch must POST through _track_submit().

    _resolve_job_id only retries an unresolved prompt_id while
    _submits_in_flight > 0 -- that window is what lets a fast
    execution_start/executed event survive the race against _dispatch's
    own write to _prompt_to_job (see _track_submit's docstring). If
    _dispatch's POST were ever changed to bypass _track_submit, that
    race window disappears and a queued job's completion event could be
    dropped, leaving it stuck at "running" forever.

    Rather than trying to force that timing race (flaky by nature), this
    spies on _track_submit and asserts it actually wraps the POST -- and
    that the counter it maintains is genuinely nonzero while the POST is
    in flight -- for a job that went through the queue/pump path (not
    submit_now, which is already covered by its own tests).
    """
    seen_in_flight = []
    original = started_client._track_submit

    @contextlib.asynccontextmanager
    async def spy():
        async with original():
            seen_in_flight.append(started_client._submits_in_flight)
            yield

    started_client._track_submit = spy  # type: ignore[method-assign]

    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    job = await started_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "done"
    # The spy records _submits_in_flight from *inside* _track_submit's CM,
    # which is only reachable if _dispatch actually entered it around the
    # POST -- an empty list here means it didn't.
    assert seen_in_flight, "_dispatch never entered _track_submit around its POST"


async def test_cancel_landing_before_the_post_keeps_the_job_off_comfyui(
    started_client, fake_comfy
):  # noqa: F811
    """Regression guard for the cancel()/_dispatch race (review finding 3).

    _dispatch only writes state="running" (and only then would cancel()'s
    "queued" branch correctly fall through to the running-job path) after
    its POST resolves. Before that, a job _pump_loop has already popped
    off `_queue` is in a gap where the DB row still says "queued" but the
    job is no longer sitting in `_queue` for cancel() to remove -- so a
    naive cancel() would mark the row cancelled and return, while
    _dispatch, unaware, still POSTs the job to ComfyUI and overwrites the
    row back to "running".

    Deterministically reproduces landing in that gap (no timing race) by
    making cancel() run as a nested step of _dispatch's own
    `_load_preset` call, guaranteeing it completes strictly before
    _dispatch's `_cancelled` check and the POST that follows it.
    """
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    original_load_preset = started_client._load_preset

    async def load_preset_then_cancel(preset_id):
        result = await original_load_preset(preset_id)
        await started_client.cancel(job_id)
        return result

    started_client._load_preset = load_preset_then_cancel  # type: ignore[method-assign]

    for _ in range(100):
        await asyncio.sleep(0.02)
        if started_client.db.get_generation_job(job_id)["state"] != "queued":
            break

    assert fake_comfy.submitted == []
    assert started_client.db.get_generation_job(job_id)["state"] == "cancelled"
    assert job_id not in started_client._running


async def test_cancel_landing_mid_post_still_interrupts_comfyui(
    started_client, fake_comfy  # noqa: F811
):
    """The other half of the _dispatch handshake: a cancel() landing after
    the POST has already reached ComfyUI must undo it there (queue delete
    + interrupt) rather than letting the job run to completion.
    """
    fake_comfy.execution_delay = 0.3
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    original_post = started_client._http.post

    async def post_then_cancel(url, *args, **kwargs):
        resp = await original_post(url, *args, **kwargs)
        if url == f"{started_client.base_url}/prompt":
            await started_client.cancel(job_id)
        return resp

    started_client._http.post = post_then_cancel  # type: ignore[method-assign]

    job = await started_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "cancelled"
    assert fake_comfy.interrupted >= 1
    assert job_id not in started_client._running


# ---- Task 9: output retrieval and ingest -----------------------------------


@pytest.fixture
async def ingesting_client(fake_comfy, workspace):  # noqa: F811
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=Scanner(db),
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def test_outputs_are_written_under_the_output_root(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    files = sorted(ingesting_client.output_dir_for(job_id).glob("*.png"))
    assert len(files) == 2
    assert all(f.stat().st_size > 0 for f in files)


async def test_outputs_are_ingested_into_the_media_database(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    files = list(ingesting_client.output_dir_for(job_id).glob("*.png"))
    assert files  # otherwise the loop below passes vacuously
    for f in files:
        assert ingesting_client.db.get_media(str(f)) is not None


async def test_a_job_is_only_done_after_its_images_land(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    job = await ingesting_client.wait_for_job(job_id, timeout=10.0)

    assert job["state"] == "done"
    assert list(ingesting_client.output_dir_for(job_id).glob("*.png"))


async def test_job_outputs_event_carries_the_written_paths(ingesting_client):
    seen = []
    ingesting_client.on_job_event(lambda event, payload: seen.append((event, payload)))
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    outputs = [p for e, p in seen if e == "job_outputs"]
    assert outputs and len(outputs[0]["files"]) == 2


async def test_a_download_failure_fails_the_job_rather_than_hanging(
    ingesting_client, fake_comfy, monkeypatch  # noqa: F811
):
    async def boom(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(ingesting_client, "_download_image", boom)

    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())

    job = await ingesting_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "failed"
    assert "connection reset" in job["error"]


async def test_a_failed_execution_writes_no_files(
    ingesting_client, fake_comfy
):  # noqa: F811
    fake_comfy.fail_with = "boom"
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    job = await ingesting_client.wait_for_job(job_id, timeout=10.0)

    assert job["state"] == "failed"
    assert not list(ingesting_client.output_dir_for(job_id).glob("*.png"))


async def test_a_cancelled_job_does_not_download_or_ingest_its_outputs(
    fake_comfy, workspace  # noqa: F811
):
    """Regression guard: an `executed` frame processed for a job already
    flagged in `_cancelled` before `_complete_job` even starts must not
    download or ingest anything, and must not resurrect the job to
    "done" (review finding, not covered by any earlier task's tests).

    Note on what `_cancelled` actually guarantees here: it is a
    short-lived flag, not a durable "this job was cancelled" marker --
    `cancel()`'s running-job branch discards `job_id` from `_cancelled`
    *before* it interrupts ComfyUI or writes state="cancelled", so by the
    time a cancel has actually taken effect the flag is already gone.
    This test only covers the narrow window where `_cancelled` is
    genuinely still set (a frame arriving before `_complete_job` gets a
    scheduler turn at all); it deliberately does NOT claim to cover a
    cancel that lands mid-download or after the job is already finished
    -- that's `test_a_cancel_landing_mid_download_leaves_no_images_ingested`
    below, and it's `collect_outputs`'s per-image state rechecks plus
    `_finish_job`'s terminal-state guard that make that safe, not this
    flag.

    Deliberately builds its own client and never calls `start()`: with a
    live websocket (as `ingesting_client` has), fake_comfy's background
    execution task -- which runs with no delay by default -- broadcasts a
    *real* `executed` frame that the reader loop processes concurrently
    with this test's manual steps, downloading the images for real before
    `_cancelled` is even set. That raced this test intermittently. With no
    socket connected, fake_comfy has nothing to broadcast to, so the only
    `_on_prompt_end` call is the explicit one below.
    """
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=Scanner(db),
    )
    try:
        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
        job_id = await c.submit_now(pid, params())
        job = c.db.get_generation_job(job_id)
        prompt_id = job["comfy_prompt_id"]

        c._cancelled.add(job_id)

        await c._on_prompt_end(job_id, {"prompt_id": prompt_id})
        for task in list(c._collect_tasks):
            await task

        assert not list(c.output_dir_for(job_id).glob("*.png"))
        # _complete_job returned before calling _finish_job at all -- the
        # job is left exactly as cancel() would have left it (never
        # resurrected to "done" by the collection task that raced it).
        assert c.db.get_generation_job(job_id)["state"] == "running"
    finally:
        await c.aclose()


async def test_a_cancel_landing_mid_download_leaves_no_images_ingested(
    fake_comfy, workspace  # noqa: F811
):
    """Regression guard for review BLOCKING 1.

    The reviewer's own probe (gate `_download_image`, fire `_on_prompt_end`,
    then `cancel()` mid-download) found the pre-fix code resurrected the
    job to "done" with both images written *and* ingested, because the
    only guard at the time was a check against `_cancelled` -- and
    `cancel()`'s running-job branch discards `job_id` from `_cancelled`
    before it does anything else, so that check never sees a cancel that
    actually took effect.

    This reproduces the same scenario deterministically: block the first
    image's download behind a gate, cancel the job while that download is
    still in flight, then release the gate and let the (now-orphaned)
    download complete. The fix -- `collect_outputs` re-checking the job's
    DB state both before and after each image's download, plus
    `_finish_job` refusing to move a job out of a terminal state -- must
    leave the row "cancelled" and ingest nothing, even though the first
    image's bytes do land on disk (an accepted, documented orphan: a
    retry re-downloads over it).

    Built as its own unstarted client for the same reason as the test
    above: a live websocket would let fake_comfy's own zero-delay
    execution race this test's manual orchestration.
    """
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=Scanner(db),
    )
    try:
        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
        job_id = await c.submit_now(pid, params())
        prompt_id = c.db.get_generation_job(job_id)["comfy_prompt_id"]

        gate = asyncio.Event()
        original_download = c._download_image

        async def gated_download(entry, target):
            await gate.wait()
            await original_download(entry, target)

        c._download_image = gated_download  # type: ignore[method-assign]

        await c._on_prompt_end(job_id, {"prompt_id": prompt_id})
        # Let the freshly-scheduled _complete_job task actually run and
        # reach (and block on) the gate before cancelling.
        await asyncio.sleep(0.1)

        await c.cancel(job_id)
        gate.set()

        for task in list(c._collect_tasks):
            await task

        job = c.db.get_generation_job(job_id)
        assert job["state"] == "cancelled"
        for f in c.output_dir_for(job_id).glob("*.png"):
            assert c.db.get_media(str(f)) is None
    finally:
        await c.aclose()


async def test_duplicate_end_of_prompt_frames_produce_exactly_one_collection(
    ingesting_client, fake_comfy  # noqa: F811
):
    """Regression guard for review BLOCKING 2.

    ComfyUI signals end-of-prompt twice for every prompt:
    `execution_success` from inside execute(), then `executing
    {node: null}` immediately after task_done(). Both reach
    `_on_prompt_end`, microseconds apart. Because collection runs as a
    fire-and-forget task rather than an inline await, `_finish_job`'s
    prompt-map eviction (which used to make a second frame resolve to no
    job at all) doesn't happen until the first frame's download/ingest
    finishes -- long after a fast second frame has already resolved to
    the same job_id. The reviewer reproduced two full collection passes
    (two `job_outputs`, two `job_update` done-events, double
    downloads/ingests) through the real reader loop this way.

    Slows the download so the window is wide open, then broadcasts a
    third end-of-prompt frame for the same prompt_id while the first
    collection is confirmed still in flight (via `_collecting`). The
    `_collecting` guard in `_on_prompt_end` must make the extras no-ops.
    """
    seen = []
    ingesting_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    original_download = ingesting_client._download_image

    async def slow_download(entry, target):
        await asyncio.sleep(0.3)
        await original_download(entry, target)

    ingesting_client._download_image = slow_download  # type: ignore[method-assign]

    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit_now(pid, params())
    prompt_id = ingesting_client.db.get_generation_job(job_id)["comfy_prompt_id"]

    for _ in range(100):
        await asyncio.sleep(0.02)
        if ingesting_client._collecting:
            break
    assert ingesting_client._collecting, "collection never started"

    await fake_comfy.broadcast(
        {"type": "execution_success", "data": {"prompt_id": prompt_id}}
    )
    await fake_comfy.broadcast(
        {"type": "executing", "data": {"prompt_id": prompt_id, "node": None}}
    )
    await asyncio.sleep(0.05)  # let the reader loop actually process them

    job = await ingesting_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "done"

    outputs = [p for e, p in seen if e == "job_outputs"]
    done_updates = [p for e, p in seen if e == "job_update" and p["state"] == "done"]
    assert len(outputs) == 1
    assert len(done_updates) == 1
    assert len(outputs[0]["files"]) == 2


def _make_png(path: Path, color=(10, 200, 90)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path)
    return path


async def test_upload_image_returns_the_comfy_side_filename(
    started_client, workspace, fake_comfy  # noqa: F811
):
    src = _make_png(workspace / "refs" / "maya.png")
    name = await started_client.upload_image(src)

    assert name
    assert len(fake_comfy.uploaded) == 1


async def test_identical_content_is_uploaded_once(
    started_client, workspace, fake_comfy  # noqa: F811
):
    a = _make_png(workspace / "refs" / "a.png", color=(1, 2, 3))
    b = _make_png(workspace / "refs" / "b.png", color=(1, 2, 3))

    first = await started_client.upload_image(a)
    second = await started_client.upload_image(b)

    assert first == second
    assert len(fake_comfy.uploaded) == 1


async def test_different_content_uploads_twice(
    started_client, workspace, fake_comfy  # noqa: F811
):
    a = _make_png(workspace / "refs" / "c.png", color=(1, 2, 3))
    b = _make_png(workspace / "refs" / "d.png", color=(9, 9, 9))

    await started_client.upload_image(a)
    await started_client.upload_image(b)

    assert len(fake_comfy.uploaded) == 2


async def test_uploading_a_missing_file_raises(started_client, workspace):
    with pytest.raises(ComfyError) as exc:
        await started_client.upload_image(workspace / "refs" / "nope.png")
    assert "nope.png" in str(exc.value)


async def test_a_reference_workflow_receives_the_uploaded_name(
    started_client, workspace, fake_comfy  # noqa: F811
):
    wf = t2i_workflow()
    wf["11"] = _node("LoadImage", "MS_REF_IMAGE", {"image": "placeholder.png"})
    pid = await started_client.register_preset("sdxl-ref", "ref", wf)

    name = await started_client.upload_image(_make_png(workspace / "refs" / "m.png"))
    job_id = await started_client.submit(pid, params(ref_image=name))
    await started_client.wait_for_job(job_id, timeout=10.0)

    sent = fake_comfy.submitted[-1]["body"]["prompt"]
    assert sent["11"]["inputs"]["image"] == name


# ---- Final review: protocol-fidelity regressions ---------------------------


async def test_cancelling_a_pending_job_leaves_a_running_sibling_untouched(
    started_client, fake_comfy  # noqa: F811
):
    """Regression guard for final-review C1.

    `cancel()` used to POST a *bodyless* `/interrupt`, which ComfyUI
    documents (server.py::post_interrupt) as a GLOBAL interrupt: it calls
    nodes.interrupt_processing() on whatever prompt happens to be
    running. With the default in_flight=2 the cancelled job is routinely
    the *pending* one inside ComfyUI while a sibling is mid-generation,
    so the `/queue delete` correctly drops the target and the interrupt
    then destroys the bystander.

    ComfyUI answers an interrupt with `execution_interrupted` (not
    `execution_error`), which the client did not handle at all, so the
    collateral-damaged sibling never reached a terminal state: its row
    stayed "running" and its id stayed in `_running` forever.
    """
    fake_comfy.execution_delay = 1.5
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())

    await started_client.submit(pid, params(positive="runner"))
    await started_client.submit(pid, params(positive="victim"))

    for _ in range(300):
        await asyncio.sleep(0.01)
        if fake_comfy.running_prompt and fake_comfy.pending_prompts:
            break
    running_prompt = fake_comfy.running_prompt
    pending_prompt = fake_comfy.pending_prompts[0]
    assert running_prompt and pending_prompt

    running_job = started_client._prompt_to_job[running_prompt]
    pending_job = started_client._prompt_to_job[pending_prompt]

    await started_client.cancel(pending_job)

    # The interrupt must have been scoped to the pending prompt, which
    # ComfyUI then declines to act on because it isn't the running one.
    assert fake_comfy.interrupted_prompts == []
    assert started_client.db.get_generation_job(pending_job)["state"] == "cancelled"

    # And the bystander still finishes normally.
    sibling = await started_client.wait_for_job(running_job, timeout=15.0)
    assert sibling["state"] == "done"
    assert running_job not in started_client._running


async def test_an_external_interrupt_marks_the_job_cancelled(
    started_client, fake_comfy  # noqa: F811
):
    """`execution_interrupted` is a terminal signal and must be handled.

    Someone hitting Cancel in ComfyUI's own web UI (or any other client
    posting a global `/interrupt`) produces exactly this frame. Without a
    branch for it the job sits at "running" forever and permanently
    consumes one of the `in_flight` admission slots.
    """
    fake_comfy.execution_delay = 1.5
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await started_client.submit(pid, params())

    for _ in range(300):
        await asyncio.sleep(0.01)
        if fake_comfy.running_prompt:
            break
    assert fake_comfy.running_prompt

    # A *global* interrupt, as ComfyUI's own UI issues it.
    await started_client._http.post(f"{fake_comfy.base_url}/interrupt")

    job = await started_client.wait_for_job(job_id, timeout=10.0)
    assert job["state"] == "cancelled"
    assert job_id not in started_client._running


async def test_outputs_are_collected_when_the_save_node_is_not_last(
    ingesting_client, fake_comfy  # noqa: F811
):
    """Regression guard for final-review C2.

    Collection used to fire on the first `executed` frame, but ComfyUI
    writes `/history` only in `PromptQueue.task_done()`, after the whole
    prompt finishes. For any workflow where MS_SAVE is not the last node
    to execute -- a preview or upscale branch continuing afterwards --
    the read is deterministically too early: `/history` returns {}, which
    flowed all the way to `_finish_job(job_id, "done")` with zero files
    and no error anywhere.
    """
    fake_comfy.post_save_delay = 0.4
    fake_comfy.trailing_output_node = True

    seen = []
    ingesting_client.on_job_event(lambda event, payload: seen.append((event, payload)))

    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())

    job = await ingesting_client.wait_for_job(job_id, timeout=15.0)
    assert job["state"] == "done"

    files = sorted(ingesting_client.output_dir_for(job_id).glob("*.png"))
    assert len(files) == 2
    for f in files:
        assert ingesting_client.db.get_media(str(f)) is not None

    outputs = [p for e, p in seen if e == "job_outputs"]
    assert len(outputs) == 1
    assert len(outputs[0]["files"]) == 2


async def test_an_empty_history_entry_fails_the_job(
    ingesting_client, fake_comfy  # noqa: F811
):
    """A prompt that ends with no history entry is a failure, not a
    silent success. Previously this produced state="done", files=[]."""

    async def empty_history(prompt_id):
        return {}

    ingesting_client.fetch_history = empty_history  # type: ignore[method-assign]

    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())

    job = await ingesting_client.wait_for_job(job_id, timeout=20.0)
    assert job["state"] == "failed"
    assert "history" in (job["error"] or "").lower()


async def test_an_error_during_the_running_write_does_not_resurrect_the_job(
    fake_comfy, workspace  # noqa: F811
):
    """Regression guard for final-review I1.

    Both `submit_now` and `_dispatch` publish `_prompt_to_job[prompt_id]`
    and *then* write state="running" through a to_thread hop. The map
    entry is live for the whole of that await, so an `execution_error`
    arriving in the window resolves to the job, `_finish_job` correctly
    writes "failed", and then the pending "running" write lands on top --
    leaving a permanently "running" row that carries an error message and
    a finished_at, with its admission slot already released.

    "checkpoint not found" errors within milliseconds of dispatch and a
    SQLite write on a WSL2 /mnt mount is 50-100ms, so the window is real.
    Reproduced deterministically here by gating the "running" write.
    """
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
    )
    try:
        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())

        gate = threading.Event()
        original_update = db.update_generation_job

        def gated_update(job_id, **fields):
            if fields.get("state") == "running":
                gate.wait(10)
            return original_update(job_id, **fields)

        db.update_generation_job = gated_update  # type: ignore[method-assign]

        submit = asyncio.create_task(c.submit_now(pid, params()))
        for _ in range(500):
            await asyncio.sleep(0.01)
            if c._prompt_to_job:
                break
        prompt_id = next(iter(c._prompt_to_job))

        error_frame = asyncio.create_task(
            c._handle_event(
                {
                    "type": "execution_error",
                    "data": {
                        "prompt_id": prompt_id,
                        "node_type": "CheckpointLoaderSimple",
                        "exception_message": "checkpoint not found",
                    },
                }
            )
        )
        await asyncio.sleep(0.05)
        gate.set()
        await error_frame
        job_id = await submit

        job = db.get_generation_job(job_id)
        assert job["state"] == "failed"
        assert "checkpoint not found" in job["error"]
    finally:
        await c.aclose()


async def test_queued_jobs_are_re_enqueued_after_a_restart(
    fake_comfy, workspace  # noqa: F811
):
    """Regression guard for final-review I3.

    The module docstring promises "state survives a restart", but nothing
    re-enqueued `state='queued'` rows: `_rehydrate_prompt_map` only reads
    `running` rows, and only into the prompt map. A restart with queued
    work left it queued forever, invisible to the pump.
    """
    db = DatabaseManager(workspace / "db")
    first = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    pid = await first.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await first.submit(pid, params()) for _ in range(3)]
    await first.aclose()  # never started -> nothing was ever dispatched
    assert all(db.get_generation_job(j)["state"] == "queued" for j in job_ids)

    second = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        in_flight=2,
    )
    await second.start()
    try:
        for job_id in job_ids:
            assert (await second.wait_for_job(job_id, timeout=20.0))["state"] == "done"
    finally:
        await second.shutdown()


async def test_running_jobs_are_reconciled_after_a_restart(
    fake_comfy, workspace  # noqa: F811
):
    """A row left at "running" by a killed process would otherwise
    over-subscribe `in_flight` forever, since no event will ever arrive
    for it. Marking it failed is honest and frees the slot."""
    db = DatabaseManager(workspace / "db")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    orphan = db.create_generation_job(pid, "{}")
    db.update_generation_job(orphan, state="running", comfy_prompt_id="gone")

    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
    )
    await c.start()
    try:
        job = db.get_generation_job(orphan)
        assert job["state"] == "failed"
        assert "restart" in job["error"].lower()
        assert job["finished_at"]
    finally:
        await c.shutdown()


async def test_cancelling_an_orphaned_queued_job_marks_it_cancelled(
    fake_comfy, workspace  # noqa: F811
):
    """A queued row this process never put in `_queue` (e.g. left by a
    previous run) must still be cancellable. `cancel()` used to hit
    ValueError on `self._queue.remove(...)`, return, and leave the row
    queued -- while `POST /jobs/{id}/cancel` reported success and leaked
    a `_cancelled` entry."""
    db = DatabaseManager(workspace / "db")
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    orphan = db.create_generation_job(pid, "{}")

    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
    )
    try:
        await c.cancel(orphan)
        assert db.get_generation_job(orphan)["state"] == "cancelled"
        assert orphan not in c._cancelled
    finally:
        await c.aclose()


async def test_a_traversing_filename_cannot_escape_the_output_root(
    fake_comfy, workspace  # noqa: F811
):
    """`collect_outputs` joined ComfyUI's `filename` onto the target dir
    verbatim, so a name containing `../` wrote outside output_root."""
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
    )
    try:
        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
        job_id = await c.submit_now(pid, params())
        prompt_id = db.get_generation_job(job_id)["comfy_prompt_id"]

        async def evil_history(_prompt_id):
            return {
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": "../../../escaped.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                }
            }

        c.fetch_history = evil_history  # type: ignore[method-assign]
        written = await c.collect_outputs(job_id, prompt_id)

        assert written
        out_root = (workspace / "out").resolve()
        for path in written:
            assert path.resolve().is_relative_to(out_root)
        assert not (workspace / "escaped.png").exists()
        assert not (workspace.parent / "escaped.png").exists()
    finally:
        await c.aclose()


async def test_a_cancelled_job_emits_no_job_outputs_event(
    fake_comfy, workspace  # noqa: F811
):
    """When `collect_outputs` bails because the job went terminal, the
    empty result must not be published as this job's outputs."""
    db = DatabaseManager(workspace / "db")
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=db,
        scanner=Scanner(db),
    )
    seen = []
    c.on_job_event(lambda event, payload: seen.append((event, payload)))
    try:
        pid = await c.register_preset("sdxl", "t2i", t2i_workflow())
        job_id = await c.submit_now(pid, params())
        prompt_id = db.get_generation_job(job_id)["comfy_prompt_id"]

        gate = asyncio.Event()
        original_download = c._download_image

        async def gated_download(entry, target):
            await gate.wait()
            await original_download(entry, target)

        c._download_image = gated_download  # type: ignore[method-assign]

        await c._on_prompt_end(job_id, {"prompt_id": prompt_id})
        await asyncio.sleep(0.2)
        await c.cancel(job_id)
        gate.set()
        for task in list(c._collect_tasks):
            await task

        assert db.get_generation_job(job_id)["state"] == "cancelled"
        assert [p for e, p in seen if e == "job_outputs"] == []
    finally:
        await c.aclose()
