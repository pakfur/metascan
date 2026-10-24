"""Writer training profiles, server side: the bounded history the training
manager keeps at ``<data_dir>/logs/writer_training_profiles.json`` (one
entry per attempt, newest last, capped at 50, atomic rewrite), the
``GET /api/writers/training/profiles`` endpoint and the VRAM-cap rehearsal
knob. Runs spawn the real worker subprocess on the FAKE backend."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi import FastAPI

from backend.services import training_manager as tm
from backend.services import training_profiles as tp
from backend.services.training_manager import TrainingError
from metascan.core import writer_dataset as wd
from tests.test_training_manager import (
    SLOW,
    Recorder,
    _idle,
    _manager,
    _speed_up,
    _until,
)
from tests.test_training_manager import dataset, db  # noqa: F401 (fixtures)

SENTINEL = "SENTINEL-periwinkle-anvil"


def _history(tmp_path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = json.loads(
        (tmp_path / "data" / "logs" / tp.HISTORY_NAME).read_text()
    )
    return out


# ---- the history file ------------------------------------------------------------


def test_history_keeps_the_newest_fifty(tmp_path):
    data_dir = tmp_path / "data"
    for i in range(60):
        tp.append_profile(
            data_dir,
            {"schema": 1, "attempt": {"run_id": i}, "timeline": {"sft": [1]}},
            adapter_id=i,
            adapter_name=f"taste-all-v{i}",
        )
    h = json.loads((data_dir / "logs" / tp.HISTORY_NAME).read_text())
    assert h["schema"] == 1 and h["max_entries"] == tp.MAX_ENTRIES == 50
    assert [p["attempt"]["run_id"] for p in h["profiles"]] == list(range(10, 60))
    assert all("timeline" not in p for p in h["profiles"])
    assert h["profiles"][-1]["adapter_id"] == 59
    assert h["profiles"][-1]["adapter_name"] == "taste-all-v59"
    assert not [f for f in (data_dir / "logs").iterdir() if f.name.endswith(".tmp")]


def test_a_corrupt_history_is_quarantined_and_started_fresh(tmp_path, caplog):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / tp.HISTORY_NAME).write_text("{not json")
    with caplog.at_level(logging.WARNING):
        tp.append_profile(
            tmp_path / "data",
            {"attempt": {"run_id": 1}},
            adapter_id=1,
            adapter_name="a",
        )
    h = _history(tmp_path)
    assert [p["attempt"]["run_id"] for p in h["profiles"]] == [1]
    quarantined = [f for f in logs.iterdir() if ".corrupt-" in f.name]
    assert len(quarantined) == 1 and quarantined[0].read_text() == "{not json"
    assert any("corrupt" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "content", ['{"schema": 1, "profiles": "nope"}', "[1, 2]", '{"schema": 1}']
)
def test_a_wrong_shape_history_is_quarantined(tmp_path, content):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / tp.HISTORY_NAME).write_text(content)
    assert tp.read_history(tmp_path / "data")["profiles"] == []
    assert [f for f in logs.iterdir() if ".corrupt-" in f.name]


def test_a_missing_history_reads_empty(tmp_path):
    h = tp.read_history(tmp_path / "data")
    assert h == {"schema": 1, "max_entries": 50, "profiles": []}


def test_last_profile_round_trip(tmp_path):
    data_dir = tmp_path / "data"
    assert tp.read_last_profile(data_dir) is None
    tp.save_last_profile(data_dir, {"schema": 1, "timeline": {"sft": [{"step": 1}]}})
    assert tp.read_last_profile(data_dir)["timeline"] == {"sft": [{"step": 1}]}


# ---- the manager records every attempt ---------------------------------------------


async def test_a_completed_run_is_recorded(db, tmp_path, dataset):  # noqa: F811
    rec = Recorder()
    m = _manager(db, tmp_path, rec)
    run_id = await m.start("all", "quick")
    await _idle(m)
    h = _history(tmp_path)
    assert len(h["profiles"]) == 1
    entry = h["profiles"][0]
    adapter = db.list_writer_adapters()[0]
    assert entry["adapter_id"] == adapter["id"]
    assert entry["adapter_name"] == "taste-all-v1"
    assert entry["attempt"]["run_id"] == run_id
    assert entry["attempt"]["outcome"] == "completed"
    assert entry["config"]["preset"] == "quick"
    assert "timeline" not in entry
    # The run dir is gone (completion cleans it); the full profile survives.
    last = tp.read_last_profile(tmp_path / "data")
    assert last is not None and last["timeline"]["sft"]


async def test_pause_resume_fail_and_cancel_each_add_an_entry(
    db, tmp_path, dataset  # noqa: F811
):
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.pause()
    await _idle(m)
    _speed_up(db, run_id)
    await m.resume(run_id)
    await _idle(m)
    outcomes = [p["attempt"]["outcome"] for p in _history(tmp_path)["profiles"]]
    assert outcomes == ["paused", "completed"]
    resumed = _history(tmp_path)["profiles"][1]["attempt"]
    assert resumed["resumed"] is True

    run2 = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.cancel()
    await _idle(m)
    assert db.get_training_run(run2)["status"] == "cancelled"
    entry = _history(tmp_path)["profiles"][-1]
    assert entry["attempt"]["outcome"] == "cancelled"
    assert entry["attempt"]["run_id"] == run2


async def test_a_failed_and_an_oom_run_are_recorded(
    db, tmp_path, dataset
):  # noqa: F811
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake={"fail_at": {"stage": "dpo", "step": 2}})
    await m.start("all", "quick")
    await _idle(m)
    m2 = _manager(
        db,
        tmp_path,
        rec,
        fake={"oom_at": {"stage": "sft", "step": 2, "batch_tokens": 99}},
    )
    run_id = await m2.start("all", "quick")
    await _idle(m2)
    run = db.get_training_run(run_id)
    assert run["status"] == "failed"
    assert run["error"].startswith("Ran out of GPU memory during sft at step 2")
    assert db.get_writer_adapter(run["adapter_id"])["error"] == run["error"]
    entries = _history(tmp_path)["profiles"]
    assert [p["attempt"]["outcome"] for p in entries] == ["failed", "oom"]
    assert entries[-1]["oom"]["batch_tokens"] == 99


def test_a_profile_without_an_outcome_gets_the_managers_verdict(tmp_path):
    """A worker terminated on shutdown never wrote its exit: the manager
    fills the outcome it decided on."""
    out = tmp_path / "run" / "out"
    out.mkdir(parents=True)
    (out / "profile.json").write_text(
        json.dumps({"schema": 1, "attempt": {"run_id": 3, "outcome": None}})
    )
    active = tm._Active(
        run_id=3,
        adapter_id=9,
        name="taste-all-v2",
        scope="all",
        run_dir=tmp_path / "run",
        resumed=False,
    )
    tp.record_attempt(tmp_path / "data", active, verdict="paused", exit_code=-15)
    entry = _history(tmp_path)["profiles"][0]
    assert entry["attempt"]["outcome"] == "paused"
    assert entry["attempt"]["exit_code"] == -15
    assert entry["attempt"]["ended_by"] == "server"


def test_a_missing_or_corrupt_profile_is_skipped(tmp_path):
    active = tm._Active(
        run_id=3,
        adapter_id=9,
        name="x",
        scope="all",
        run_dir=tmp_path / "run",
        resumed=False,
    )
    tp.record_attempt(tmp_path / "data", active, verdict="failed", exit_code=1)
    (tmp_path / "run" / "out").mkdir(parents=True)
    (tmp_path / "run" / "out" / "profile.json").write_text("{bad")
    tp.record_attempt(tmp_path / "data", active, verdict="failed", exit_code=1)
    assert tp.read_history(tmp_path / "data")["profiles"] == []


async def test_no_example_text_reaches_the_history(
    db, tmp_path, monkeypatch
):  # noqa: F811
    def build(db, scope, store, now, *, content_mode, resolve=None):
        prefs = [
            wd.PrefExample(
                system=f"{SENTINEL} system",
                user=f"{SENTINEL} u{i}",
                chosen=f"{SENTINEL} chosen {i}",
                rejected=f"{SENTINEL} r{i}",
                target_model="krea2",
                categories=[],
                source="variant",
                group=f"krea2|g{i % 20}",
            )
            for i in range(120)
        ]
        counts = wd.DatasetCounts(
            pref_pairs=120, edit_pairs=0, hindsight=0, content_mode="uncensored"
        )
        return [], prefs, counts

    monkeypatch.setattr(wd, "build_examples", build)
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake={"oom_at": {"stage": "dpo", "step": 3}})
    await m.start("all", "quick")
    await _idle(m)
    m2 = _manager(db, tmp_path, rec)
    await m2.start("all", "quick")
    await _idle(m2)
    logs = tmp_path / "data" / "logs"
    blob = "".join(f.read_text() for f in logs.iterdir())
    assert SENTINEL not in blob
    assert len(_history(tmp_path)["profiles"]) == 2


# ---- the endpoint --------------------------------------------------------------------


async def test_profiles_endpoint(db, tmp_path, dataset, monkeypatch):  # noqa: F811
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
            r = await c.get("/api/writers/training/profiles")
            assert r.status_code == 200
            assert r.json() == {
                "schema": 1,
                "max_entries": 50,
                "profiles": [],
                "current": None,
            }
            run_id = await m.start("all", "quick")
            await _until(
                lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0)
                >= 1
            )
            r = await c.get("/api/writers/training/profiles")
            current = r.json()["current"]
            assert current["attempt"]["run_id"] == run_id
            assert current["attempt"]["outcome"] is None  # still running
            await m.pause()
            await _idle(m)
            _speed_up(db, run_id)
            await m.resume(run_id)
            await _idle(m)
            body = (await c.get("/api/writers/training/profiles")).json()
            outcomes = [p["attempt"]["outcome"] for p in body["profiles"]]
            assert outcomes == ["completed", "paused"]  # newest first
            assert body["current"]["attempt"]["outcome"] == "completed"
            assert body["current"]["timeline"]["dpo"]
            assert all("timeline" not in p for p in body["profiles"])
            # The light status says which cap the config sets.
            st = (await c.get("/api/writers/training", params={"dataset": 0})).json()
            assert st["vram_cap"] == {
                "default": None,
                "choices": [16, 20, 24, 28],
                "min": 8,
            }
    finally:
        tm.set_training_manager(None)


# ---- the VRAM cap rehearsal knob --------------------------------------------------------


def test_resolve_vram_cap():
    assert tm.resolve_vram_cap(None, None) is None
    assert tm.resolve_vram_cap(None, 24) == 24.0  # the config default
    assert tm.resolve_vram_cap(0, 24) is None  # "Off" beats the config
    assert tm.resolve_vram_cap(20, 24) == 20.0
    assert tm.resolve_vram_cap(8, None) == 8.0
    for bad in (7.5, -3, "24", True, float("nan"), 2000):
        with pytest.raises(TrainingError) as e:
            tm.resolve_vram_cap(bad, None)
        assert (e.value.status, e.value.code) == (422, "invalid_vram_cap")
    # A bad config value is ignored, never fatal.
    assert tm.resolve_vram_cap(None, "lots") is None
    assert tm.resolve_vram_cap(None, 4) is None


async def test_the_cap_reaches_the_worker(db, tmp_path, dataset):  # noqa: F811
    rec = Recorder()
    m = _manager(db, tmp_path, rec, vram_cap=lambda: 20)
    await m.start("all", "quick")
    await _idle(m)
    assert _history(tmp_path)["profiles"][-1]["config"]["vram_cap_gb"] == 20.0

    await m.start("all", "quick", vram_cap_gb=24)
    await _idle(m)
    entry = _history(tmp_path)["profiles"][-1]
    assert entry["config"]["vram_cap_gb"] == 24.0
    assert entry["config"]["vram_cap_applied"] is True

    await m.start("all", "quick", vram_cap_gb=0)
    await _idle(m)
    assert _history(tmp_path)["profiles"][-1]["config"]["vram_cap_gb"] is None

    with pytest.raises(TrainingError) as e:
        await m.start("all", "quick", vram_cap_gb=4)
    assert e.value.code == "invalid_vram_cap"
    assert not m.gpu_locked()


async def test_start_route_takes_the_cap(
    db, tmp_path, dataset, monkeypatch
):  # noqa: F811
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
            r = await c.post(
                "/api/writers/training/start",
                json={"scope": "all", "effort": "quick", "vram_cap_gb": 5},
            )
            assert r.status_code == 422
            assert r.json()["detail"]["code"] == "invalid_vram_cap"
            r = await c.post(
                "/api/writers/training/start",
                json={"scope": "all", "effort": "quick", "vram_cap_gb": 16},
            )
            assert r.status_code == 200, r.text
            run_dir = Path(db.get_training_run(r.json()["run_id"])["run_dir"])
            task = json.loads((run_dir / "task.json").read_text())
            assert task["vram_cap_gb"] == 16.0
            await m.cancel()
            await _idle(m)
    finally:
        tm.set_training_manager(None)


# ---- fix round 1: attempts nobody recorded, unreadable history, cap warnings -----------


async def test_an_attempt_cut_by_a_hard_stop_is_recorded_at_restart(
    db, tmp_path, dataset  # noqa: F811
):
    """The server died with the worker mid-run: its supervisor never
    recorded the attempt. ``recover`` records it (from the stale
    profile.json) before the resume overwrites that file."""
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 2
    )
    run_dir = Path(db.get_training_run(run_id)["run_dir"])
    await _until(lambda: tp.run_profile_path(run_dir).exists())
    active = m._active
    assert active is not None and active.proc is not None
    active.supervisor.cancel()
    active.proc.kill()
    await active.proc.wait()
    assert not (tmp_path / "data" / "logs" / tp.HISTORY_NAME).exists()

    m2 = _manager(db, tmp_path, rec, fake={"step_sleep_s": 0.0})
    assert await m2.recover() == [run_id]
    entries = _history(tmp_path)["profiles"]
    assert len(entries) == 1
    attempt = entries[0]["attempt"]
    assert attempt["run_id"] == run_id
    assert attempt["outcome"] == "paused"
    assert attempt["exit_code"] == -1
    assert attempt["ended_by"] == "server_restart"
    assert entries[0]["adapter_id"] == db.get_training_run(run_id)["adapter_id"]
    assert entries[0]["adapter_name"] == "taste-all-v1"

    _speed_up(db, run_id)
    await m2.resume(run_id)
    await _idle(m2)
    outcomes = [p["attempt"]["outcome"] for p in _history(tmp_path)["profiles"]]
    assert outcomes == ["paused", "completed"]


async def test_a_resume_whose_worker_dies_early_is_not_recorded_twice(
    db, tmp_path, dataset, monkeypatch  # noqa: F811
):
    """The previous attempt's profile.json is moved aside before a spawn:
    a worker that dies before writing its own leaves nothing to record,
    instead of the previous attempt being recorded again."""
    rec = Recorder()
    m = _manager(db, tmp_path, rec, fake=SLOW)
    run_id = await m.start("all", "quick")
    await _until(
        lambda: (m.status()["run"] or {}).get("progress", {}).get("step", 0) >= 1
    )
    await m.pause()
    await _idle(m)
    assert len(_history(tmp_path)["profiles"]) == 1

    import sys

    monkeypatch.setattr(
        m, "_command", lambda active: [sys.executable, "-c", "raise SystemExit(7)"]
    )
    await m.resume(run_id)
    await _idle(m)
    assert db.get_training_run(run_id)["status"] == "failed"
    assert [p["attempt"]["outcome"] for p in _history(tmp_path)["profiles"]] == [
        "paused"
    ]


def test_an_unreadable_history_is_never_replaced(tmp_path, caplog, monkeypatch):
    data_dir = tmp_path / "data"
    tp.append_profile(
        data_dir,
        {"schema": 1, "attempt": {"run_id": 1}},
        adapter_id=1,
        adapter_name="a",
    )
    path = data_dir / "logs" / tp.HISTORY_NAME
    before = path.read_bytes()
    real_read_text = Path.read_text

    def locked(self: Path, *a: Any, **kw: Any) -> str:
        if self == path:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", locked)
    with caplog.at_level(logging.WARNING, logger=tp.__name__):
        tp.append_profile(
            data_dir,
            {"schema": 1, "attempt": {"run_id": 2}},
            adapter_id=1,
            adapter_name="a",
        )
    monkeypatch.setattr(Path, "read_text", real_read_text)
    survivors = [path] + list(path.parent.glob(f"{tp.HISTORY_NAME}.corrupt-*"))
    assert any(p.exists() and p.read_bytes() == before for p in survivors)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    # Reading for the endpoint still never raises.
    monkeypatch.setattr(Path, "read_text", locked)
    assert tp.read_history(data_dir)["profiles"] == []


def test_a_bad_config_cap_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger=tm.__name__):
        for _ in range(3):
            assert tm.config_vram_cap("warn-once-value") is None
            assert tm.config_vram_cap(5.25) is None
    warnings = [r for r in caplog.records if "writer_train_vram_cap_gb" in r.message]
    assert len(warnings) == 2


def test_an_attempt_is_recorded_once(tmp_path):
    out = tmp_path / "run" / "out"
    out.mkdir(parents=True)
    (out / "profile.json").write_text(
        json.dumps({"schema": 1, "attempt": {"run_id": 3, "attempt_id": "a1"}})
    )
    active = tm._Active(
        run_id=3,
        adapter_id=9,
        name="x",
        scope="all",
        run_dir=tmp_path / "run",
        resumed=False,
    )
    tp.record_attempt(tmp_path / "data", active, verdict="paused", exit_code=-15)
    tp.record_attempt(tmp_path / "data", active, verdict="paused", exit_code=-1)
    assert len(_history(tmp_path)["profiles"]) == 1
    tp.set_aside_run_profile(tmp_path / "run")
    assert not (out / "profile.json").exists()
    assert (out / tp.PREV_PROFILE_NAME).exists()
    tp.set_aside_run_profile(tmp_path / "run")  # nothing to move: no error
