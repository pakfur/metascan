"""The Upscaling, Taste and Writer training processes."""

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, List

import pytest

from backend.services.background.protocol import Capabilities, ProcessReport, State
from backend.services.background.registry import ProcessRegistry
from backend.services.background.taste import TasteProcess
from backend.services.background.training import WriterTrainingProcess
from backend.services.taste_service import TasteService, TrainConflict
from backend.services.training_manager import TrainingError, TrainingManager


class Stub:
    holds: tuple = ()
    order = 1

    def __init__(self, pid):
        self.id = self.label = pid

    def capabilities(self):
        return Capabilities(True, True, True)

    def report(self):
        return ProcessReport(State.IDLE, None, "")

    async def start(self): ...
    async def pause(self): ...
    async def resume(self): ...
    async def stop(self): ...


async def until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


def registry_for(tmp_path):
    reg = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    reg.register(Stub("tagging"))
    reg.register(Stub("embedding"))
    return reg


# ---- Taste --------------------------------------------------------------------


class Runner:
    def __init__(self):
        self.training = False
        self.release = asyncio.Event()

    async def train(self, half_life):
        self.training = True
        try:
            await self.release.wait()
            return {"status": "ok", "model_id": 1}
        finally:
            self.training = False


def taste_service(runner=None):
    svc = TasteService(None, runner or Runner(), lambda: {}, broadcast=lambda *a: None)
    svc._missing = lambda: None  # labels suffice
    return svc


async def test_taste_report_states(tmp_path):
    reg = registry_for(tmp_path)
    svc = taste_service()
    svc.set_reembed_status(lambda: {"running": True, "done": 2, "total": 9})
    proc = TasteProcess(reg, svc, poll_s=60)
    reg.register(proc)
    st = reg.status("taste")
    assert (
        st.state == State.RUNNING
        and st.activity == "Re-embedding 2 of 9 deleted images"
    )
    assert (st.progress.done, st.progress.total) == (2, 9)
    svc.set_reembed_status(None)
    assert reg.status("taste").state == State.IDLE
    svc._last_error = "boom"
    assert reg.status("taste").state == State.FAILED
    await proc.aclose()
    await reg.aclose()


async def test_paused_taste_does_not_auto_retrain_but_owner_train_works(tmp_path):
    reg = registry_for(tmp_path)
    runner = Runner()
    svc = taste_service(runner)
    proc = TasteProcess(reg, svc, poll_s=60)
    reg.register(proc)
    await reg.command("taste", "pause")
    assert svc.auto_paused
    assert await svc.maybe_retrain() is False
    await svc.start_training()  # the owner's POST /train
    await until(lambda: svc.is_training)
    st = reg.status("taste")  # the run finishes: running, winding down
    assert st.state == State.RUNNING and "pausing" in st.activity
    runner.release.set()
    await svc.wait_idle()
    assert reg.status("taste").state == State.PAUSED
    await reg.command("taste", "resume")
    assert not svc.auto_paused
    await proc.aclose()
    await reg.aclose()


async def test_stop_cancels_a_running_train_and_a_train_clears_the_stop(tmp_path):
    reg = registry_for(tmp_path)
    runner = Runner()
    svc = taste_service(runner)
    proc = TasteProcess(reg, svc, poll_s=60)
    reg.register(proc)
    await svc.start_training()
    await until(lambda: svc.is_training)
    st = await reg.command("taste", "stop")
    assert st.state == State.STOPPED and not svc.is_training and svc.auto_paused
    assert await svc.maybe_retrain() is False
    await proc.owner_train_starting()
    assert reg.status("taste").state != State.STOPPED and not svc.auto_paused
    with pytest.raises(TrainConflict):
        runner.training = True
        await svc.start_training()
    await proc.aclose()
    await reg.aclose()


async def test_persisted_taste_pause_applies_at_construction(tmp_path):
    first = registry_for(tmp_path)
    svc = taste_service()
    p = TasteProcess(first, svc, poll_s=60)
    first.register(p)
    await first.command("taste", "pause")
    await p.aclose()
    await first.aclose()
    second = registry_for(tmp_path)
    svc2 = taste_service()
    p2 = TasteProcess(second, svc2, poll_s=60)
    assert svc2.auto_paused
    await p2.aclose()
    await second.aclose()


# ---- Writer training ----------------------------------------------------------


class FakeManager:
    def __init__(self):
        self.run = None
        self.state = "idle"
        self.log = []

    def status(self):
        return {"state": self.state, "run": self.run}

    async def pause(self):
        if self.run is None:
            raise TrainingError(409, "not_running")
        self.log.append("pause")
        self.state = "pausing"
        self.run["state"] = "pausing"

    async def cancel(self, run_id=None):
        self.log.append(("cancel", run_id))
        self.run = None
        self.state = "idle"

    async def resume(self, run_id):
        self.log.append(("resume", run_id))
        self.run = {"progress": {"step": 3, "total_steps": 10, "stage": "sft"}}
        self.state = "running"


class FakeDb:
    def __init__(self):
        self.paused: List[dict] = []

    def list_training_runs(self, statuses):
        return list(self.paused)


class Job:
    def __init__(self):
        self.st = {"running": False, "done": 0, "total": 0}
        self.cancelled = False

    def status(self):
        return dict(self.st)

    def cancel(self):
        self.cancelled = True
        self.st["running"] = False
        return True


@pytest.fixture
async def tr(tmp_path):
    reg = registry_for(tmp_path)
    mgr, db, job = FakeManager(), FakeDb(), Job()
    card = {"v": None}
    proc = WriterTrainingProcess(
        reg, lambda: mgr, lambda: db, lambda: job, lambda: card["v"], poll_s=60
    )
    reg.register(proc)
    yield proc, reg, mgr, db, job, card
    await proc.aclose()
    await reg.aclose()


def a_run(step=3, total=10, stage="sft", state="running"):
    return {
        "state": state,
        "progress": {"step": step, "total_steps": total, "stage": stage},
    }


async def test_training_run_reports_counts_holds_and_confirm_stop(tr):
    proc, reg, mgr, *_ = tr
    mgr.run, mgr.state = a_run(), "running"
    st = reg.status("training")
    assert st.state == State.RUNNING and st.activity == "SFT step 4 of 10"
    assert (st.progress.done, st.progress.total) == (3, 10)
    assert st.capabilities.confirm_stop and not st.capabilities.can_start
    assert reg.status("tagging").held_by == ["Writer training"]
    assert reg.status("embedding").held_by == ["Writer training"]


async def test_pause_winds_down_until_the_manager_reports_paused(tr):
    proc, reg, mgr, db, *_ = tr
    mgr.run, mgr.state = a_run(), "running"
    st = await reg.command("training", "pause")
    assert mgr.log == ["pause"]
    assert st.state == State.RUNNING and st.activity == "Pausing at the next step"
    # the worker exited: the run is paused in the database
    mgr.run, mgr.state = None, "idle"
    db.paused = [{"id": 7, "step": 4, "total_steps": 10}]
    await proc.refresh()
    st = reg.status("training")
    assert st.state == State.PAUSED and st.activity == "Paused at step 4 of 10"
    assert reg.status("tagging").state == State.IDLE  # holds are gone
    st = await reg.command("training", "resume")
    assert mgr.log[-1] == ("resume", 7) and st.state == State.RUNNING


async def test_a_run_paused_by_a_crash_shows_paused_and_play_resumes_it(tr):
    proc, reg, mgr, db, *_ = tr
    db.paused = [{"id": 5, "step": 1, "total_steps": 4}]
    await proc.refresh()
    st = reg.status("training")
    assert st.state == State.PAUSED and st.capabilities.can_start
    await reg.command("training", "start")
    assert mgr.log == [("resume", 5)]


async def test_stop_cancels_the_run_else_briefs_else_scorecard(tr):
    proc, reg, mgr, db, job, card = tr
    mgr.run, mgr.state = a_run(), "running"
    await reg.command("training", "stop")
    assert mgr.log == [("cancel", None)] and not job.cancelled

    await reg.command("training", "start")  # clear the stop
    job.st = {"running": True, "done": 2, "total": 5}
    st = reg.status("training")
    assert st.activity == "Writing briefs, 2 of 5"
    await proc.stop()
    assert job.cancelled and mgr.log == [("cancel", None)]

    cancelled = []

    class Svc:
        async def cancel(self):
            cancelled.append(1)
            card["v"] = None

    card["v"] = {"phase": "rendering", "done": 1, "total": 4}
    assert "Scorecard rendering, 1 of 4" == reg.status("training").activity
    proc._get_scorecard_service = lambda: Svc()
    await proc.stop()
    assert cancelled == [1]


async def test_stop_cancels_a_paused_run_by_id(tr):
    proc, reg, mgr, db, *_ = tr
    db.paused = [{"id": 9, "step": 1, "total_steps": 4}]
    await proc.refresh()
    await proc.stop()
    assert mgr.log == [("cancel", 9)]


async def test_a_failed_run_sticks_until_the_next(tr):
    proc, reg, mgr, *_ = tr
    proc.record_event("run_failed", {"run_id": 1, "error": "Ran out of GPU memory"})
    st = reg.status("training")
    assert st.state == State.FAILED and st.error == "Ran out of GPU memory"
    mgr.run, mgr.state = a_run(), "running"
    proc.record_event("run_started", {"run_id": 2})
    assert reg.status("training").state == State.RUNNING
    mgr.run, mgr.state = None, "idle"
    assert reg.status("training").state == State.IDLE


async def test_pause_during_briefs_or_scorecard_is_refused(tr):
    from backend.services.background.registry import CommandRefused

    proc, reg, mgr, db, job, card = tr
    job.st = {"running": True, "done": 1, "total": 5}
    assert not reg.status("training").capabilities.can_pause
    with pytest.raises(CommandRefused) as exc:
        await reg.command("training", "pause")
    assert exc.value.code == "not_pausable"
    assert reg.intent("training") == "none"
    st = reg.status("training")
    assert st.state == State.RUNNING and mgr.log == []
    card["v"], job.st["running"] = {"phase": "rendering", "done": 0, "total": 2}, False
    assert not reg.status("training").capabilities.can_pause
    mgr.run, mgr.state = a_run(), "running"  # a real run can be paused
    assert reg.status("training").capabilities.can_pause


async def test_stopped_training_with_nothing_to_resume_can_be_started(tr):
    proc, reg, mgr, *_ = tr
    await reg.command("training", "stop")
    st = reg.status("training")
    assert st.state == State.STOPPED and st.capabilities.can_start
    st = await reg.command("training", "start")
    assert st.state == State.IDLE and mgr.log == []


async def test_training_activity_never_carries_examples(tr):
    proc, reg, mgr, db, job, card = tr
    mgr.run, mgr.state = a_run(stage="SECRET prompt"), "running"
    assert "SECRET" not in reg.status("training").activity


async def test_owner_run_starting_clears_a_pause(tr):
    proc, reg, mgr, db, *_ = tr
    mgr.run, mgr.state = a_run(), "running"
    await reg.command("training", "pause")
    mgr.run, mgr.state = None, "idle"
    db.paused = []
    assert reg.intent("training") == "paused"
    await proc.owner_run_starting()
    assert reg.intent("training") == "none"
    assert mgr.log == ["pause"]  # nothing resumed: the owner's own run starts


async def test_play_on_a_failed_run_clears_the_error(tr):
    proc, reg, mgr, *_ = tr
    proc.record_event("run_failed", {"run_id": 1, "error": "boom"})
    st = reg.status("training")
    assert st.state == State.FAILED and st.capabilities.can_start
    st = await reg.command("training", "start")
    assert st.state == State.IDLE and st.error is None and mgr.log == []


async def test_a_raising_signature_does_not_end_the_polls(tmp_path):
    reg = registry_for(tmp_path)
    svc = taste_service()
    proc = TasteProcess(reg, svc, poll_s=0.01)
    reg.register(proc)
    calls = {"n": 0}

    def sig():
        calls["n"] += 1
        raise RuntimeError("no")

    proc._signature = sig
    await until(lambda: calls["n"] >= 3)
    assert not proc._task.done()
    await proc.aclose()
    await reg.aclose()


async def test_a_raising_training_signature_does_not_end_the_polls(tmp_path):
    reg = registry_for(tmp_path)
    proc = WriterTrainingProcess(
        reg, FakeManager, FakeDb, Job, lambda: None, poll_s=0.01
    )
    reg.register(proc)
    calls = {"n": 0}

    def sig():
        calls["n"] += 1
        raise RuntimeError("no")

    proc._signature = sig
    await until(lambda: calls["n"] >= 3)
    assert not proc._task.done()
    await proc.aclose()
    await reg.aclose()


# ---- the GPU lock waits for the embedding hold ---------------------------------


def make_manager(tmp_path, busy, hold, wait_s):
    async def noop():
        return None

    return TrainingManager(
        None,
        tmp_path / "data",
        dataset_inputs=lambda: (object(), "uncensored", None),
        gpu_busy=busy,
        release_gpu=noop,
        refresh_adapters=noop,
        broadcast=lambda e, d: None,
        paths=lambda: None,
        gate=lambda p: None,
        backend="fake",
        hold_background=hold,
        index_wait_s=wait_s,
    )


async def test_index_build_stops_being_a_refusal_once_the_hold_lands(tmp_path):
    state = {"held": False, "polls": 0}

    @contextlib.contextmanager
    def hold():
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    def busy():
        if state["held"]:
            state["polls"] += 1
            return ["index_build"] if state["polls"] < 3 else []
        return ["index_build"]

    m = make_manager(tmp_path, busy, hold, 5.0)
    with m._background_hold():
        await m._take_gpu()
    assert m.gpu_locked() and not state["held"]  # the run's own hold takes over


async def test_index_build_that_outlasts_the_wait_is_refused_as_before(tmp_path):
    @contextlib.contextmanager
    def hold():
        yield

    m = make_manager(tmp_path, lambda: ["index_build"], hold, 0.3)
    with pytest.raises(TrainingError) as exc:
        await m._take_gpu()
    assert exc.value.code == "gpu_busy" and exc.value.extra["reasons"] == [
        "index_build"
    ]
    assert not m.gpu_locked()


async def test_another_busy_reason_is_refused_at_once(tmp_path):
    entered = []

    @contextlib.contextmanager
    def hold():
        entered.append(1)
        yield

    m = make_manager(tmp_path, lambda: ["index_build", "comfy_jobs"], hold, 30.0)
    with pytest.raises(TrainingError) as exc:
        with m._background_hold():
            await m._take_gpu()
    assert exc.value.extra["reasons"] == ["index_build", "comfy_jobs"]
