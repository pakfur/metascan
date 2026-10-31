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
from backend.services.background.upscale import RESTART_NOTE, UpscaleProcess
from backend.services.taste_service import TasteService, TrainConflict
from backend.services.training_manager import TrainingError, TrainingManager
from metascan.core.upscale_queue_process import ProcessUpscaleQueue


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


# ---- Upscaling ----------------------------------------------------------------


def utask(tid, status, progress=0.0):
    return SimpleNamespace(
        id=tid, status=SimpleNamespace(value=status), progress=progress
    )


class FakeQueue:
    def __init__(self):
        self.tasks: List[Any] = []
        self.gated = False
        self.on_task_updated = self.on_task_added = self.on_task_removed = None
        self.on_queue_changed = None
        self.started = 0
        self.cancelled: List[str] = []

    def get_all_tasks(self):
        return list(self.tasks)

    def start_processing(self):
        self.started += 1

    def cancel_task(self, tid):
        self.cancelled.append(tid)
        for t in self.tasks:
            if t.id == tid:
                t.status.value = "cancelled"
        return True


@pytest.fixture
async def up(tmp_path):
    q = FakeQueue()
    reg = registry_for(tmp_path)
    calls = []

    async def pause():
        calls.append("pause")
        n = 0
        for t in q.tasks:
            if t.status.value in ("pending", "processing"):
                t.status.value, t.progress = "paused", 0
                n += 1
        return n

    async def resume():
        calls.append("resume")
        n = 0
        for t in q.tasks:
            if t.status.value == "paused":
                t.status.value = "pending"
                n += 1
        return n

    proc = UpscaleProcess(reg, lambda: q, pause, resume, clock=lambda: 0.0)
    proc._cache = None
    reg.register(proc)
    # the fake clock never advances: drop the cache before each read
    orig = proc._tasks
    proc._tasks = lambda: (setattr(proc, "_cache", None), orig())[1]
    yield proc, reg, q, calls
    await reg.aclose()


async def test_upscale_reports_counts_only(up):
    proc, reg, q, _ = up
    assert reg.status("upscale").state == State.IDLE
    q.tasks = [
        utask("a", "completed"),
        utask("b", "processing", 40),
        utask("c", "pending"),
    ]
    st = reg.status("upscale")
    assert st.state == State.RUNNING
    assert st.activity == "File 2 of 3, 40%"
    assert (st.progress.done, st.progress.total) == (1, 3)


async def test_upscale_pause_restarts_current_file_and_resume_continues(up):
    proc, reg, q, calls = up
    q.tasks = [utask("b", "processing", 40), utask("c", "pending")]
    st = await reg.command("upscale", "pause")
    assert st.state == State.PAUSED and st.activity == RESTART_NOTE
    assert q.gated and calls == ["pause"] and q.tasks[0].progress == 0
    st = await reg.command("upscale", "resume")
    assert st.state == State.RUNNING and not q.gated
    assert calls == ["pause", "resume"] and q.started >= 1


async def test_upscale_stop_cancels_open_tasks_and_owner_add_clears_it(up):
    proc, reg, q, _ = up
    q.tasks = [utask("a", "completed"), utask("b", "processing"), utask("c", "paused")]
    st = await reg.command("upscale", "stop")
    assert st.state == State.STOPPED and q.cancelled == ["b", "c"]
    await proc.owner_task_adding()
    assert reg.status("upscale").state != State.STOPPED


async def test_upscale_task_added_while_paused_is_paused_on_arrival(up):
    proc, reg, q, calls = up
    await reg.command("upscale", "pause")
    q.tasks = [utask("n", "pending")]
    await proc.task_added()
    assert q.tasks[0].status.value == "paused"
    assert reg.status("upscale").state == State.PAUSED


async def test_upscale_holds_tagging_and_embedding_while_running(up):
    proc, reg, q, _ = up
    q.tasks = [utask("b", "processing", 10)]
    assert reg.status("tagging").state == State.HELD
    assert reg.status("embedding").held_by == ["Upscaling"]


async def test_upscale_persisted_pause_gates_before_register(tmp_path):
    first = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    first.register(Stub("tagging"))
    first.register(Stub("embedding"))
    q = FakeQueue()

    async def noop():
        return 0

    p1 = UpscaleProcess(first, lambda: q, noop, noop)
    first.register(p1)
    await first.command("upscale", "pause")
    await first.aclose()
    second = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    q2 = FakeQueue()
    calls = []

    async def pause():
        calls.append("pause")
        return 0

    p2 = UpscaleProcess(second, lambda: q2, pause, noop)
    assert q2.gated  # before the poller could claim anything
    second.register(p2)
    await second.settle()
    assert calls == ["pause"] and second.status("upscale").state == State.PAUSED
    await second.aclose()


def test_a_gated_queue_claims_nothing(tmp_path):
    q = ProcessUpscaleQueue(tmp_path / "q")
    q.add_task(
        str(tmp_path / "x.png"),
        "image",
        2,
        False,
        False,
        False,
        2,
        "general",
        None,
        True,
    )
    q.gated = True
    assert q.claim_next_pending() is None
    q.gated = False
    assert q.claim_next_pending() is not None


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


async def test_training_activity_never_carries_examples(tr):
    proc, reg, mgr, db, job, card = tr
    mgr.run, mgr.state = a_run(stage="SECRET prompt"), "running"
    assert "SECRET" not in reg.status("training").activity


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
        hold_embedding=hold,
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
        await m._take_gpu()
    assert exc.value.extra["reasons"] == ["index_build", "comfy_jobs"]
