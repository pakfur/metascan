"""The Embedding process: cancel-path pause and hold, deferred states, owner rules."""

import asyncio

import pytest
from fastapi import HTTPException

import backend.services.background.embedding as embedding_mod
from backend import dependencies
from backend.services.auto_index import AutoIndexScheduler
from backend.services.background.embedding import EmbeddingProcess
from backend.services.background.protocol import Progress, State
from backend.services.background.registry import ProcessRegistry


class Queue:
    """is_indexing stays true until ``finish()``: the worker exits after its save."""

    def __init__(self):
        self.indexing = False
        self.cancels = 0

    def is_indexing(self):
        return self.indexing

    def cancel_indexing(self):
        self.cancels += 1

    def finish(self):
        self.indexing = False


class Db:
    def __init__(self):
        self.unembedded = ["a.png"]

    def get_unembedded_file_paths(self):
        return list(self.unembedded)


@pytest.fixture
async def env(monkeypatch, tmp_path):
    monkeypatch.setattr(embedding_mod, "gpu_reserved", lambda: False)
    queue, db = Queue(), Db()
    builds: list = []
    progress: dict = {}
    fail: dict = {"exc": None}

    async def start_build(rebuild, allow_vlm):
        if fail["exc"] is not None:
            raise fail["exc"]
        builds.append((rebuild, allow_vlm))
        queue.indexing = True
        return {"status": "started", "total": 1}

    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    proc = EmbeddingProcess(
        registry,
        lambda: queue,
        lambda: db,
        start_build,
        lambda: progress,
        retry_s=0.05,
        wind_down_poll_s=0.01,
    )
    registry.register(proc)
    dependencies.set_registry(registry)
    yield proc, registry, queue, db, builds, progress, fail
    dependencies.set_registry(None)
    await proc.aclose()
    await registry.aclose()


async def until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


def state(registry):
    return registry.status("embedding").state


async def test_idle_with_declared_shape(env):
    proc, registry, *_ = env
    st = registry.status("embedding")
    assert (st.state, st.label, st.order) == (State.IDLE, "Embedding", 20)
    assert proc.holds == ("tagging",)
    assert st.capabilities.can_stop


async def test_running_reports_progress_in_files(env):
    proc, registry, queue, db, builds, progress, _ = env
    await proc.kick(True)
    progress.update(current=3, total=10)
    st = registry.status("embedding")
    assert st.state == State.RUNNING
    assert st.progress == Progress(3, 10, "files")
    assert "3 of 10" in st.activity


async def test_pause_cancels_and_saves_before_paused(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(True)
    await registry.command("embedding", "pause")
    assert queue.cancels == 1
    # The worker has not exited: still running, and says so.
    st = registry.status("embedding")
    assert st.state == State.RUNNING
    assert st.activity == "Saving, then pausing"
    queue.finish()
    await until(lambda: state(registry) == State.PAUSED)


async def test_resume_starts_an_incremental_build_keeping_the_vlm_flag(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.request_build(True, True, owner=True)  # a rebuild with VLM tags
    await registry.command("embedding", "pause")
    queue.finish()
    await until(lambda: state(registry) == State.PAUSED)
    await registry.command("embedding", "resume")
    assert builds == [(True, True), (False, True)]


async def test_resume_with_nothing_left_stays_idle(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(False)
    await registry.command("embedding", "pause")
    queue.finish()
    await until(lambda: state(registry) == State.PAUSED)
    db.unembedded = []
    await registry.command("embedding", "resume")
    assert builds == [(False, False)]
    assert state(registry) == State.IDLE


async def test_resume_before_the_worker_exits_waits_for_it(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(False)
    await registry.command("embedding", "pause")
    await registry.command("embedding", "resume")
    assert builds == [(False, False)]  # nothing yet: still exiting
    queue.finish()
    await until(lambda: len(builds) == 2)


async def test_hold_while_indexing_cancels_and_release_rebuilds(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(False)
    with registry.hold(["embedding"], by="Text to image"):
        await registry.settle()
        assert queue.cancels == 1
        queue.finish()
        await until(lambda: state(registry) == State.HELD)
    await registry.settle()
    await until(lambda: len(builds) == 2)
    assert builds[1] == (False, False)


async def test_hold_while_idle_release_stays_idle(env):
    proc, registry, queue, db, builds, *_ = env
    with registry.hold(["embedding"], by="Text to image"):
        await registry.settle()
        assert state(registry) == State.HELD
    await registry.settle()
    assert builds == []
    assert state(registry) == State.IDLE


async def test_kick_does_nothing_while_held_paused_or_stopped(env):
    proc, registry, queue, db, builds, *_ = env
    with registry.hold(["embedding"], by="x"):
        assert (await proc.kick(False))["status"] == "blocked"
    await registry.command("embedding", "pause")
    assert (await proc.kick(False))["status"] == "blocked"
    await registry.command("embedding", "resume")
    await registry.command("embedding", "stop")
    assert state(registry) == State.STOPPED
    assert (await proc.kick(False))["status"] == "blocked"
    assert builds == []


async def test_stop_cancels_and_stays_stopped(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(False)
    await registry.command("embedding", "stop")
    assert queue.cancels == 1
    queue.finish()
    await asyncio.sleep(0.05)
    assert state(registry) == State.STOPPED
    assert len(builds) == 1


async def test_owner_build_clears_a_stop(env):
    proc, registry, queue, db, builds, *_ = env
    await registry.command("embedding", "stop")
    res = await proc.request_build(True, True, owner=True)
    assert res["status"] == "started"
    assert builds == [(True, True)]
    assert state(registry) == State.RUNNING


async def test_owner_build_while_paused_clears_the_pause(env):
    proc, registry, queue, db, builds, *_ = env
    await registry.command("embedding", "pause")
    await proc.request_build(False, True, owner=True)
    assert builds == [(False, True)]
    assert state(registry) == State.RUNNING


async def test_owner_build_while_held_queues_until_release(env):
    proc, registry, queue, db, builds, *_ = env
    with registry.hold(["embedding"], by="x"):
        await registry.settle()
        res = await proc.request_build(True, True, owner=True)
        assert res == {"status": "queued"}
        assert builds == []
    await registry.settle()
    await until(lambda: builds == [(True, True)])


async def test_gpu_reserved_is_waiting_not_an_error(env, monkeypatch):
    proc, registry, queue, db, builds, *_ = env
    monkeypatch.setattr(embedding_mod, "gpu_reserved", lambda: True)
    res = await proc.kick(False)
    assert res["status"] == "waiting"
    st = registry.status("embedding")
    assert (st.state, st.error) == (State.WAITING, None)
    monkeypatch.setattr(embedding_mod, "gpu_reserved", lambda: False)
    await until(lambda: builds == [(False, False)])


async def test_failed_run_keeps_error(env):
    proc, registry, queue, db, builds, *_ = env
    await proc.kick(False)
    queue.finish()
    proc.record_error("worker died")
    st = registry.status("embedding")
    assert st.state == State.FAILED
    assert st.error == "worker died"
    await proc.kick(False)  # a new run clears it once it is running
    assert state(registry) == State.RUNNING


async def test_owner_build_conflict_still_raises(env):
    proc, registry, queue, db, builds, progress, fail = env
    fail["exc"] = HTTPException(409, "Index build already in progress")
    with pytest.raises(HTTPException):
        await proc.request_build(False, True, owner=True)
    assert (await proc.kick(False))["status"] == "refused"


async def test_running_holds_tagging(env):
    proc, registry, queue, *_ = env
    await proc.kick(False)

    class Tag:
        id, label, order, holds = "tagging", "Tagging", 30, ()

        def capabilities(self):
            from backend.services.background.protocol import Capabilities

            return Capabilities(True, True, False)

        def report(self):
            from backend.services.background.protocol import ProcessReport

            return ProcessReport(State.IDLE, None, "x")

        async def start(self):
            pass

        pause = resume = stop = start

    registry.register(Tag())
    st = registry.status("tagging")
    assert st.state == State.HELD
    assert st.held_by == ["Embedding"]


async def test_auto_index_does_not_start_while_blocked():
    started = []

    async def start_build():
        started.append(1)

    blocked = {"on": True}
    sched = AutoIndexScheduler(
        start_build,
        lambda: False,
        lambda: False,
        delay_s=0.02,
        is_blocked=lambda: blocked["on"],
    )
    sched.notify()
    await asyncio.sleep(0.1)
    assert started == []
    blocked["on"] = False
    await asyncio.sleep(0.1)
    assert started == [1]
    await sched.aclose()
