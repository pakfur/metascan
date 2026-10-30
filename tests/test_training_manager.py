"""Writer LoRA training manager (Task 6): the GPU lock, the run lifecycle
on the FAKE worker backend (no CUDA, no torch), the API and the gate.

Every run spawns the real worker subprocess (``backend: "fake"``) in a temp
data dir; the dataset builder is replaced by canned examples."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from backend.services import training_manager as tm
from backend.services.gpu_lock import require_gpu_free
from backend.services.training_manager import TrainingError, TrainingManager
from metascan.core import gpu_guard
from metascan.core import writer_dataset as wd
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.hardware import (
    CudaInfo,
    Gate,
    HardwareReport,
    WriterTrainingPaths,
    clear_writer_training_probe_cache,
    writer_training_gate,
    writer_training_reasons,
)
from metascan.core.writer_adapters import adapter_dir

TIMEOUT = 60.0


# ---- fixtures ------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture(autouse=True)
def _clear_reservation():
    yield
    gpu_guard.set_gpu_reservation(None)


def _examples(n_pairs: int) -> Tuple[list, list, wd.DatasetCounts]:
    prefs = [
        wd.PrefExample(
            system="S",
            user=f"u{i}",
            chosen=f"c{i}",
            rejected=f"r{i}",
            target_model="krea2",
            categories=[],
            source="variant",
            group=f"krea2|g{i % 20}",
        )
        for i in range(n_pairs)
    ]
    sft = [
        wd.SftExample(
            system="S",
            user=f"s{i}",
            chosen=f"t{i}",
            target_model="krea2",
            categories=[],
            source="edit",
            group=f"krea2|g{i % 20}",
        )
        for i in range(12)
    ]
    counts = wd.DatasetCounts(
        pref_pairs=n_pairs, edit_pairs=0, hindsight=0, content_mode="uncensored"
    )
    return sft, prefs, counts


@pytest.fixture
def dataset(monkeypatch):
    state: Dict[str, Any] = {"n": 120, "calls": []}

    def build(db, scope, store, now, *, content_mode, resolve=None):
        state["calls"].append((scope, content_mode))
        return _examples(state["n"])

    monkeypatch.setattr(wd, "build_examples", build)
    return state


class Recorder:
    def __init__(self) -> None:
        self.log: List[Tuple[str, Any]] = []

    def events(self, name: str) -> List[Any]:
        return [d for e, d in self.log if e == name]


def _paths(tmp_path: Path) -> WriterTrainingPaths:
    return WriterTrainingPaths(
        python=sys.executable,
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "convert_lora_to_gguf.py",
    )


def _manager(
    db: Any,
    tmp_path: Path,
    rec: Recorder,
    *,
    busy: Optional[List[str]] = None,
    fake: Optional[Dict[str, Any]] = None,
    gate: Optional[Gate] = None,
    **kw: Any,
) -> TrainingManager:
    async def release() -> None:
        rec.log.append(("release_gpu", None))

    async def refresh() -> None:
        rec.log.append(("refresh_adapters", None))

    return TrainingManager(
        db,
        tmp_path / "data",
        dataset_inputs=lambda: (object(), "uncensored", None),
        gpu_busy=lambda: list(busy or []),
        release_gpu=release,
        refresh_adapters=refresh,
        broadcast=lambda e, d: rec.log.append((e, d)),
        paths=lambda: _paths(tmp_path),
        gate=lambda p: gate or Gate(True, True, "", []),
        backend="fake",
        fake={"sft_steps": 4, "dpo_steps": 4, "progress_every": 1, **(fake or {})},
        poll_s=0.02,
        **kw,
    )


async def _until(pred, timeout: float = TIMEOUT) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def _idle(m: TrainingManager) -> None:
    await _until(lambda: m.status()["state"] == "idle")


SLOW = {"sft_steps": 200, "dpo_steps": 200, "step_sleep_s": 0.05}


def _speed_up(db: Any, run_id: int) -> None:
    """Drop the fake's per-step sleep before a resume (the fake knobs are
    not part of the worker's resume fingerprint)."""
    import json

    task_path = Path(db.get_training_run(run_id)["run_dir"]) / "task.json"
    task = json.loads(task_path.read_text())
    task["fake"]["step_sleep_s"] = 0.0
    task_path.write_text(json.dumps(task))


# ---- hyperparameters -----------------------------------------------------------


def test_presets_resolve_and_overrides_are_clamped():
    presets = tm.resolved_presets()
    assert presets["quick"]["rank"] == 8 and presets["thorough"]["epochs"] == 3
    assert presets["standard"]["dpo_lr"] == 1e-5
    hp = tm.resolve_hyperparams("quick", {"rank": 999, "lr": 1.0, "epochs": 0.2})
    assert hp == {"rank": 64, "lr": 5e-4, "epochs": 1, "dpo_lr": 1e-5}
    with pytest.raises(TrainingError) as e:
        tm.resolve_hyperparams("quick", {"beta": 0.2})
    assert e.value.status == 422
    with pytest.raises(TrainingError):
        tm.resolve_hyperparams("insane")


def test_training_paths_from_config(tmp_path):
    p = tm.training_paths_from_config({}, tmp_path)
    assert p.python == sys.executable
    assert p.base_dir == (
        tmp_path / "models/vlm/train/huihui-ai--Huihui-Qwen3-VL-8B-Instruct-abliterated"
    )
    assert p.converter_script == tmp_path / "llama.cpp/convert_lora_to_gguf.py"
    p = tm.training_paths_from_config(
        {
            "models": {
                "writer_train_python": "/opt/py/bin/python",
                "writer_train_repo": "org/repo",
                "llama_cpp_dir": str(tmp_path / "lc"),
            }
        },
        tmp_path,
    )
    assert p.python == "/opt/py/bin/python"
    assert p.base_dir == tmp_path / "models/vlm/train/org--repo"
    assert p.converter_script == tmp_path / "lc/convert_lora_to_gguf.py"


# ---- the run lifecycle ------------------------------------------------------------


async def test_a_completed_run_registers_exactly_one_adapter(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    run_id = await m.start("all", "quick")
    assert m.gpu_locked()
    await _idle(m)
    assert not m.gpu_locked()

    run = db.get_training_run(run_id)
    assert run["status"] == "completed"
    adapters = db.list_writer_adapters()
    assert len(adapters) == 1
    a = adapters[0]
    assert a["status"] == "untested" and a["name"] == "taste-all-v1"
    assert a["scope"] == "all" and a["base_model"] == "qwen3vl-8b"
    assert a["data_cutoff"] and a["holdout_agreement"] is not None
    assert a["counts"]["content_mode"] == "uncensored"
    folder = adapter_dir(tmp_path / "data", a["id"])
    for name in ("adapter.gguf", "adapter_config.json", "meta.json"):
        assert (folder / name).is_file()
    assert (folder / "peft" / "adapter_config.json").is_file()
    assert a["file_path"] == str(folder / "adapter.gguf")
    import json

    meta = json.loads((folder / "meta.json").read_text())
    assert meta["counts"]["content_mode"] == "uncensored"
    assert meta["adapter_id"] == a["id"] and meta["run_id"] == run_id
    assert not Path(run["run_dir"]).exists()  # checkpoints + JSONL cleaned

    names = [e for e, _ in rec.log]
    assert names.index("release_gpu") < names.index("run_started")
    assert rec.events("run_progress")
    assert rec.events("run_completed") == [{"run_id": run_id, "adapter_id": a["id"]}]
    # The writer reloads its adapter list BEFORE the UI hears of it.
    assert names.index("refresh_adapters") < len(names) - 1 - names[::-1].index(
        "adapters_changed"
    )
    assert names.count("refresh_adapters") == 1

    # A duplicate completion (e.g. a second supervisor) registers nothing.
    active = tm._Active(
        run_id=run_id,
        adapter_id=a["id"],
        name=a["name"],
        scope="all",
        run_dir=Path(run["run_dir"]),
        resumed=False,
    )
    await m._complete(active, {"status": "ok"})
    assert not db.complete_training_run(run_id, a["id"], {})
    assert names.count("refresh_adapters") == 1
    assert len(db.list_writer_adapters()) == 1
    assert len(rec.events("run_completed")) == 1


async def test_jsonl_files_keep_the_holdout_out_of_training(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    run_dir = Path(db.get_training_run(run_id)["run_dir"])
    import json

    task = json.loads((run_dir / "task.json").read_text())
    assert task["backend"] == "fake" and task["resume"] is False
    assert task["data_root"] == str(run_dir)
    train = (run_dir / "data/prefs.jsonl").read_text().splitlines()
    held = (run_dir / "data/holdout.jsonl").read_text().splitlines()
    assert held and len(train) + len(held) == 120
    held_groups = {json.loads(x)["group"] for x in held}
    assert not held_groups & {json.loads(x)["group"] for x in train}
    sft = (run_dir / "data/sft.jsonl").read_text().splitlines()
    assert not held_groups & {json.loads(x)["group"] for x in sft}
    await m.cancel()
    await _idle(m)


async def test_start_refusals(db, tmp_path, dataset):
    rec = Recorder()
    gate = Gate(False, False, "a b", ["no CUDA", "no deps"])
    m = _manager(db, tmp_path, rec, gate=gate)
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert (e.value.status, e.value.code) == (409, "gate_unavailable")
    assert e.value.detail()["reasons"] == ["no CUDA", "no deps"]

    m = _manager(db, tmp_path, rec)
    dataset["n"] = 50
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert e.value.code == "not_ready"
    assert e.value.detail()["readiness"]["missing"] == 50
    with pytest.raises(TrainingError) as e:
        await m.start("12345", "quick")
    assert (e.value.status, e.value.code) == (422, "unknown_scope")
    with pytest.raises(TrainingError) as e:
        await m.start("all", "insane")
    assert e.value.status == 422
    assert db.list_writer_adapters() == [] and not m.gpu_locked()

    dataset["n"] = 120
    m = _manager(db, tmp_path, rec, fake=SLOW)
    await m.start("all", "quick")
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert e.value.code == "already_training"
    await m.cancel()
    await _idle(m)


async def test_start_refused_while_gpu_busy_releases_the_lock(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, busy=["comfy_jobs"])
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert (e.value.status, e.value.code) == (409, "gpu_busy")
    assert e.value.detail()["reasons"] == ["comfy_jobs"]
    assert not m.gpu_locked()
    assert ("release_gpu", None) not in rec.log
    assert db.list_writer_adapters() == [] and db.list_training_runs() == []


class _FakeComfy:
    def __init__(self) -> None:
        self.pending = False

    def has_pending_jobs(self) -> bool:
        return self.pending


class _FakeT2i:
    def __init__(self) -> None:
        self.batches: List[Any] = []

    def active_batches(self) -> List[Any]:
        return self.batches


class _FakeUnloadable:
    def __init__(self) -> None:
        self.calls = 0

    async def shutdown_all(self) -> None:
        self.calls += 1

    async def shutdown(self) -> None:
        self.calls += 1


async def test_lifespan_wiring_refuses_while_a_comfy_job_is_pending(
    db, tmp_path, dataset
):
    """The real gpu_busy / release_gpu wiring from backend.main."""
    from backend.main import _build_training_manager

    comfy, t2i = _FakeComfy(), _FakeT2i()
    roles, clip = _FakeUnloadable(), _FakeUnloadable()
    m = _build_training_manager(
        db=db,
        data_dir=tmp_path / "data",
        comfy_client=comfy,
        t2i_runner=t2i,
        inference_client=clip,
        vlm_roles=roles,
    )
    rec = Recorder()
    m._dataset_inputs = lambda: (object(), "uncensored", None)
    m._paths = lambda: _paths(tmp_path)
    m._gate = lambda p: Gate(True, True, "", [])
    m._broadcast = lambda e, d: rec.log.append((e, d))

    async def no_refresh() -> None:
        return None

    m._refresh_adapters = no_refresh
    m.backend, m.fake, m.poll_s = "fake", {"sft_steps": 3, "dpo_steps": 3}, 0.02

    comfy.pending = True
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert e.value.code == "gpu_busy" and e.value.extra["reasons"] == ["comfy_jobs"]
    comfy.pending = False
    t2i.batches = [{"id": "b"}]
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert e.value.extra["reasons"] == ["t2i_batch"]
    assert roles.calls == 0 and clip.calls == 0
    assert db.list_writer_adapters() == [] and not m.gpu_locked()

    t2i.batches = []
    await m.start("all", "quick")
    assert roles.calls == 1 and clip.calls == 1  # every VLM + CLIP unloaded
    await _idle(m)
    assert db.list_writer_adapters()[0]["status"] == "untested"


async def test_pause_releases_the_lock_and_resume_continues(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 3
    )
    await m.pause()
    await _idle(m)
    assert not m.gpu_locked()
    assert db.get_training_run(run_id)["status"] == "paused"
    adapter_id = db.get_training_run(run_id)["adapter_id"]
    assert db.get_writer_adapter(adapter_id)["status"] == "paused"
    assert rec.events("run_paused")[0]["run_id"] == run_id
    with pytest.raises(TrainingError) as e:
        await m.pause()
    assert e.value.code == "not_running"

    results: List[Dict[str, Any]] = []
    orig = m._read_result

    def spy(active):
        r = orig(active)
        results.append(r)
        return r

    m._read_result = spy  # type: ignore[method-assign]
    _speed_up(db, run_id)
    await m.resume(run_id)
    assert m.gpu_locked()
    await _idle(m)
    assert results[-1]["status"] == "ok"
    assert results[-1]["resumed_from"] is not None  # from the pause checkpoint
    assert db.get_training_run(run_id)["status"] == "completed"
    assert [a["id"] for a in db.list_writer_adapters()] == [adapter_id]
    assert db.get_writer_adapter(adapter_id)["status"] == "untested"
    assert rec.events("run_started")[-1]["resumed"] is True
    with pytest.raises(TrainingError) as e:
        await m.resume(run_id)
    assert e.value.detail() == {"code": "not_resumable", "run_status": "completed"}
    with pytest.raises(TrainingError) as e:
        await m.cancel(run_id)
    assert e.value.code == "not_cancellable"


async def test_server_stop_pauses_and_a_restart_resumes(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW, shutdown_grace_s=20.0)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 2
    )
    await m.aclose()  # lifespan shutdown: pause, checkpoint, exit
    assert db.get_training_run(run_id)["status"] == "paused"
    assert not m.gpu_locked()
    with pytest.raises(TrainingError):
        await m.start("all", "quick")  # a closing manager starts nothing

    m2 = _manager(db, tmp_path, rec, fake={"step_sleep_s": 0.0})
    assert await m2.recover() == []  # nothing was left running
    _speed_up(db, run_id)
    await m2.resume(run_id)
    await _idle(m2)
    assert db.get_training_run(run_id)["status"] == "completed"
    assert len(db.list_writer_adapters()) == 1


async def test_a_crashed_server_leaves_running_and_recover_pauses_it(
    db, tmp_path, dataset
):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 2
    )
    active = m._active
    assert active is not None and active.proc is not None
    # The server process dies: its supervisor never runs again.
    active.supervisor.cancel()
    active.proc.kill()
    await active.proc.wait()
    adapter_id = db.get_training_run(run_id)["adapter_id"]
    assert db.get_training_run(run_id)["status"] == "running"
    assert db.get_writer_adapter(adapter_id)["status"] == "training"

    m2 = _manager(db, tmp_path, rec, fake={"step_sleep_s": 0.0})
    assert await m2.recover() == [run_id]
    assert db.get_training_run(run_id)["status"] == "paused"
    assert db.get_writer_adapter(adapter_id)["status"] == "paused"
    assert [r["run_id"] for r in m2.resumable_runs()] == [run_id]
    _speed_up(db, run_id)
    await m2.resume(run_id)
    await _idle(m2)
    assert db.get_training_run(run_id)["status"] == "completed"
    assert [a["id"] for a in db.list_writer_adapters()] == [adapter_id]


async def test_cancel_cleans_the_run(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    run_dir = Path(db.get_training_run(run_id)["run_dir"])
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.cancel()
    await _idle(m)
    assert not m.gpu_locked()
    run = db.get_training_run(run_id)
    assert run["status"] == "cancelled"
    assert db.list_writer_adapters() == []  # soft-deleted
    assert db.get_writer_adapter(run["adapter_id"])["status"] == "deleted"
    assert not run_dir.exists()
    assert rec.events("run_cancelled") == [
        {"run_id": run_id, "adapter_id": run["adapter_id"]}
    ]

    # A paused run is cancelled by id without a worker.
    run2 = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.pause()
    await _idle(m)
    dir2 = Path(db.get_training_run(run2)["run_dir"])
    assert dir2.exists()
    await m.cancel(run2)
    assert db.get_training_run(run2)["status"] == "cancelled"
    assert not dir2.exists()
    assert db.list_writer_adapters() == []
    # The next adapter never reuses a cancelled run's number.
    await m.start("all", "quick")
    assert db.list_writer_adapters()[0]["name"] == "taste-all-v3"
    await m.cancel()
    await _idle(m)


async def test_a_failed_run_marks_the_adapter_failed(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake={"fail_at": {"stage": "dpo", "step": 2}})
    run_id = await m.start("all", "quick")
    await _idle(m)
    run = db.get_training_run(run_id)
    assert run["status"] == "failed" and "fake failure" in run["error"]
    assert db.get_writer_adapter(run["adapter_id"])["status"] == "failed"
    assert rec.events("run_failed")[0]["run_id"] == run_id
    assert not m.gpu_locked()


async def test_stdout_and_stderr_are_drained(db, tmp_path, dataset, monkeypatch):
    """Both pipes are read line by line: a worker that floods stdout and
    stderr far past the pipe buffer still finishes."""
    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    flood = (
        "import runpy, sys\n"
        "for i in range(20000):\n"
        "    print('stdout line', i)\n"
        "    print('stderr line', i, file=sys.stderr)\n"
        "sys.stdout.flush(); sys.stderr.flush()\n"
        "sys.argv = ['writer_train_worker', sys.argv[1]]\n"
        "runpy.run_module('metascan.workers.writer_train_worker', run_name='__main__')\n"
    )

    def command(active):
        return [sys.executable, "-c", flood, str(active.run_dir / "task.json")]

    monkeypatch.setattr(m, "_command", command)
    seen: Dict[str, int] = {}
    orig_finish = m._finish

    async def finish(active, rc):
        seen.update(active.streams)
        await orig_finish(active, rc)

    monkeypatch.setattr(m, "_finish", finish)
    run_id = await m.start("all", "quick")
    await asyncio.wait_for(_idle(m), timeout=TIMEOUT)
    assert db.get_training_run(run_id)["status"] == "completed"
    assert seen["stdout"] >= 20000 and seen["stderr"] >= 20000


# ---- the lock on every GPU consumer ---------------------------------------------

# (method, path) of every GPU consumer route.
CONSUMERS: List[Tuple[str, str]] = [
    ("POST", "/api/t2i/prompt"),
    ("POST", "/api/t2i/batches"),
    ("POST", "/api/i2v/prompt"),
    ("POST", "/api/i2v/generate"),
    ("POST", "/api/i2v/batches"),
    ("POST", "/api/storyboard/1/parse"),
    ("POST", "/api/storyboard/1/synthesize"),
    ("POST", "/api/storyboard/1/compile"),
    ("POST", "/api/storyboard/1/generate"),
    ("POST", "/api/storyboard/1/generate-video"),
    ("POST", "/api/storyboard/1/compose"),
    ("POST", "/api/storyboard/subjects/1/describe"),
    ("POST", "/api/storyboard/scenes/1/describe"),
    ("POST", "/api/vlm/tag"),
    ("POST", "/api/vlm/retag"),
    ("POST", "/api/vlm/active"),
    ("POST", "/api/chat"),
    ("POST", "/api/prompt/generate"),
    ("POST", "/api/prompt/transform"),
    ("POST", "/api/prompt/clean"),
    ("POST", "/api/similarity/search"),
    ("POST", "/api/similarity/content-search"),
    ("POST", "/api/similarity/index/build"),
    ("POST", "/api/models/inference/start"),
    ("POST", "/api/models/rebuild-index"),
    ("POST", "/api/comfy/submit"),
    ("POST", "/api/upscale"),
    ("POST", "/api/upscale/resume-all"),
]
# Routes that must keep working while training (browsing and rating).
NOT_LOCKED = [
    ("GET", "/api/media"),
    ("POST", "/api/judgments/seen"),
    ("GET", "/api/writers/training"),
    ("GET", "/api/t2i/images"),
    ("POST", "/api/vlm/unload"),
]


def _consumer_modules() -> List[Any]:
    from backend.api import (
        chat,
        comfy,
        i2v,
        judgments,
        media,
        models,
        prompt,
        similarity,
        storyboard,
        t2i,
        upscale,
        vlm,
        writers,
    )

    return [
        t2i,
        i2v,
        storyboard,
        vlm,
        chat,
        prompt,
        similarity,
        models,
        comfy,
        upscale,
        media,
        judgments,
        writers,
    ]


def _consumer_app() -> FastAPI:
    app = FastAPI()
    for mod in _consumer_modules():
        app.include_router(mod.router)
    return app


def _api_routes() -> List[APIRoute]:
    return [
        r
        for mod in _consumer_modules()
        for r in mod.router.routes
        if isinstance(r, APIRoute)
    ]


def _matches(path: str, template: str) -> bool:
    a, b = path.split("/"), template.split("/")
    return len(a) == len(b) and all(x == y or y.startswith("{") for x, y in zip(a, b))


def _accepts_empty_body(route: APIRoute) -> bool:
    from pydantic import TypeAdapter, ValidationError

    assert route.body_field is not None
    try:
        TypeAdapter(route.body_field.field_info.annotation).validate_python({})
    except ValidationError:
        return False
    return True


def _route_has_lock(method: str, path: str) -> Optional[bool]:
    for r in _api_routes():
        if method in r.methods and _matches(path, r.path):
            return any(d.call is require_gpu_free for d in r.dependant.dependencies)
    return None


def test_every_consumer_route_declares_the_lock_and_browsing_does_not():
    for method, path in CONSUMERS:
        assert _route_has_lock(method, path) is True, path
    for method, path in NOT_LOCKED:
        assert _route_has_lock(method, path) is False, path


async def test_the_lock_blocks_every_consumer_and_releases_after(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    gpu_guard.set_gpu_reservation(m.gpu_locked)
    app = _consumer_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await m.start("all", "quick")
        for method, path in CONSUMERS:
            r = await client.request(method, path, json={})
            assert r.status_code == 409, (path, r.status_code, r.text)
            assert r.json()["detail"]["code"] == "gpu_busy_training", path
        await m.cancel()
        await _idle(m)
        assert not gpu_guard.gpu_reserved()
        # Released: the same requests get past the lock to body validation.
        # Only routes whose body is required are sent (a 422 proves the
        # handler is reachable without running it -- a handler would touch
        # the real library); the others are covered by the dependency.
        require_gpu_free()
        checked = 0
        for method, path in CONSUMERS:
            route = next(
                r
                for r in _api_routes()
                if method in r.methods and _matches(path, r.path)
            )
            if route.body_field is None or _accepts_empty_body(route):
                continue
            r = await client.request(method, path, json={})
            assert r.status_code == 422, (path, r.status_code)
            checked += 1
        assert checked >= 15


async def test_clients_refuse_to_load_models_under_the_lock():
    from backend.api import similarity
    from fastapi import HTTPException

    from metascan.core.comfy_client import ComfyClient, ComfyError
    from metascan.core.inference_client import InferenceClient, InferenceError
    from metascan.core.vlm_client import VlmClient, VlmError

    gpu_guard.set_gpu_reservation(lambda: True)
    with pytest.raises(VlmError, match="GPU busy: training"):
        await VlmClient().start("qwen3vl-8b")
    with pytest.raises(InferenceError, match="GPU busy: training"):
        await InferenceClient().start("small", wait_ready=False)
    with pytest.raises(ComfyError, match="GPU busy: training"):
        await ComfyClient.submit(SimpleNamespace(), 1, None)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as e:
        await similarity._start_index_build(rebuild=False, allow_vlm_tagging=False)
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "gpu_busy_training"


# ---- the writers API ---------------------------------------------------------------


async def test_training_routes(db, tmp_path, dataset, monkeypatch):
    from backend.api import writers

    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    tm.set_training_manager(m)
    monkeypatch.setattr(writers, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(writers.router)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/writers/training", params={"scope": "all"})
            body = r.json()
            assert r.status_code == 200
            assert body["state"] == "idle" and body["gate"]["available"] is True
            assert body["dataset"]["readiness"]["trainable"] is True
            assert body["dataset"]["counts"]["content_mode"] == "uncensored"
            assert set(body["presets"]) == {"quick", "standard", "thorough"}
            assert body["clamps"]["rank"] == [4, 64]
            assert body["next_name"] == "taste-all-v1"

            r = await c.get("/api/writers/dataset/preview", params={"scope": "all"})
            examples = r.json()["examples"]
            assert len(examples) == 10
            # Preference pairs and the edit SFT targets, interleaved.
            assert {e["kind"] for e in examples} == {"preference", "edit"}

            r = await c.post(
                "/api/writers/training/start",
                json={"scope": "all", "effort": "quick", "overrides": {"rank": 12}},
            )
            assert r.status_code == 200, r.text
            run_id = r.json()["run_id"]
            assert db.get_writer_adapter(1)["hyperparams"]["rank"] == 12
            assert db.get_writer_adapter(1)["name"] == "taste-all-v1"
            r = await c.get("/api/writers/training")
            assert r.json()["next_name"] == "taste-all-v2"
            r = await c.post("/api/writers/training/start", json={})
            assert r.status_code == 409
            assert r.json()["detail"]["code"] == "already_training"
            await _until(
                lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0)
                >= 1
            )
            r = await c.post("/api/writers/training/pause")
            assert r.status_code == 200 and r.json()["state"] == "pausing"
            await _idle(m)
            r = await c.get("/api/writers/training")
            assert [x["run_id"] for x in r.json()["runs"]] == [run_id]
            r = await c.post("/api/writers/training/resume", json={"run_id": run_id})
            assert r.status_code == 200
            r = await c.post("/api/writers/training/cancel", json={})
            assert r.status_code == 200 and r.json()["state"] == "cancelling"
            await _idle(m)
            r = await c.post("/api/writers/training/cancel", json={})
            assert r.status_code == 409
            r = await c.get("/api/writers/training", params={"scope": "999"})
            assert r.status_code == 422
    finally:
        tm.set_training_manager(None)


# ---- the gate -------------------------------------------------------------------------


def _report(vram: Optional[float]) -> HardwareReport:
    return HardwareReport(
        os="Linux",
        machine="x86_64",
        python="3.11",
        is_wsl=False,
        cpu_count=16,
        cuda=(
            None
            if vram is None
            else CudaInfo(name="GPU", vram_gb=vram, capability="12.0")
        ),
    )


def test_gate_reports_each_missing_piece(tmp_path):
    clear_writer_training_probe_cache()
    paths = WriterTrainingPaths(
        python=str(tmp_path / "py"),
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "lc" / "convert_lora_to_gguf.py",
    )
    calls: List[str] = []

    def deps(python):
        calls.append("deps")
        return "training dependencies not installed: peft"

    def trl(python):
        calls.append("trl")
        return "trl 0.27 lacks DPOConfig.ref_adapter_name"

    reasons = writer_training_reasons(
        _report(None), paths, deps_probe=deps, trl_probe=trl
    )
    assert len(reasons) == 4
    assert "CUDA" in reasons[0]
    assert reasons[1] == "training dependencies not installed: peft"
    assert "base weights" in reasons[2] and "converter" in reasons[3]
    assert calls == ["deps"]  # the TRL probe needs the deps

    # 24 GB is not enough (2048 tokens, DPO): 32 GB is the floor.
    clear_writer_training_probe_cache()
    reasons = writer_training_reasons(
        _report(24.0), paths, deps_probe=lambda p: "", trl_probe=trl
    )
    assert reasons[0] == "Requires 32 GB VRAM; detected 24.0 GB."
    assert reasons[1] == "trl 0.27 lacks DPOConfig.ref_adapter_name"

    # A "32 GB" card reports ~31.8 GiB through torch (32607 MiB on an RTX
    # 5090): it must pass the VRAM check.
    clear_writer_training_probe_cache()
    reasons = writer_training_reasons(
        _report(31.8), paths, deps_probe=lambda p: "", trl_probe=trl
    )
    assert not any("VRAM" in r for r in reasons)

    # Everything present.
    clear_writer_training_probe_cache()
    paths.base_dir.mkdir()
    (paths.base_dir / "config.json").write_text("{}")
    (paths.base_dir / "model-00001.safetensors").write_bytes(b"x")
    paths.converter_script.parent.mkdir()
    paths.converter_script.write_text("")
    gate = writer_training_gate(
        _report(32.0), paths, deps_probe=lambda p: "", trl_probe=lambda p: ""
    )
    assert gate.available and gate.reasons == []

    # probe=False never spawns: cached result reused, else "not checked".
    assert writer_training_gate(_report(32.0), paths, probe=False).available
    clear_writer_training_probe_cache()
    gate = writer_training_gate(_report(32.0), paths, probe=False)
    assert gate.reasons == ["Training dependencies not checked yet."]
    clear_writer_training_probe_cache()


def test_feature_gates_include_writer_training_without_probing(monkeypatch):
    from metascan.core import hardware

    def boom(python, timeout=300.0):
        raise AssertionError("feature_gates must not probe")

    monkeypatch.setattr(hardware, "probe_training_deps", boom)
    clear_writer_training_probe_cache()
    gates = hardware.feature_gates(_report(None))
    assert gates["writer_training"].available is False
    assert any("CUDA" in r for r in gates["writer_training"].reasons)


def _stub_python(tmp_path: Path, name: str, body: str) -> str:
    """A fake interpreter: a shell script that ignores ``-c <code>``."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def test_the_deps_probe_runs_in_a_subprocess_with_the_given_python(tmp_path):
    from metascan.core.hardware import probe_training_deps

    missing = _stub_python(
        tmp_path, "missing", """echo '{"missing": ["peft", "trl"]}'"""
    )
    reason = probe_training_deps(missing)
    # Names the interpreter it checked and the exact command for it, so a
    # pip run into another venv is obvious.
    assert reason == (
        f"training dependencies not installed for {missing}: peft, trl "
        f'(run "{missing}" -m pip install -r requirements-train.txt)'
    )
    broken = _stub_python(
        tmp_path, "broken", """echo '{"missing": [], "broken": ["torch (OSError)"]}'"""
    )
    assert probe_training_deps(broken) == (
        "training dependencies fail to import: torch (OSError)"
    )
    ok = _stub_python(tmp_path, "ok", """echo '{"missing": [], "broken": []}'""")
    assert probe_training_deps(ok) == ""
    crash = _stub_python(tmp_path, "crash", "exit 3")
    assert probe_training_deps(crash) == (
        "the training dependency probe failed (exit 3)"
    )
    slow = _stub_python(tmp_path, "slow", "sleep 5")
    assert probe_training_deps(slow, timeout=0.3).startswith("could not run")
    assert "could not run" in probe_training_deps(str(tmp_path / "nonexistent"))


def test_only_a_passing_probe_is_cached_for_good(tmp_path, monkeypatch):
    from metascan.core import hardware

    clear_writer_training_probe_cache()
    paths = WriterTrainingPaths(
        python=str(tmp_path / "py"),
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "conv.py",
    )
    calls: List[str] = []

    def failing(python):
        calls.append("deps")
        return "could not run the training dependency probe: timed out"

    writer_training_reasons(_report(48.0), paths, deps_probe=failing)
    writer_training_reasons(_report(48.0), paths, deps_probe=failing)
    assert calls == ["deps"]  # a failure is cached for PROBE_FAILURE_TTL_S
    monkeypatch.setattr(hardware, "PROBE_FAILURE_TTL_S", 0.0)
    clear_writer_training_probe_cache()
    writer_training_reasons(_report(48.0), paths, deps_probe=failing)
    writer_training_reasons(_report(48.0), paths, deps_probe=failing)
    assert calls == ["deps", "deps", "deps"]  # ... and expires

    def definitive(python):
        calls.append("deps2")
        return "training dependencies not installed: peft"

    # "Not installed" is cached only briefly too: installing the deps while
    # the server runs must not need a restart.
    monkeypatch.setattr(hardware, "PROBE_FAILURE_TTL_S", 60.0)
    clear_writer_training_probe_cache()
    writer_training_reasons(_report(48.0), paths, deps_probe=definitive)
    writer_training_reasons(_report(48.0), paths, deps_probe=definitive)
    assert calls.count("deps2") == 1  # kept within the TTL
    monkeypatch.setattr(hardware, "PROBE_FAILURE_TTL_S", 0.0)
    clear_writer_training_probe_cache()
    writer_training_reasons(_report(48.0), paths, deps_probe=definitive)
    writer_training_reasons(_report(48.0), paths, deps_probe=definitive)
    assert calls.count("deps2") == 3  # ... and expires

    def ok(python):
        calls.append("ok")
        return ""

    clear_writer_training_probe_cache()
    writer_training_reasons(_report(48.0), paths, deps_probe=ok, trl_probe=lambda p: "")
    writer_training_reasons(_report(48.0), paths, deps_probe=ok, trl_probe=lambda p: "")
    assert calls.count("ok") == 1  # a passing answer is kept for good
    clear_writer_training_probe_cache()


async def _anoop() -> None:
    return None


def test_gpu_reserved_fails_closed():
    def broken() -> bool:
        raise RuntimeError("probe broke")

    gpu_guard.set_gpu_reservation(broken)
    assert gpu_guard.gpu_reserved() is True
    gpu_guard.set_gpu_reservation(None)
    assert gpu_guard.gpu_reserved() is False


def test_upscale_queue_holds_pending_tasks_while_locked(tmp_path, monkeypatch):
    from metascan.core.upscale_queue_process import ProcessUpscaleQueue

    q = ProcessUpscaleQueue(tmp_path / "queue")
    started: List[str] = []
    monkeypatch.setattr(q, "_start_task_process", lambda t: started.append(t.id))
    assert not q.has_active_tasks()
    task_id = q.add_task(str(tmp_path / "a.png"), "image")
    assert q.has_active_tasks()  # pending counts

    gpu_guard.set_gpu_reservation(lambda: True)
    q.start_processing()
    assert started == [] and q.get_task(task_id).status.value == "pending"

    gpu_guard.set_gpu_reservation(None)
    q.start_processing()
    assert started == [task_id]


# ---- fix round 1 ------------------------------------------------------------------


async def test_the_second_busy_check_refuses_and_releases(db, tmp_path, dataset):
    rec = Recorder()
    answers = [[], ["t2i_batch"]]
    released: List[int] = []
    m = _manager(db, tmp_path, rec, on_gpu_released=lambda: released.append(1))
    m._gpu_busy = lambda: answers.pop(0)
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert e.value.code == "gpu_busy" and e.value.extra["reasons"] == ["t2i_batch"]
    assert ("release_gpu", None) in rec.log  # the GPU had been freed first
    assert not m.gpu_locked() and released == [1]
    assert db.list_writer_adapters() == [] and db.list_training_runs() == []


async def test_lifespan_busy_reasons_cover_storyboard_index_and_upscale(
    db, tmp_path, monkeypatch
):
    from backend.api import similarity, storyboard, upscale
    from backend.main import _build_training_manager

    m = _build_training_manager(
        db=db,
        data_dir=tmp_path,
        comfy_client=_FakeComfy(),
        t2i_runner=_FakeT2i(),
        inference_client=_FakeUnloadable(),
        vlm_roles=_FakeUnloadable(),
    )
    assert m._gpu_busy() == []

    class _Pending:
        def done(self) -> bool:
            return False

    monkeypatch.setattr(storyboard, "_background_tasks", {_Pending()})
    monkeypatch.setattr(similarity, "is_indexing", lambda: True)
    monkeypatch.setattr(upscale, "upscale_active", lambda: True)
    assert m._gpu_busy() == ["storyboard", "index_build", "upscale_active"]


class _FakeBriefs:
    def __init__(self) -> None:
        self.running = True
        self.cancelled = 0
        self.started = 0

    def cancel(self) -> bool:
        was = self.running
        self.cancelled += 1
        self.running = False
        return was

    async def wait(self) -> None:
        return None

    def start(self) -> None:
        self.started += 1
        self.running = True


async def test_training_cancels_the_briefs_job_and_restarts_it_after(
    db, tmp_path, dataset
):
    from backend.main import _build_training_manager
    from backend.services import hindsight_briefs

    briefs = _FakeBriefs()
    hindsight_briefs.set_briefs_job(briefs)  # type: ignore[arg-type]
    try:
        m = _build_training_manager(
            db=db,
            data_dir=tmp_path / "data",
            comfy_client=_FakeComfy(),
            t2i_runner=_FakeT2i(),
            inference_client=_FakeUnloadable(),
            vlm_roles=_FakeUnloadable(),
        )
        m._dataset_inputs = lambda: (object(), "uncensored", None)
        m._paths = lambda: _paths(tmp_path)
        m._gate = lambda p: Gate(True, True, "", [])
        m._broadcast = lambda e, d: None
        m._refresh_adapters = _anoop
        m.backend, m.fake, m.poll_s = "fake", {"sft_steps": 2, "dpo_steps": 2}, 0.02
        await m.start("all", "quick")
        assert briefs.cancelled == 1 and briefs.started == 0
        await _idle(m)
        await _until(lambda: briefs.started == 1)
        # Nothing was interrupted the second time: no restart.
        briefs.running = False
        await m.start("all", "quick")
        await _idle(m)
        await asyncio.sleep(0.05)
        assert briefs.started == 1
    finally:
        hindsight_briefs.set_briefs_job(None)


async def test_lifespan_installs_the_lock_and_recovers(db, tmp_path, dataset):
    import inspect

    from backend import main
    from backend.services import hindsight_briefs

    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    aid = db.create_writer_adapter("x", "all", "t2i", "qwen3vl-8b", status="training")
    run_id = db.create_training_run(aid, status="running")
    briefs = _FakeBriefs()
    hindsight_briefs.set_briefs_job(briefs)  # type: ignore[arg-type]
    try:
        await main._install_training(m)
        assert tm.get_training_manager() is m
        assert db.get_training_run(run_id)["status"] == "paused"  # recovered
        assert briefs.blocked == m.gpu_locked  # type: ignore[attr-defined]
        m._gpu_held = True
        assert gpu_guard.gpu_reserved()
        m._gpu_held = False
        assert not gpu_guard.gpu_reserved()
        await main._uninstall_training(m)
        assert tm.get_training_manager() is None
        gpu_guard.set_gpu_reservation(lambda: True)
        gpu_guard.set_gpu_reservation(None)
    finally:
        hindsight_briefs.set_briefs_job(None)
    src = inspect.getsource(main.lifespan)
    assert "await _install_training(training_manager)" in src
    assert "await _uninstall_training(training_manager)" in src


async def test_dataset_summary_is_cached_per_scope_and_invalidated(
    db, tmp_path, dataset
):
    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    await m.dataset_summary("all")
    await m.dataset_summary("all")
    assert len(dataset["calls"]) == 1
    await m.start("all", "quick")  # builds once more and invalidates
    n = len(dataset["calls"])
    await m.dataset_summary("all")
    assert len(dataset["calls"]) == n + 1
    await _idle(m)
    await m.dataset_summary("all")  # completion invalidated it too
    assert len(dataset["calls"]) == n + 2


async def test_dataset_summary_is_single_flight_per_scope(db, tmp_path, monkeypatch):
    calls: List[str] = []

    def build(db_, scope, store, now, *, content_mode, resolve=None):
        calls.append(scope)
        import time

        time.sleep(0.2)  # a slow library-wide build
        return _examples(120)

    monkeypatch.setattr(wd, "build_examples", build)
    m = _manager(db, tmp_path, Recorder())
    out = await asyncio.gather(*(m.dataset_summary("all") for _ in range(5)))
    assert calls == ["all"]  # concurrent cold callers awaited one build
    assert all(o == out[0] for o in out)
    await m.dataset_summary("all")  # cached
    assert calls == ["all"]
    # A cancelled waiter does not cancel the shared build for the others.
    m.invalidate_dataset_summary()
    first = asyncio.ensure_future(m.dataset_summary("all"))
    second = asyncio.ensure_future(m.dataset_summary("all"))
    await asyncio.sleep(0.05)
    first.cancel()
    assert (await second)["scope"] == "all"
    assert calls == ["all", "all"]


async def test_dataset_summary_errors_are_not_cached(db, tmp_path, dataset):
    m = _manager(db, tmp_path, Recorder())
    for _ in range(2):
        with pytest.raises(TrainingError) as e:
            await m.dataset_summary("999")
        assert e.value.detail()["code"] == "unknown_scope"
    assert m._summary_inflight == {}  # type: ignore[attr-defined]


async def test_training_status_light_never_builds(db, tmp_path, dataset, monkeypatch):
    from backend.api import writers

    m = _manager(db, tmp_path, Recorder())
    tm.set_training_manager(m)
    monkeypatch.setattr(writers, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(writers.router)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/writers/training", params={"dataset": "0"})
            assert r.status_code == 200
            body = r.json()
            assert dataset["calls"] == []
            assert "dataset" not in body and "next_name" not in body
            assert body["state"] == "idle" and body["gpu_locked"] is False
            assert body["run"] is None and body["runs"] == []
            assert body["gate"]["available"] is True
            # A deleted category is never an error for the light status.
            r = await c.get(
                "/api/writers/training", params={"dataset": "0", "scope": "999"}
            )
            assert r.status_code == 200
            r = await c.get("/api/writers/training")
            assert r.json()["dataset"]["readiness"]["trainable"] is True
            assert len(dataset["calls"]) == 1
    finally:
        tm.set_training_manager(None)


# ---- orphaned workers ----------------------------------------------------------------


def _dummy_worker(marker: bool = True) -> "subprocess.Popen[bytes]":

    args = [sys.executable, "-c", "import time; time.sleep(120)"]
    if marker:
        args.append("metascan.workers.writer_train_worker")
    return subprocess.Popen(args)


def _paused_run(db, tmp_path, status="running", name="x") -> Tuple[int, Path]:
    aid = db.create_writer_adapter(
        f"{name}{status}", "all", "t2i", "qwen3vl-8b", status="training"
    )
    run_dir = tmp_path / "runs" / str(aid)
    run_dir.mkdir(parents=True)
    run_id = db.create_training_run(aid, status=status, run_dir=str(run_dir))
    return run_id, run_dir


async def test_recover_stops_an_orphaned_worker_then_pauses(db, tmp_path):
    proc = _dummy_worker()
    try:
        run_id, run_dir = _paused_run(db, tmp_path)
        tm.write_worker_pid(run_dir, proc.pid)
        assert tm.recorded_worker(run_dir) is not None
        m = _manager(db, tmp_path, Recorder())
        assert await m.recover() == [run_id]
        assert proc.wait(timeout=15) is not None  # it was stopped
        assert db.get_training_run(run_id)["status"] == "paused"
        assert not (run_dir / tm.WORKER_PID_NAME).exists()
    finally:
        if proc.poll() is None:
            proc.kill()


async def test_recover_never_kills_a_process_that_is_not_ours(db, tmp_path):
    import json

    stranger = _dummy_worker(marker=False)
    ours = _dummy_worker()
    try:
        run_id, run_dir = _paused_run(db, tmp_path)
        tm.write_worker_pid(run_dir, stranger.pid)  # no writer_train_worker
        run2, dir2 = _paused_run(db, tmp_path, status="running", name="y")
        tm.write_worker_pid(dir2, ours.pid)
        rec = json.loads((dir2 / tm.WORKER_PID_NAME).read_text())
        rec["create_time"] -= 100  # a reused PID: different start time
        (dir2 / tm.WORKER_PID_NAME).write_text(json.dumps(rec))
        m = _manager(db, tmp_path, Recorder())
        assert sorted(await m.recover()) == sorted([run_id, run2])
        await asyncio.sleep(0.3)
        assert stranger.poll() is None and ours.poll() is None
    finally:
        stranger.kill()
        ours.kill()


async def test_resume_refuses_while_the_recorded_worker_lives(db, tmp_path, dataset):
    proc = _dummy_worker()
    try:
        run_id, run_dir = _paused_run(db, tmp_path, status="paused")
        (run_dir / "task.json").write_text("{}")
        tm.write_worker_pid(run_dir, proc.pid)
        m = _manager(db, tmp_path, Recorder())
        with pytest.raises(TrainingError) as e:
            await m.resume(run_id)
        assert (e.value.status, e.value.code) == (409, "worker_alive")
        assert not m.gpu_locked()
    finally:
        proc.kill()


async def test_deleting_a_paused_adapter_discards_its_run(
    db, tmp_path, dataset, monkeypatch
):
    from backend.api import writers

    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.pause()
    await _idle(m)
    run = db.get_training_run(run_id)
    assert Path(run["run_dir"]).exists()

    sent: List[Any] = []
    monkeypatch.setattr(writers, "get_db", lambda: db)
    monkeypatch.setattr(writers, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(writers.ws_manager, "broadcast_sync", lambda *a: sent.append(a))
    app = FastAPI()
    app.include_router(writers.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.delete(f"/api/writers/adapters/{run['adapter_id']}")
    assert r.json() == {"status": "deleted"}
    assert db.get_training_run(run_id)["status"] == "cancelled"
    assert not Path(run["run_dir"]).exists()
    assert m.resumable_runs() == []
    assert (
        "training",
        "run_cancelled",
        {"run_id": run_id, "adapter_id": run["adapter_id"]},
    ) in sent


async def test_index_build_rechecks_the_lock_right_before_spawning(monkeypatch):
    from fastapi import HTTPException

    from backend.api import similarity

    locked = {"on": False}
    started: List[Any] = []

    class _Db:
        def get_unembedded_file_paths(self):
            locked["on"] = True  # training takes the GPU meanwhile
            return ["/x/a.png"]

    class _Eq:
        def is_indexing(self):
            return False

        def start_indexing(self, *a):
            started.append(a)
            return True

    monkeypatch.setattr(similarity, "get_db", lambda: _Db())
    monkeypatch.setattr(similarity, "load_app_config", lambda: {})
    monkeypatch.setattr(similarity, "_get_embedding_queue", lambda: _Eq())
    gpu_guard.set_gpu_reservation(lambda: locked["on"])
    with pytest.raises(HTTPException) as e:
        await similarity._start_index_build(rebuild=False, allow_vlm_tagging=False)
    assert e.value.detail["code"] == "gpu_busy_training"
    assert started == []


# ---- fix round 2: the background gate probe ------------------------------------------


class _Probe:
    """A controllable async probe: counts calls, waits for ``release``."""

    def __init__(self, result=("", "")) -> None:
        self.calls = 0
        self.release = asyncio.Event()
        self.result = result
        self.cancelled = 0

    async def __call__(self, python: str) -> Tuple[str, str]:
        self.calls += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return self.result


def _probing_manager(db, tmp_path, probe, **kw) -> TrainingManager:
    clear_writer_training_probe_cache()
    m = _manager(db, tmp_path, Recorder(), **kw)
    m._gate = None  # the real gate with the background probe
    m._probe = probe
    m._paths = lambda: WriterTrainingPaths(
        python=str(tmp_path / "train-python"),
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "conv.py",
    )
    return m


async def test_a_finished_probe_broadcasts_gate_changed(db, tmp_path, dataset):
    # The Training tab only shows cached probe results: tell it when one lands.
    probe = _Probe(result=("training dependencies not installed: peft", ""))
    rec = Recorder()
    clear_writer_training_probe_cache()
    m = _manager(db, tmp_path, rec)
    m._gate = None
    m._probe = probe
    m._paths = lambda: WriterTrainingPaths(
        python=str(tmp_path / "train-python"),
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "conv.py",
    )
    await m.gate_status()
    assert rec.events("gate_changed") == []
    probe.release.set()
    await _until(lambda: not m.probing())
    assert rec.events("gate_changed") == [{}]
    clear_writer_training_probe_cache()


async def test_gate_probe_runs_in_the_background_single_flight(db, tmp_path, dataset):
    probe = _Probe(result=("training dependencies not installed: peft", ""))
    m = _probing_manager(db, tmp_path, probe)
    results = await asyncio.wait_for(
        asyncio.gather(*[m.gate_status() for _ in range(5)]), timeout=2.0
    )
    assert probe.calls == 1  # one probe for five concurrent requests
    for gate, checking in results:
        assert checking and not gate.available
        assert "Checking training dependencies…" in gate.reasons
        assert any("base weights" in r for r in gate.reasons)  # other pieces
    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick")
    assert (e.value.status, e.value.code) == (409, "gate_checking")
    assert not m.gpu_locked()

    probe.release.set()
    await _until(lambda: not m.probing())
    gate, checking = await m.gate_status()
    assert not checking and probe.calls == 1
    assert "training dependencies not installed: peft" in gate.reasons
    clear_writer_training_probe_cache()


async def test_get_training_never_waits_for_the_probe(
    db, tmp_path, dataset, monkeypatch
):
    from backend.api import writers

    probe = _Probe()
    m = _probing_manager(db, tmp_path, probe)
    tm.set_training_manager(m)
    monkeypatch.setattr(writers, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(writers.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            rs = await asyncio.wait_for(
                asyncio.gather(*[c.get("/api/writers/training") for _ in range(4)]),
                timeout=5.0,
            )
            assert probe.calls == 1
            for r in rs:
                assert r.status_code == 200
                assert r.json()["gate"]["checking"] is True
            r = await c.post("/api/writers/training/start", json={})
            assert r.json()["detail"]["code"] == "gate_checking"
            probe.release.set()
            await _until(lambda: not m.probing())
            r = await c.get("/api/writers/training")
            assert r.json()["gate"]["checking"] is False
            # recheck clears the cache and starts a fresh background probe
            probe.release = asyncio.Event()
            r = await c.get("/api/writers/training", params={"recheck": 1})
            assert r.json()["gate"]["checking"] is True and probe.calls == 2
            probe.release.set()
            await _until(lambda: not m.probing())
    finally:
        tm.set_training_manager(None)
        clear_writer_training_probe_cache()


async def test_probe_failures_are_negative_cached_and_recheck_restarts(
    db, tmp_path, monkeypatch
):
    from metascan.core import hardware

    probe = _Probe(result=("could not run the training dependency probe: x", ""))
    probe.release.set()
    m = _probing_manager(db, tmp_path, probe)
    await m.gate_status()
    await _until(lambda: not m.probing())
    gate, checking = await m.gate_status()
    assert not checking and probe.calls == 1  # the failure is cached (60 s)
    assert any(r.startswith("could not run") for r in gate.reasons)

    monkeypatch.setattr(hardware, "PROBE_FAILURE_TTL_S", 0.0)
    await m.gate_status(recheck=True)
    await _until(lambda: not m.probing())
    _, checking = await m.gate_status()  # expired at once: probes again
    assert checking and probe.calls == 3
    await _until(lambda: not m.probing())

    # A recheck during a probe cancels it and starts a fresh one.
    slow = _Probe()
    m._probe = slow
    await m.gate_status(recheck=True)
    await m.gate_status(recheck=True)
    await asyncio.sleep(0.01)
    assert slow.calls == 2 and slow.cancelled == 1
    slow.release.set()
    await _until(lambda: not m.probing())
    await m.aclose()
    clear_writer_training_probe_cache()


async def test_the_async_probe_never_uses_a_thread(tmp_path, monkeypatch):
    def no_threads(*a, **k):
        raise AssertionError("the probe must not use the default executor")

    monkeypatch.setattr(asyncio, "to_thread", no_threads)
    missing = _stub_python(tmp_path, "m", """echo '{"missing": ["peft"]}'""")
    deps, trl = await tm.probe_training_python(missing)
    assert deps.startswith("training dependencies not installed for ") and trl == ""
    assert ": peft" in deps
    ok = _stub_python(
        tmp_path,
        "ok",
        """echo '{"missing": [], "broken": [], "installed": true, "version": "0.25.1"}'""",
    )
    assert await tm.probe_training_python(ok) == ("", "")
    slow = _stub_python(tmp_path, "slow", "sleep 5")
    deps, _ = await tm.probe_training_python(slow, timeout=0.3)
    assert deps == "could not run the training dependency probe: timed out after 0s"
    deps, _ = await tm.probe_training_python(str(tmp_path / "nope"))
    assert deps.startswith("could not run the training dependency probe")


# ---- fix round 2: shutdown, races, unverifiable workers, mapping ------------------------


async def test_no_briefs_restart_while_closing(db, tmp_path, dataset):
    from backend.main import _build_training_manager
    from backend.services import hindsight_briefs

    briefs = _FakeBriefs()
    hindsight_briefs.set_briefs_job(briefs)  # type: ignore[arg-type]
    try:
        m = _build_training_manager(
            db=db,
            data_dir=tmp_path / "data",
            comfy_client=_FakeComfy(),
            t2i_runner=_FakeT2i(),
            inference_client=_FakeUnloadable(),
            vlm_roles=_FakeUnloadable(),
        )
        m._dataset_inputs = lambda: (object(), "uncensored", None)
        m._paths = lambda: _paths(tmp_path)
        m._gate = lambda p: Gate(True, True, "", [])
        m._broadcast = lambda e, d: None
        m._refresh_adapters = _anoop
        m.backend, m.fake, m.poll_s = "fake", SLOW, 0.02
        await m.start("all", "quick")
        assert briefs.cancelled == 1
        await m.aclose()  # pauses the run while closing
        await asyncio.sleep(0.05)
        assert briefs.started == 0
    finally:
        hindsight_briefs.set_briefs_job(None)


async def test_resume_rechecks_the_run_after_taking_the_gpu(db, tmp_path, dataset):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.pause()
    await _idle(m)
    adapter_id = db.get_training_run(run_id)["adapter_id"]
    orig = m._take_gpu

    async def take_then_discard():
        await orig()
        db.soft_delete_writer_adapter(adapter_id)  # a delete lands meanwhile
        tm.discard_open_runs(db, adapter_id)

    m._take_gpu = take_then_discard  # type: ignore[method-assign]
    with pytest.raises(TrainingError) as e:
        await m.resume(run_id)
    assert (e.value.status, e.value.code) == (409, "run_discarded")
    assert not m.gpu_locked() and m.status()["state"] == "idle"


async def test_an_adapter_delete_waits_for_the_op_lock(db, tmp_path, monkeypatch):
    from backend.api import writers

    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    tm.set_training_manager(m)
    aid = db.create_writer_adapter("p", "all", "t2i", "qwen3vl-8b", status="paused")
    monkeypatch.setattr(writers, "get_db", lambda: db)
    monkeypatch.setattr(writers, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(writers.ws_manager, "broadcast_sync", lambda *a: None)
    app = FastAPI()
    app.include_router(writers.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            async with m.op_lock:  # e.g. a resume in progress
                task = asyncio.create_task(c.delete(f"/api/writers/adapters/{aid}"))
                await asyncio.sleep(0.2)
                assert not task.done()
                assert db.get_writer_adapter(aid)["deleted_at"] is None
            r = await task
        assert r.json() == {"status": "deleted"}
    finally:
        tm.set_training_manager(None)


async def test_an_unverifiable_worker_is_never_killed_and_blocks_resume(
    db, tmp_path, dataset, monkeypatch
):
    import psutil

    proc = _dummy_worker()
    try:
        run_id, run_dir = _paused_run(db, tmp_path, status="running")
        (run_dir / "task.json").write_text("{}")
        tm.write_worker_pid(run_dir, proc.pid)
        real = psutil.Process.cmdline

        def denied(self):
            if self.pid == proc.pid:
                raise psutil.AccessDenied(self.pid)
            return real(self)

        monkeypatch.setattr(psutil.Process, "cmdline", denied)
        assert tm.worker_state(run_dir) == (tm.WORKER_UNVERIFIABLE, proc.pid)
        assert tm.recorded_worker(run_dir) is None
        assert not tm.stop_orphan_worker(run_dir)
        m = _manager(db, tmp_path, Recorder())
        assert await m.recover() == [run_id]  # paused, worker untouched
        await asyncio.sleep(0.3)
        assert proc.poll() is None
        with pytest.raises(TrainingError) as e:
            await m.resume(run_id)
        assert (e.value.status, e.value.code) == (409, "worker_unverifiable")
        assert e.value.extra["pid"] == proc.pid
        assert f"PID {proc.pid}" in e.value.extra["message"]
    finally:
        proc.kill()


async def test_a_mid_submit_gpu_refusal_maps_to_409(monkeypatch):
    from backend.api import comfy as comfy_api
    from backend.api import t2i as t2i_api
    from metascan.core.comfy_client import GpuBusyError

    err = t2i_api._http_error(GpuBusyError())
    assert err is not None and err.status_code == 409
    assert err.detail["code"] == "gpu_busy_training"

    async def submit(*a, **k):
        raise GpuBusyError()

    monkeypatch.setattr(
        comfy_api, "_require_client", lambda: SimpleNamespace(submit=submit)
    )
    app = FastAPI()
    app.include_router(comfy_api.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/api/comfy/submit",
            json={
                "preset_id": 1,
                "positive": "p",
                "seed": 1,
                "width": 64,
                "height": 64,
            },
        )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gpu_busy_training"


# ---- final-review fixes ------------------------------------------------------------


async def test_a_continued_run_resumes_after_its_source_adapter_is_deleted(
    db, tmp_path, dataset
):
    # "Continue from" adapter A, pause, delete A, resume: the run must not
    # depend on A's folder (its PEFT dir is copied into the run at start).
    import json

    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    await m.start("all", "quick")
    await _idle(m)
    source = db.list_writer_adapters()[0]["id"]
    m.fake = dict(m.fake, **SLOW)
    run_id = await m.start("all", "quick", start_from=source)
    run_dir = Path(db.get_training_run(run_id)["run_dir"])
    task = json.loads((run_dir / "task.json").read_text())
    init = Path(task["init_adapter_dir"])
    assert run_dir in init.parents
    assert (init / "adapter_config.json").is_file()
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 2
    )
    await m.pause()
    await _idle(m)

    import shutil

    shutil.rmtree(adapter_dir(tmp_path / "data", source))
    db.soft_delete_writer_adapter(source)
    _speed_up(db, run_id)
    await m.resume(run_id)
    await _idle(m)
    run = db.get_training_run(run_id)
    assert run["status"] == "completed", run
    child = db.get_writer_adapter(run["adapter_id"])
    meta = json.loads(
        (adapter_dir(tmp_path / "data", child["id"]) / "meta.json").read_text()
    )
    assert meta["init_adapter_lora"] is not None


async def test_completion_refreshes_the_writer_before_dropping_the_lock(
    db, tmp_path, dataset
):
    # The GPU release restarts an interrupted briefs job, which may spawn
    # the shared writer: it must already have the new --lora list.
    order: List[Tuple[str, bool]] = []
    rec = Recorder()
    m = _manager(db, tmp_path, rec)

    async def refresh() -> None:
        order.append(("refresh", m.gpu_locked()))

    m._refresh_adapters = refresh
    m.on_gpu_released = lambda: order.append(("released", m.gpu_locked()))
    await m.start("all", "quick")
    await _idle(m)
    assert db.list_writer_adapters()[0]["status"] == "untested"
    assert [name for name, _ in order] == ["refresh", "released"]
    assert order[0] == ("refresh", True)
    names = [e for e, _ in rec.log]
    assert "adapters_changed" in names[names.index("run_started") :]


async def test_a_start_that_fails_after_spawning_stops_the_worker(
    db, tmp_path, dataset, monkeypatch
):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    spawned: List[Any] = []
    orig_exec = asyncio.create_subprocess_exec

    async def exec_spy(*a: Any, **kw: Any) -> Any:
        proc = await orig_exec(*a, **kw)
        spawned.append(proc)
        return proc

    def boom(run_dir: Path, pid: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tm.asyncio, "create_subprocess_exec", exec_spy)
    monkeypatch.setattr(tm, "write_worker_pid", boom)
    with pytest.raises(OSError):
        await m.start("all", "quick")
    assert len(spawned) == 1
    # Terminated and reaped before the run is marked failed / lock released.
    assert spawned[0].returncode is not None
    assert not m.gpu_locked()
    (run,) = db.list_training_runs()
    assert run["status"] == "failed"


async def test_a_run_completing_while_closing_skips_the_scorecard_hook(
    db, tmp_path, dataset
):
    calls: List[Tuple[int, bool]] = []

    async def hook(adapter_id: int, render: bool) -> None:
        calls.append((adapter_id, render))

    rec = Recorder()
    m = _manager(db, tmp_path, rec, on_completed=hook)
    await m.start("all", "quick", render_scorecard=True)
    m._closing = True  # the server began shutting down meanwhile
    await _idle(m)
    assert db.list_writer_adapters()[0]["status"] == "untested"
    assert calls == []


def test_lifespan_recovers_training_before_preloading_models():
    # An orphaned trainer (a server that died hard) holds the GPU: it must
    # be stopped by recover() before CLIP / VLM preloads spawn anything.
    import inspect

    from backend import main

    src = inspect.getsource(main.lifespan)
    install = src.index("await _install_training(training_manager)")
    for spawn in (
        "asyncio.create_task(_preload())",
        "asyncio.create_task(_preload_faiss())",
        "asyncio.create_task(_preload_vlm())",
        "similarity.init_embedding_queue()",
    ):
        assert install < src.index(spawn), spawn
    # Shutdown order is unchanged: training first, then the scorecard.
    tail = src[src.index("yield") :]
    assert tail.index("await _uninstall_training(training_manager)") < tail.index(
        "await scorecard_service.aclose()"
    )
