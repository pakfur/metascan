"""ComfyClient tests, driven entirely by the in-process fake ComfyUI."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError, _next_backoff
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

    for _ in range(50):
        await asyncio.sleep(0.02)
        if started_client.db.get_generation_job(job_id)["state"] == "running":
            break

    await started_client.cancel(job_id)

    assert fake_comfy.interrupted == 1
    assert started_client.db.get_generation_job(job_id)["state"] == "cancelled"


async def test_cancel_all_clears_the_queue(started_client, fake_comfy):  # noqa: F811
    fake_comfy.execution_delay = 0.5
    pid = await started_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_ids = [await started_client.submit(pid, params()) for _ in range(6)]

    await asyncio.sleep(0.05)
    await started_client.cancel_all()

    assert started_client.queue_depth() == 0
    states = {started_client.db.get_generation_job(j)["state"] for j in job_ids}
    # Tightened: cancel_all() must resolve every job it touches -- including
    # ones mid-dispatch when it runs -- before returning. "running" would
    # mean cancel_all() missed a job (see the _cancelled handshake in
    # cancel()/_dispatch); "queued" would mean the queue wasn't drained.
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

    for f in ingesting_client.output_dir_for(job_id).glob("*.png"):
        assert ingesting_client.db.get_media(str(f)) is not None


async def test_a_job_is_only_done_after_its_images_land(ingesting_client):
    pid = await ingesting_client.register_preset("sdxl", "t2i", t2i_workflow())
    job_id = await ingesting_client.submit(pid, params())
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

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
    await ingesting_client.wait_for_job(job_id, timeout=10.0)

    assert not list(ingesting_client.output_dir_for(job_id).glob("*.png"))


async def test_a_cancelled_job_does_not_download_or_ingest_its_outputs(
    fake_comfy, workspace  # noqa: F811
):
    """Regression guard: a cancelled job's outputs must never be
    downloaded or ingested, and the job must not be resurrected to
    "done" (review finding, not covered by any earlier task's tests).

    ComfyUI's /interrupt is not synchronous -- a job can still emit
    `executed` (and _on_executed can still schedule a collection task)
    after cancel() has already flagged the job in `_cancelled`, or even
    after cancel() has already written state="cancelled". Rather than
    fight that inherently timing-dependent race (see this file's
    docstrings on the cancel()/_dispatch handshake for why that's
    flaky-by-nature), this reproduces the exact state such a frame would
    find deterministically: flag the job cancelled, then drive
    `_on_executed` directly, exactly as the reader loop would.

    Deliberately builds its own client and never calls `start()`: with a
    live websocket (as `ingesting_client` has), fake_comfy's background
    execution task -- which runs with no delay by default -- broadcasts a
    *real* `executed` frame that the reader loop processes concurrently
    with this test's manual steps, downloading the images for real before
    `_cancelled` is even set. That raced this test intermittently. With no
    socket connected, fake_comfy has nothing to broadcast to, so the only
    `_on_executed` call is the explicit one below.
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

        await c._on_executed(job_id, {"prompt_id": prompt_id})
        for task in list(c._collect_tasks):
            await task

        assert not list(c.output_dir_for(job_id).glob("*.png"))
        # _complete_job returned before calling _finish_job at all -- the
        # job is left exactly as cancel() would have left it (never
        # resurrected to "done" by the collection task that raced it).
        assert c.db.get_generation_job(job_id)["state"] == "running"
    finally:
        await c.aclose()
