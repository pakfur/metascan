"""The Storyboard process: run tracking, the cancel point, pause and stop."""

import asyncio

import pytest

from backend.services.background.generation import StoryboardProcess
from backend.services.background.protocol import State
from backend.services.background.registry import ProcessRegistry
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import RunCancelled, StoryboardRunner
from tests.test_background_generation import Comfy, Stub, until
from tests import test_storyboard_compile as compile_t
from tests import test_storyboard_compose as compose_t
from tests.test_storyboard_runner import StubComfy


class BlockingVlm:
    """Answers like the compose / compile fakes, but each call first waits for
    ``release`` (set it to let calls through)."""

    model_id = "qwen3vl-4b"

    def __init__(self, inner=None, blocked=True):
        self.inner = inner
        self.blocked = blocked
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.started = 0
        self.finished = 0

    async def ensure_started(self, model_id):
        pass

    async def shutdown(self):
        pass

    async def generate_text(self, **kw):
        self.started += 1
        await self.release.wait()
        self.finished += 1
        if self.inner is not None:
            return await self.inner.generate_text(**kw)
        return "a prompt"


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def make(db, tmp_path, vlm):
    events = []
    runner = StoryboardRunner(
        db, StubComfy(db), (lambda: vlm), output_root=tmp_path / "out"
    )
    runner.on_event(lambda ch, ev, d: events.append((ev, d)))
    return runner, events


@pytest.fixture
async def env(db, tmp_path):
    vlm = BlockingVlm()
    runner, events = make(db, tmp_path, vlm)
    comfy = Comfy()
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    registry.register(Stub("tagging"))
    registry.register(Stub("embedding"))
    proc = StoryboardProcess(registry, runner, comfy, wind_down_poll_s=0.01)
    registry.register(proc)
    yield proc, registry, comfy, runner, vlm, events, db
    vlm.release.set()
    await proc.aclose()
    await registry.aclose()


def board_with_beats(db, n):
    sb = db.create_storyboard(
        name="Secret Title", target_model="sd", architecture="t2i", base_seed=1
    )
    scene = db.create_scene(sb, name="S")
    panel = db.create_panel(scene, action="secret action")
    for i in range(n):
        db.create_beat(panel, action=f"beat {i}", subject_ids=[])
    return sb


def names(events):
    return [e for e, _ in events]


async def test_synthesis_is_a_tracked_run_with_counts_only(env):
    proc, registry, comfy, runner, vlm, events, db = env
    sb = board_with_beats(db, 2)
    task = asyncio.create_task(runner.synthesize(sb))
    await until(lambda: vlm.started >= 1)
    assert runner.active_runs() == [
        {"storyboard_id": sb, "kind": "synthesis", "done": 0, "total": 2}
    ]
    st = registry.status("storyboard")
    assert st.state == State.RUNNING
    assert st.activity == "Synthesis 1 of 2"
    assert "Secret" not in st.activity and "secret" not in st.activity
    vlm.release.set()
    await task
    assert runner.active_runs() == []
    await until(lambda: registry.status("storyboard").state == State.IDLE)


async def test_compile_is_a_tracked_run(db, tmp_path):
    vlm = BlockingVlm(inner=compile_t.FakeVlm())
    runner, events = make(db, tmp_path, vlm)
    sb = compile_t._make_storyboard(db)
    compile_t._make_panel(db, sb)
    task = asyncio.create_task(runner.compile_video(sb))
    await until(lambda: vlm.started >= 1)
    assert runner.active_runs()[0]["kind"] == "compile"
    assert runner.active_runs()[0]["total"] == 1
    vlm.release.set()
    await task
    assert runner.active_runs() == []
    assert names(events).count("compile_complete") == 1


async def test_compose_is_a_tracked_run(db, tmp_path):
    vlm = BlockingVlm(inner=compose_t.FakeVlm())
    runner, events = make(db, tmp_path, vlm)
    sb = compose_t._board(db)
    task = asyncio.create_task(runner.compose_story(sb))
    await until(lambda: vlm.started >= 1)
    assert [r["kind"] for r in runner.active_runs()] == ["compose"]
    vlm.release.set()
    await task
    assert runner.active_runs() == []
    assert names(events).count("story_complete") == 1


async def test_a_failed_run_still_ends(db, tmp_path):
    runner, events = make(db, tmp_path, None)
    with pytest.raises(Exception):
        await runner.synthesize(999)
    assert runner.active_runs() == []
    assert names(events).count("synthesis_error") == 1


async def test_stop_ends_synthesis_between_beats_with_one_error_event(env):
    proc, registry, comfy, runner, vlm, events, db = env
    sb = board_with_beats(db, 12)
    task = asyncio.create_task(runner.synthesize(sb))
    await until(lambda: vlm.started >= 1)
    in_call = vlm.started
    await registry.command("storyboard", "stop")
    vlm.release.set()
    with pytest.raises(RunCancelled):
        await task
    assert vlm.started == in_call  # nothing began after the stop
    errors = [d for e, d in events if e == "synthesis_error"]
    assert len(errors) == 1 and errors[0]["error"] == "cancelled"
    assert "synthesis_complete" not in names(events)
    assert runner.active_runs() == []
    assert registry.status("storyboard").state == State.STOPPED


async def test_stop_cancels_the_storyboards_jobs(env):
    proc, registry, comfy, runner, vlm, events, db = env
    sb = board_with_beats(db, 4)
    cancelled = []

    async def fake_cancel(storyboard_id):
        cancelled.append(storyboard_id)
        return 0

    runner.cancel = fake_cancel
    task = asyncio.create_task(runner.synthesize(sb))
    await until(lambda: vlm.started >= 1)
    await registry.command("storyboard", "stop")
    vlm.release.set()
    with pytest.raises(RunCancelled):
        await task
    assert cancelled == [sb]


async def test_pause_gates_submissions_waits_at_the_next_check_and_resume_continues(
    env,
):
    proc, registry, comfy, runner, vlm, events, db = env
    sb = board_with_beats(db, 12)
    task = asyncio.create_task(runner.synthesize(sb))
    await until(lambda: vlm.started >= 1)
    await registry.command("storyboard", "pause")
    assert "storyboard" in comfy.gated
    assert registry.status("storyboard").activity == "Pausing after the current job"
    vlm.release.set()
    await until(lambda: registry.status("storyboard").state == State.PAUSED)
    settled = vlm.started
    await asyncio.sleep(0.05)
    assert vlm.started == settled and not task.done()
    assert runner.active_runs()[0]["done"] < 12
    await registry.command("storyboard", "resume")
    assert comfy.gated == set()
    counts = await asyncio.wait_for(task, 2)
    assert counts["synthesized"] + counts["fallback"] == 12
    assert names(events).count("synthesis_complete") == 1


async def test_an_owner_run_clears_stopped(env):
    proc, registry, comfy, runner, vlm, events, db = env
    await registry.command("storyboard", "stop")
    assert registry.status("storyboard").state == State.STOPPED
    await proc.owner_batch_starting()
    sb = board_with_beats(db, 1)
    task = asyncio.create_task(runner.synthesize(sb))
    await until(lambda: vlm.started >= 1)
    assert registry.status("storyboard").state == State.RUNNING
    vlm.release.set()
    await task
