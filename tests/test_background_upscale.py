"""The Upscaling and Moving originals processes behind the registry."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.services.background.generation import UpscaleProcess
from backend.services.background.originals_move import OriginalsMoveProcess
from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry
from metascan.utils.path_utils import to_native_path, to_posix_path


class Stub:
    holds: tuple = ()
    order = 1

    def __init__(self, pid: str) -> None:
        self.id = self.label = pid

    def capabilities(self) -> Capabilities:
        return Capabilities(True, True, True)

    def report(self) -> ProcessReport:
        return ProcessReport(State.IDLE, None, "")

    async def start(self) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...


class Runner:
    def __init__(self) -> None:
        self.batches: List[Dict[str, Any]] = []
        self.counts = (0, 2, "rendering")
        self.uploading = False
        self.cancelled: List[str] = []
        self.unloaded = 0
        self.log: List[str] = []

    def active_batches(self) -> List[Dict[str, Any]]:
        return list(self.batches)

    def progress_counts(self) -> tuple:
        return self.counts

    def is_writing(self) -> bool:
        return self.uploading

    async def cancel_batch(self, batch_id: str) -> bool:
        self.log.append("cancel_batch")
        self.cancelled.append(batch_id)
        self.batches.clear()
        return True

    async def unload_vlms(self) -> None:
        self.unloaded += 1


class Comfy:
    def __init__(self, log: List[str]) -> None:
        self.paused = False
        self.in_flight = 0
        self.gated = False
        self.jobs = 0
        self.log = log
        self.cancelled_sources: List[str] = []

    def set_source_paused(self, source: str, paused: bool) -> None:
        assert source == "upscale"
        self.paused = paused
        self.log.append(f"gate {'closed' if paused else 'open'}")

    def source_in_flight(self, source: str) -> int:
        return self.in_flight

    def source_job_count(self, source: str) -> int:
        return self.jobs

    def has_gated_jobs(self, source: str) -> bool:
        return self.gated

    async def cancel_source(self, source: str) -> int:
        self.log.append("cancel_source")
        self.cancelled_sources.append(source)
        return self.jobs


@pytest.fixture
async def up(tmp_path):
    reg = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    reg.register(Stub("tagging"))
    reg.register(Stub("embedding"))
    runner = Runner()
    comfy = Comfy(runner.log)
    proc = UpscaleProcess(reg, runner, comfy, wind_down_poll_s=0.01)
    reg.register(proc)
    yield proc, reg, runner, comfy
    await proc.aclose()
    await reg.aclose()


async def until(pred: Any, timeout: float = 2.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


def test_identity():
    assert UpscaleProcess.id == "upscale" and UpscaleProcess.label == "Upscaling"
    assert UpscaleProcess.holds == ("tagging", "embedding")


async def test_idle_and_running_report_counts_only(up):
    proc, reg, runner, _ = up
    assert reg.status("upscale").state == State.IDLE
    runner.batches = [{"batch_id": "b"}]
    st = reg.status("upscale")
    assert st.state == State.RUNNING
    assert st.activity == "File 1 of 2, rendering"
    assert (st.progress.done, st.progress.total, st.progress.unit) == (0, 2, "files")


async def test_running_holds_tagging_and_embedding(up):
    proc, reg, runner, _ = up
    runner.batches = [{"batch_id": "b"}]
    proc.poke()
    await until(lambda: reg.status("tagging").state == State.HELD)
    assert reg.status("embedding").state == State.HELD


async def test_pause_gates_queued_jobs_and_winds_down_with_a_job_in_flight(up):
    proc, reg, runner, comfy = up
    runner.batches = [{"batch_id": "b"}]
    comfy.in_flight = 1
    st = await reg.command("upscale", "pause")
    assert comfy.paused is True
    assert st.state == State.RUNNING
    assert reg.status("upscale").activity == "Pausing after the current job"
    comfy.in_flight = 0
    await until(lambda: reg.status("upscale").state == State.PAUSED)


async def test_a_pause_waits_out_an_upload(up):
    proc, reg, runner, comfy = up
    runner.batches = [{"batch_id": "b"}]
    runner.uploading = True
    await reg.command("upscale", "pause")
    assert reg.status("upscale").state == State.RUNNING
    runner.uploading = False
    await until(lambda: reg.status("upscale").state == State.PAUSED)


async def test_resume_unloads_the_vlms_then_opens_the_gate(up):
    proc, reg, runner, comfy = up
    runner.batches = [{"batch_id": "b"}]
    await reg.command("upscale", "pause")
    comfy.gated = True
    st = await reg.command("upscale", "resume")
    assert comfy.paused is False and runner.unloaded == 1
    assert st.state == State.RUNNING


async def test_stop_cancels_jobs_before_the_gate_opens(up):
    proc, reg, runner, comfy = up
    runner.batches = [{"batch_id": "b"}]
    await reg.command("upscale", "pause")
    await reg.command("upscale", "stop")
    assert runner.log.index("cancel_source") < runner.log.index("gate open")
    assert runner.cancelled == ["b"]
    assert reg.status("upscale").state == State.STOPPED


async def test_stop_reaches_jobs_with_no_run(up):
    proc, reg, runner, comfy = up
    comfy.jobs = 2  # rehydrated after a restart: no in-memory run
    await reg.command("upscale", "stop")
    assert comfy.cancelled_sources == ["upscale"]


async def test_play_after_a_stop_restarts_nothing(up):
    proc, reg, runner, comfy = up
    await reg.command("upscale", "stop")
    st = await reg.command("upscale", "start")
    assert st.state == State.IDLE and runner.batches == []


async def test_a_new_run_clears_a_stop(up):
    proc, reg, *_ = up
    await reg.command("upscale", "stop")
    await proc.owner_batch_starting()
    assert reg.status("upscale").state != State.STOPPED


async def test_a_new_run_while_paused_stays_paused(up):
    proc, reg, runner, comfy = up
    await reg.command("upscale", "pause")
    await until(lambda: reg.status("upscale").state == State.PAUSED)
    await proc.owner_batch_starting()
    runner.batches = [{"batch_id": "b"}]
    assert reg.status("upscale").state == State.PAUSED
    assert comfy.paused is True


async def test_a_rehydrated_job_shows_as_running(up):
    proc, reg, runner, comfy = up
    comfy.jobs = 3
    st = reg.status("upscale")
    assert st.state == State.RUNNING and st.activity == "Rendering 3 jobs"


async def test_a_persisted_pause_applies_on_register(tmp_path):
    state = tmp_path / "bg.json"
    first = ProcessRegistry(state, lambda st: None)
    first.register(Stub("tagging"))
    first.register(Stub("embedding"))
    runner = Runner()
    comfy = Comfy(runner.log)
    p1 = UpscaleProcess(first, runner, comfy)
    first.register(p1)
    await first.command("upscale", "pause")
    await p1.aclose()
    await first.aclose()

    second = ProcessRegistry(state, lambda st: None)
    second.register(Stub("tagging"))
    second.register(Stub("embedding"))
    runner2 = Runner()
    comfy2 = Comfy(runner2.log)
    p2 = UpscaleProcess(second, runner2, comfy2, wind_down_poll_s=0.01)
    second.register(p2)
    await until(lambda: second.status("upscale").state == State.PAUSED)
    await until(lambda: comfy2.paused)
    await p2.aclose()
    await second.aclose()


async def test_a_persisted_stop_survives_a_restart(tmp_path):
    state = tmp_path / "bg.json"
    first = ProcessRegistry(state, lambda st: None)
    first.register(Stub("tagging"))
    first.register(Stub("embedding"))
    runner = Runner()
    p1 = UpscaleProcess(first, runner, Comfy(runner.log))
    first.register(p1)
    await first.command("upscale", "stop")
    await p1.aclose()
    await first.aclose()

    second = ProcessRegistry(state, lambda st: None)
    second.register(Stub("tagging"))
    second.register(Stub("embedding"))
    runner2 = Runner()
    p2 = UpscaleProcess(second, runner2, Comfy(runner2.log))
    second.register(p2)
    assert second.status("upscale").state == State.STOPPED
    await p2.aclose()
    await second.aclose()


# ---- Moving originals -----------------------------------------------------------


class Db:
    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self.records = records

    def list_upscale_records(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.records]

    def repoint_upscale_original(self, file_path: str, original_path: str) -> None:
        for r in self.records:
            if r["file_path"] == file_path:
                r["original_path"] = original_path


class Move:
    def __init__(self, tmp: Path, files: int = 3) -> None:
        self.tmp = tmp
        self.old = tmp / "old"
        self.old.mkdir()
        self.new = tmp / "new"
        records = []
        for i in range(files):
            path = self.old / f"{i}.png"
            path.write_bytes(b"x" * (i + 1))
            records.append(
                {"file_path": f"/lib/{i}.png", "original_path": to_posix_path(path)}
            )
        self.db = Db(records)
        self.saved: List[str] = []
        self.reg = ProcessRegistry(tmp / "bg.json", lambda st: None)
        self.proc = self.make()
        self.reg.register(self.proc)

    def make(self) -> OriginalsMoveProcess:
        return OriginalsMoveProcess(
            self.reg, self.db, self.tmp / "data", self.saved.append
        )

    def done(self) -> bool:
        return not self.proc._running()


@pytest.fixture
async def move(tmp_path):
    m = Move(tmp_path)
    yield m
    await m.proc.aclose()
    await m.reg.aclose()


async def test_a_move_copies_repoints_and_saves_the_setting_last(move):
    await move.proc.begin(str(move.new))
    await until(move.done)
    assert sorted(p.name for p in move.new.iterdir()) == ["0.png", "1.png", "2.png"]
    assert list(move.old.iterdir()) == []
    for record in move.db.records:
        assert Path(to_native_path(record["original_path"])).parent == move.new
    assert move.saved == [str(move.new)]
    assert move.proc.active() is False
    assert move.reg.status("originals_move").state == State.IDLE


async def test_pause_lands_between_files_and_resume_finishes(move):
    move.proc._gate.clear()  # so the pause lands before the first file
    await move.proc.begin(str(move.new))
    await move.reg.command("originals_move", "pause")
    await asyncio.sleep(0.05)
    assert move.saved == [] and move.proc.active() is True
    await move.reg.command("originals_move", "resume")
    await until(move.done)
    assert move.saved == [str(move.new)]


async def test_stop_leaves_the_setting_alone_and_every_file_findable(move):
    move.proc._gate.clear()
    await move.proc.begin(str(move.new))
    await move.reg.command("originals_move", "stop")
    await until(move.done)
    assert move.saved == [] and move.proc.active() is False
    for record in move.db.records:
        assert Path(to_native_path(record["original_path"])).is_file()


async def test_a_failed_file_stops_the_move_and_play_continues(move, monkeypatch):
    real = move.proc._move_one
    calls = {"n": 0}

    def flaky(record: Dict[str, Any], target: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(record, target)

    monkeypatch.setattr(move.proc, "_move_one", flaky)
    await move.proc.begin(str(move.new))
    await until(move.done)
    assert move.saved == []
    st = move.reg.status("originals_move")
    assert st.state == State.FAILED and st.error == "OSError"
    for record in move.db.records:
        assert Path(to_native_path(record["original_path"])).is_file()

    await move.reg.command("originals_move", "start")
    await until(move.done)
    assert move.saved == [str(move.new)]


async def test_a_move_left_by_the_last_run_continues(move):
    data = move.tmp / "data"
    data.mkdir()
    (data / "originals_move.json").write_text(json.dumps({"target": str(move.new)}))
    move.proc.resume_at_startup()
    await until(move.done)
    assert move.saved == [str(move.new)]


async def test_a_persisted_pause_is_not_overridden_at_startup(move):
    data = move.tmp / "data"
    data.mkdir()
    (data / "originals_move.json").write_text(json.dumps({"target": str(move.new)}))
    await move.reg.command("originals_move", "pause")
    move.proc.resume_at_startup()
    await asyncio.sleep(0.05)
    assert move.saved == [] and move.proc._running() is False
    assert move.reg.status("originals_move").state == State.PAUSED


async def test_activity_is_counts_never_a_path(move):
    move.proc._gate.clear()
    await move.proc.begin(str(move.new))
    await until(lambda: move.proc._total == 3)
    assert str(move.tmp) not in move.reg.status("originals_move").activity
