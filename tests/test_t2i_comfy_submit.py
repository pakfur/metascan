"""ComfyClient.submit carries a t2i_batch_id onto the job row.

Driven by the in-process fake ComfyUI, like tests/test_comfy_client.py.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import GenerationParams
from metascan.core.comfy_client import ComfyClient
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
    base = dict(
        positive="a lighthouse at dusk", seed=42, width=1024, height=576, batch_size=1
    )
    base.update(kw)
    return GenerationParams(**base)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
async def client(fake_comfy, workspace):  # noqa: F811
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=DatabaseManager(workspace / "db"),
        in_flight=2,
    )
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
async def started_client(fake_comfy, workspace):  # noqa: F811
    c = ComfyClient(
        base_url=fake_comfy.base_url,
        output_root=workspace / "out",
        db=DatabaseManager(workspace / "db"),
        in_flight=2,
    )
    await c.start()
    try:
        yield c
    finally:
        await c.shutdown()


async def test_submit_stores_the_batch_id_on_the_job_row(client):
    pid = await client.register_preset("krea2", "t2i", t2i_workflow())

    job_id = await client.submit(pid, params(), t2i_batch_id="abc")

    assert client.db.get_generation_job(job_id)["t2i_batch_id"] == "abc"
    rows = {r["id"]: r for r in client.db.list_generation_jobs()}
    assert rows[job_id]["t2i_batch_id"] == "abc"


async def test_submit_without_a_batch_id_leaves_it_null(client):
    pid = await client.register_preset("krea2", "t2i", t2i_workflow())

    job_id = await client.submit(pid, params())

    assert client.db.get_generation_job(job_id)["t2i_batch_id"] is None


async def test_the_batch_id_reaches_create_generation_job_positionally(client):
    pid = await client.register_preset("krea2", "t2i", t2i_workflow())
    calls = []
    real = client.db.create_generation_job

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    client.db.create_generation_job = spy

    await client.submit(
        pid, params(), output_dir=Path("/tmp/x"), output_name="stem", t2i_batch_id="b1"
    )

    ((args, kwargs),) = calls
    # (preset_id, params_json, panel_id, output_dir, beat_id, output_prefix,
    #  i2v_source_path, output_name, t2i_batch_id) -- all positional.
    assert kwargs == {}
    assert len(args) == 9
    assert args[7] == "stem"
    assert args[8] == "b1"


async def test_a_batch_job_keeps_its_batch_id_through_to_done(started_client):
    pid = await started_client.register_preset("krea2", "t2i", t2i_workflow())

    job_id = await started_client.submit(pid, params(), t2i_batch_id="abc")
    job = await started_client.wait_for_job(job_id, timeout=10.0)

    assert job["state"] == "done"
    assert job["t2i_batch_id"] == "abc"


def test_the_signatures_only_grew_at_the_end_of_submit():
    submit = list(inspect.signature(ComfyClient.submit).parameters)
    assert submit[-2:] == ["output_name", "t2i_batch_id"]
    # submit_now is unchanged: nothing in the t2i flow uses it.
    assert list(inspect.signature(ComfyClient.submit_now).parameters) == [
        "self",
        "preset_id",
        "params",
        "panel_id",
        "output_dir",
    ]
