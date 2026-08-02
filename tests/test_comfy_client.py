"""ComfyClient tests, driven entirely by the in-process fake ComfyUI."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError
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
