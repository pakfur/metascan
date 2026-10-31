"""ComfyClient's per-source submission gate (Background activity, spec 6.3)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_client import ComfyClient, job_source
from metascan.core.database_sqlite import DatabaseManager
from tests._fake_comfy_server import fake_comfy  # noqa: F401
from tests.test_comfy_client import _until, params, t2i_workflow


@pytest.fixture
async def gated(fake_comfy):  # noqa: F811
    with tempfile.TemporaryDirectory() as tmp:
        c = ComfyClient(
            base_url=fake_comfy.base_url,
            output_root=Path(tmp) / "out",
            db=DatabaseManager(Path(tmp) / "db"),
            in_flight=1,
        )
        await c.start()
        try:
            yield c
        finally:
            await c.shutdown()


def prompts(fake) -> list:
    return [s["body"]["prompt"]["6"]["inputs"]["text"] for s in fake.submitted]


def test_job_source_classification():
    assert job_source(t2i_batch_id="b") == "t2i"
    assert job_source(i2v_source_path="/a.png") == "i2v"
    assert job_source(panel_id=3) == "storyboard"
    assert job_source(beat_id=3) == "storyboard"
    assert job_source() is None


def test_unknown_source_is_refused(tmp_path):
    c = ComfyClient("http://127.0.0.1:1", tmp_path, db=None)
    with pytest.raises(ValueError):
        c.set_source_paused("nope", True)


async def test_a_gated_job_stays_queued_while_another_source_dispatches(
    gated, fake_comfy  # noqa: F811
):
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    gated.set_source_paused("t2i", True)
    held = await gated.submit(pid, params(positive="t2i"), t2i_batch_id="b1")
    other = await gated.submit(pid, params(positive="i2v"), i2v_source_path="/x.png")
    plain = await gated.submit(pid, params(positive="plain"))

    assert (await gated.wait_for_job(other, timeout=10))["state"] == "done"
    assert (await gated.wait_for_job(plain, timeout=10))["state"] == "done"
    assert prompts(fake_comfy) == ["i2v", "plain"]
    assert gated.db.get_generation_job(held)["state"] == "queued"
    assert gated.queue_depth() == 1


async def test_submit_returns_at_once_while_gated(gated):
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    gated.set_source_paused("t2i", True)
    job = await asyncio.wait_for(
        gated.submit(pid, params(), t2i_batch_id="b1"), timeout=2
    )
    assert gated.db.get_generation_job(job)["state"] == "queued"


async def test_ungating_dispatches_in_the_original_order(
    gated, fake_comfy  # noqa: F811
):
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    gated.set_source_paused("t2i", True)
    ids = [
        await gated.submit(pid, params(positive=f"j{i}"), t2i_batch_id="b1")
        for i in range(3)
    ]
    await asyncio.sleep(0.7)
    assert prompts(fake_comfy) == []
    gated.set_source_paused("t2i", False)
    for job in ids:
        await gated.wait_for_job(job, timeout=10)
    assert prompts(fake_comfy) == ["j0", "j1", "j2"]


async def test_a_running_job_is_not_interrupted_by_the_gate(
    gated, fake_comfy  # noqa: F811
):
    fake_comfy.hold = asyncio.Event()
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    running = await gated.submit(pid, params(positive="a"), t2i_batch_id="b1")
    assert await _until(lambda: gated.source_in_flight("t2i") == 1)
    gated.set_source_paused("t2i", True)
    queued = await gated.submit(pid, params(positive="b"), t2i_batch_id="b1")
    await asyncio.sleep(0.3)
    assert gated.db.get_generation_job(running)["state"] == "running"
    assert gated.source_in_flight("t2i") == 1
    fake_comfy.hold.set()
    assert (await gated.wait_for_job(running, timeout=10))["state"] == "done"
    assert gated.source_in_flight("t2i") == 0
    await asyncio.sleep(0.7)
    assert gated.db.get_generation_job(queued)["state"] == "queued"


async def test_has_active_jobs_ignores_gated_queued_jobs(gated):
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    gated.set_source_paused("t2i", True)
    await gated.submit(pid, params(), t2i_batch_id="b1")
    assert gated.has_pending_jobs()
    assert not gated.has_active_jobs()
    gated.set_source_paused("t2i", False)
    assert gated.has_active_jobs()


async def test_a_gated_queued_job_can_be_cancelled(gated):
    pid = await gated.register_preset("sdxl", "t2i", t2i_workflow())
    gated.set_source_paused("t2i", True)
    job = await gated.submit(pid, params(), t2i_batch_id="b1")
    await gated.cancel(job)
    assert gated.db.get_generation_job(job)["state"] == "cancelled"
    assert gated.queue_depth() == 0


async def test_a_restart_requeue_is_classified_from_the_row(
    fake_comfy, tmp_path  # noqa: F811
):
    db = DatabaseManager(tmp_path / "db")
    first = ComfyClient(fake_comfy.base_url, tmp_path / "out", db, in_flight=1)
    pid = await first.register_preset("sdxl", "t2i", t2i_workflow())
    job = await first.submit(pid, params(), i2v_source_path="/x.png")
    await first.aclose()

    second = ComfyClient(fake_comfy.base_url, tmp_path / "out", db, in_flight=1)
    second.set_source_paused("i2v", True)  # before the pump exists
    await second.start()
    try:
        await asyncio.sleep(0.7)
        assert db.get_generation_job(job)["state"] == "queued"
        second.set_source_paused("i2v", False)
        assert (await second.wait_for_job(job, timeout=10))["state"] == "done"
    finally:
        await second.shutdown()
