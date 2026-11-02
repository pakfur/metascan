"""Upscale runs against the fake ComfyUI: candidates, never media."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core import upscale_runner as runner_mod
from metascan.core.comfy_client import ComfyClient
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.core.upscale_runner import (
    UpscaleNotFoundError,
    UpscaleRequestError,
    UpscaleRunner,
    UpscaleRunRequest,
)
from metascan.utils.path_utils import to_native_path, to_posix_path
from tests._fake_comfy_server import fake_comfy  # noqa: F401


def node(class_type: str, title: str, **inputs: Any) -> dict:
    return {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}


def image_workflow(seed: bool = True) -> dict:
    graph = {
        "1": node("LoadImage", "MS_SOURCE_IMAGE", image="baked.png"),
        "2": node("SaveImage", "MS_SAVE", images=["1", 0], filename_prefix="up"),
    }
    if seed:
        graph["3"] = node("KSampler", "MS_SEED", seed=1)
    return graph


class Env:
    def __init__(self, tmp: Path, comfy: ComfyClient) -> None:
        self.tmp = tmp
        self.comfy = comfy
        self.db = comfy.db
        self.scanner = Scanner(self.db, ThumbnailCache(tmp / "thumbs"))
        self.events: List[Any] = []
        self.runner = self.make_runner()
        self.source = tmp / "library" / "a.png"
        self.source.parent.mkdir(parents=True)
        Image.new("RGB", (32, 32), "red").save(self.source)
        self.scanner.ingest_file(self.source)
        self.posix = to_posix_path(self.source)
        self.staging = tmp / "staging"

    def make_runner(self) -> UpscaleRunner:
        runner = UpscaleRunner(
            db=self.db,
            comfy=self.comfy,
            staging_root=lambda: self.tmp / "staging",
            technical=self.scanner.read_technical,
            supported=Scanner.SUPPORTED_EXTENSIONS,
        )
        runner.on_event(lambda ch, ev, data: self.events.append((ch, ev, data)))
        self.comfy.on_job_event(runner.handle_job_event)
        return runner

    def request(self, preset_id: int, **kw: Any) -> UpscaleRunRequest:
        values: Dict[str, Any] = dict(
            source_path=self.posix, preset_id=preset_id, seed=5
        )
        values.update(kw)
        return UpscaleRunRequest(**values)

    def names(self) -> List[str]:
        return [e[1] for e in self.events]


@pytest.fixture
async def env(fake_comfy):  # noqa: F811
    fake_comfy.images_per_job = 1
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        c = ComfyClient(
            base_url=fake_comfy.base_url,
            output_root=root / "out",
            db=DatabaseManager(root / "db"),
            scanner=None,
            in_flight=1,
        )
        c.scanner = Scanner(c.db, ThumbnailCache(root / "t"))
        await c.start()
        try:
            yield Env(root, c)
        finally:
            await c.shutdown()


async def until(pred: Any, timeout: float = 10.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.01)


async def finished(env: Env) -> None:
    await until(lambda: any(n in ("batch_done", "batch_failed") for n in env.names()))
    await until(lambda: not env.runner._record_tasks)


async def test_a_run_makes_one_candidate_per_count_and_no_media(
    env, fake_comfy
):  # noqa: F811
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    started = await env.runner.start_run(
        env.request(pid, count=3, seed_policy="increment")
    )
    assert started.total == 3 and started.next_seed == 8
    await finished(env)

    rows = env.db.list_upscale_candidates(env.posix)
    assert sorted(r["seed"] for r in rows) == [5, 6, 7]
    assert all(
        Path(to_native_path(r["file_path"])).parent.parent == env.staging for r in rows
    )
    assert all(r["preset_name"] == "up" and r["kind"] == "upscale_image" for r in rows)
    assert env.db.all_media_paths() == [env.posix]
    assert "batch_done" in env.names() and "candidates_changed" in env.names()


async def test_the_source_is_the_original_once_upscaled(env, fake_comfy):  # noqa: F811
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    original = env.tmp / "originals" / "kept.png"
    original.parent.mkdir()
    Image.new("RGB", (16, 16), "green").save(original)
    with env.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO upscale_records (file_path, original_path, original_name, "
            "upscaled_size) VALUES (?, ?, 'a.png', 1)",
            (env.posix, to_posix_path(original)),
        )
        conn.commit()

    uploaded: List[Path] = []
    real = env.comfy.upload_file

    async def record(path: Path) -> str:
        uploaded.append(Path(path))
        return await real(path)

    env.comfy.upload_file = record
    await env.runner.start_run(env.request(pid))
    await finished(env)
    assert uploaded == [original]


async def test_a_workflow_with_no_seed_makes_one(env):
    pid = await env.comfy.register_preset(
        "up", "upscale_image", image_workflow(seed=False)
    )
    started = await env.runner.start_run(env.request(pid, seed=None, count=4))
    assert started.total == 1
    await finished(env)
    assert len(env.db.list_upscale_candidates(env.posix)) == 1


async def test_a_seeded_workflow_needs_a_seed(env):
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    with pytest.raises(UpscaleRequestError):
        await env.runner.start_run(env.request(pid, seed=None))


async def test_a_fixed_seed_makes_one(env):
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    started = await env.runner.start_run(env.request(pid, count=4, seed_policy="fixed"))
    assert started.total == 1
    await finished(env)


async def test_a_workflow_of_the_wrong_type_is_refused(env):
    graph = {
        "1": node("VHS_LoadVideo", "MS_SOURCE_VIDEO", video="a.mp4"),
        "2": node("VHS_VideoCombine", "MS_SAVE", images=["1", 0]),
    }
    pid = await env.comfy.register_preset("v", "upscale_video", graph)
    with pytest.raises(UpscaleRequestError, match="image"):
        await env.runner.start_run(env.request(pid))


async def test_a_prompt_or_scale_the_workflow_cannot_take_is_refused(env):
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    with pytest.raises(UpscaleRequestError, match="prompt"):
        await env.runner.start_run(env.request(pid, prompt="sharper"))
    with pytest.raises(UpscaleRequestError, match="scale"):
        await env.runner.start_run(env.request(pid, scale=2.0))


async def test_a_missing_file_is_refused(env):
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    env.source.unlink()
    with pytest.raises(UpscaleRequestError):
        await env.runner.start_run(env.request(pid))


async def test_an_output_metascan_cannot_read_gives_no_candidate(
    env, fake_comfy
):  # noqa: F811
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    fake_comfy.output_override = {
        "images": [{"filename": "x.gifv", "subfolder": "", "type": "output"}]
    }
    await env.runner.start_run(env.request(pid))
    await finished(env)
    assert env.db.list_upscale_candidates(env.posix) == []
    assert "no_output" in env.names()


async def test_cancel_stops_the_run_and_keeps_finished_candidates(
    env, fake_comfy
):  # noqa: F811
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    started = await env.runner.start_run(env.request(pid, count=1))
    await finished(env)
    assert len(env.db.list_upscale_candidates(env.posix)) == 1
    assert await env.runner.cancel_batch(started.batch_id) is False

    fake_comfy.hold = asyncio.Event()
    env.events.clear()
    second = await env.runner.start_run(
        env.request(pid, count=2, seed_policy="increment")
    )
    await until(lambda: env.comfy.source_in_flight("upscale") == 1)
    assert await env.runner.cancel_batch(second.batch_id) is True
    fake_comfy.hold.set()
    assert "batch_cancelled" in env.names()
    assert len(env.db.list_upscale_candidates(env.posix)) == 1


async def test_an_unknown_run_is_not_found(env):
    with pytest.raises(UpscaleNotFoundError):
        await env.runner.cancel_batch("nope")


async def test_a_gate_holds_the_run_before_it_uploads(env):
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    gate = asyncio.Event()
    env.runner.wait_unpaused = gate.wait
    uploads: List[Any] = []
    real = env.comfy.upload_file

    async def record(path: Path) -> str:
        uploads.append(path)
        return await real(path)

    env.comfy.upload_file = record
    await env.runner.start_run(env.request(pid))
    await asyncio.sleep(0.05)
    assert uploads == [] and env.runner.progress_counts()[:2] == (0, 1)
    gate.set()
    await finished(env)
    assert len(uploads) == 1


async def test_a_finished_job_with_no_listener_is_recorded_by_the_sweep(env):
    """A restart: outputs landed, the row says done, no candidate exists."""
    pid = await env.comfy.register_preset("up", "upscale_image", image_workflow())
    folder = env.staging / "job"
    folder.mkdir(parents=True)
    Image.new("RGB", (64, 64), "blue").save(folder / "candidate.png")
    (folder / "candidate.json").write_text("{}")
    job = env.db.create_generation_job(
        pid,
        '{"seed": 3, "positive": null}',
        output_dir=str(folder),
        upscale_source_path=env.posix,
    )
    env.db.update_generation_job(job, state="done")

    assert env.runner.recover_finished() == 1
    assert env.runner.recover_finished() == 0
    rows = env.db.list_upscale_candidates(env.posix)
    assert [(r["seed"], r["width"]) for r in rows] == [(3, 64)]


async def test_a_lost_soundtrack_is_flagged(env, monkeypatch):
    monkeypatch.setattr(runner_mod, "_has_audio", lambda path: "library" in str(path))
    source = env.tmp / "library" / "clip.mp4"
    assert env.runner._audio_lost(to_posix_path(source), env.tmp / "out.mp4") is True
    assert (
        env.runner._audio_lost(to_posix_path(source), env.tmp / "library/o.mp4")
        is False
    )


async def test_unknown_audio_is_not_lost(env, monkeypatch):
    monkeypatch.setattr(runner_mod, "_has_audio", lambda path: None)
    assert env.runner._audio_lost("/x/a.mp4", Path("/x/b.mp4")) is False
