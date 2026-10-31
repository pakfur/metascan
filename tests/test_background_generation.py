"""The T2I and I2V processes: gate-based pause, holds, stop, owner start."""

import asyncio
import json

import pytest

from backend.services.background.generation import I2vProcess, T2iProcess
from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry
from metascan.core.comfy_client import ComfyClient
from metascan.core.database_sqlite import DatabaseManager
from tests.test_comfy_client import params, t2i_workflow


class Stub:
    """Tagging / Embedding stand-in: idle, holds nothing."""

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


class Comfy:
    def __init__(self):
        self.gated = set()
        self.in_flight = 0
        self.gated_jobs = False
        self.log = []

    def set_source_paused(self, source, paused):
        (self.gated.add if paused else self.gated.discard)(source)
        self.log.append(("gate", paused))

    def source_in_flight(self, source):
        return self.in_flight

    def has_gated_jobs(self, source):
        return self.gated_jobs

    def source_job_count(self, source):
        return 0

    async def cancel_source(self, source):
        return 0


class T2iRunner:
    def __init__(self):
        self.rows = []
        self.writing = False
        self.cancelled = []
        self.wait_unpaused = None
        self.log = []
        self.unload_gate = None

    async def unload_vlms(self):
        self.log.append("unload-start")
        if self.unload_gate is not None:
            await self.unload_gate.wait()
        self.log.append("unload-end")

    def active_batches(self):
        return list(self.rows)

    def is_writing(self):
        return self.writing

    async def cancel_batch(self, batch_id):
        self.cancelled.append(batch_id)
        self.rows = [r for r in self.rows if r["batch_id"] != batch_id]
        return True


class I2vRunner(T2iRunner):
    def progress_counts(self):
        return 1, 4, "rendering"


def row(done=2, failed=0, total=5, state="rendering", batch_id="b1"):
    return {
        "batch_id": batch_id,
        "images_done": done,
        "images_failed": failed,
        "images_total": total,
        "state": state,
    }


async def until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


@pytest.fixture
async def env(tmp_path):
    comfy, runner = Comfy(), T2iRunner()
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    registry.register(Stub("tagging"))
    registry.register(Stub("embedding"))
    proc = T2iProcess(registry, runner, comfy, wind_down_poll_s=0.01)
    registry.register(proc)
    yield proc, registry, comfy, runner
    await proc.aclose()
    await registry.aclose()


def status(registry, pid="t2i"):
    return registry.status(pid)


async def test_idle_without_batches(env):
    proc, registry, *_ = env
    st = status(registry)
    assert st.state == State.IDLE and st.progress is None
    assert st.capabilities == Capabilities(True, True, True)


async def test_active_batch_reports_counts_only_and_holds_tagging_and_embedding(env):
    proc, registry, comfy, runner = env
    runner.rows = [row(done=2, failed=0, total=5)]
    proc.poke()
    st = status(registry)
    assert st.state == State.RUNNING
    assert st.activity == "Image 3 of 5, rendering"
    assert (st.progress.done, st.progress.total, st.progress.unit) == (2, 5, "images")
    for pid in ("tagging", "embedding"):
        held = status(registry, pid)
        assert held.state == State.HELD and held.held_by == ["Text to image"]


async def test_prompting_phase_word(env):
    proc, registry, comfy, runner = env
    runner.rows = [row(done=0, state="prompting")]
    assert status(registry).activity == "Image 1 of 5, writing prompts"


async def test_pause_winds_down_then_pauses_and_releases_the_holds(env):
    proc, registry, comfy, runner = env
    runner.rows = [row()]
    comfy.in_flight = 1
    await registry.command("t2i", "pause")
    assert comfy.gated == {"t2i"}
    st = status(registry)
    assert st.state == State.RUNNING
    assert st.activity == "Pausing after the current job"
    assert status(registry, "tagging").state == State.HELD  # still holds
    comfy.in_flight = 0
    await until(lambda: status(registry).state == State.PAUSED)
    assert status(registry, "tagging").state == State.IDLE
    assert status(registry, "embedding").state == State.IDLE


async def test_pause_waits_for_a_prompt_step_mid_call(env):
    proc, registry, comfy, runner = env
    runner.rows = [row(state="prompting")]
    runner.writing = True
    await registry.command("t2i", "pause")
    assert status(registry).activity == "Pausing after the current job"
    runner.writing = False
    await until(lambda: status(registry).state == State.PAUSED)


async def test_pause_blocks_the_prompting_loop_and_resume_releases_it(env):
    proc, registry, comfy, runner = env
    assert runner.wait_unpaused == proc.wait_unpaused
    await asyncio.wait_for(runner.wait_unpaused(), 1)  # open
    await registry.command("t2i", "pause")
    waiter = asyncio.create_task(runner.wait_unpaused())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    await registry.command("t2i", "resume")
    await asyncio.wait_for(waiter, 1)
    assert comfy.gated == set()


async def test_stop_cancels_every_active_batch_and_opens_the_gate(env):
    proc, registry, comfy, runner = env
    runner.rows = [row(batch_id="a"), row(batch_id="b")]
    await registry.command("t2i", "pause")
    await registry.command("t2i", "stop")
    assert sorted(runner.cancelled) == ["a", "b"]
    assert comfy.gated == set()
    assert status(registry).state == State.STOPPED


async def test_an_owner_batch_clears_stopped(env):
    proc, registry, comfy, runner = env
    await registry.command("t2i", "stop")
    assert status(registry).state == State.STOPPED
    await proc.owner_batch_starting()
    runner.rows = [row()]
    proc.poke()
    assert status(registry).state == State.RUNNING


async def test_an_owner_batch_while_paused_stays_paused_behind_the_gate(env):
    proc, registry, comfy, runner = env
    await registry.command("t2i", "pause")
    await proc.owner_batch_starting()
    runner.rows = [row()]
    proc.poke()
    assert status(registry).state == State.PAUSED
    assert comfy.gated == {"t2i"}


async def test_submit_does_not_wait_on_the_hold(tmp_path):
    comfy = ComfyClient(
        "http://127.0.0.1:1", tmp_path / "out", DatabaseManager(tmp_path / "db")
    )
    runner = T2iRunner()
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    registry.register(Stub("tagging"))
    registry.register(Stub("embedding"))
    proc = T2iProcess(registry, runner, comfy)
    registry.register(proc)
    try:
        runner.rows = [row()]
        proc.poke()
        assert status(registry, "tagging").state == State.HELD
        pid = await comfy.register_preset("sdxl", "t2i", t2i_workflow())
        await asyncio.wait_for(comfy.submit(pid, params(), t2i_batch_id="b1"), 2)
    finally:
        await proc.aclose()
        await registry.aclose()
        await comfy.aclose()


async def test_a_persisted_pause_is_reapplied_on_register(tmp_path):
    (tmp_path / "bg.json").write_text(json.dumps({"intents": {"t2i": "paused"}}))
    comfy = Comfy()
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    assert registry.persisted_intent("t2i") == "paused"
    proc = T2iProcess(registry, T2iRunner(), comfy, wind_down_poll_s=0.01)
    registry.register(proc)
    try:
        await registry.settle()
        assert comfy.gated == {"t2i"}
        assert status(registry).state == State.PAUSED
    finally:
        await proc.aclose()
        await registry.aclose()


async def test_i2v_reports_videos(tmp_path):
    comfy, runner = Comfy(), I2vRunner()
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    proc = I2vProcess(registry, runner, comfy)
    registry.register(proc)
    try:
        assert proc.id == "i2v" and proc.order == 50
        assert proc.holds == ("tagging", "embedding")
        runner.rows = [{"batch_id": "v"}]
        proc.poke()
        st = status(registry, "i2v")
        assert st.activity == "Video 2 of 4, rendering"
        assert st.progress.unit == "videos"
        await registry.command("i2v", "stop")
        assert runner.cancelled == ["v"]
    finally:
        await proc.aclose()
        await registry.aclose()


async def test_i2v_runner_waits_for_the_gate_before_writing_a_prompt(tmp_path):
    from metascan.core.i2v_runner import I2vBatchRequest, I2vRunner, _I2vBatch

    runner = I2vRunner(db=None, comfy=None, get_vlm=lambda: None, output_root=tmp_path)
    gate = asyncio.Event()
    seen = {"writing_during": None}

    async def wait_unpaused():
        await gate.wait()

    async def write_prompt(*a, **k):
        seen["writing_during"] = runner.is_writing()
        return "text", [], "model"

    runner.wait_unpaused = wait_unpaused
    runner.write_prompt = write_prompt  # type: ignore[assignment]
    req = I2vBatchRequest.__new__(I2vBatchRequest)
    for name, value in dict(
        source_path="/a.png",
        idea="",
        prompt="",
        duration_s=6,
        template_id=None,
        writer="base",
    ).items():
        object.__setattr__(req, name, value)
    b = _I2vBatch(id="x", req=req, seeds=[[1]], window=asyncio.Semaphore(1))
    task = asyncio.create_task(runner._step_prompt(b, 0))
    await asyncio.sleep(0.05)
    assert not task.done() and not runner.is_writing()
    gate.set()
    text, lineage = await asyncio.wait_for(task, 1)
    assert text == "text" and seen["writing_during"] is True
    assert not runner.is_writing()


async def test_generation_busy_ignores_a_paused_batch(tmp_path):
    from backend.services.auto_index import generation_busy

    class T2i:
        rows = [{"batch_id": "b"}]
        writing = False

        def active_batches(self):
            return self.rows

        def is_writing(self):
            return self.writing

    comfy = ComfyClient(
        "http://127.0.0.1:1", tmp_path / "out", DatabaseManager(tmp_path / "db")
    )
    t2i = T2i()
    try:
        pid = await comfy.register_preset("sdxl", "t2i", t2i_workflow())
        await comfy.submit(pid, params(), t2i_batch_id="b")
        assert generation_busy(t2i, comfy) is True
        comfy.set_source_paused("t2i", True)
        assert generation_busy(t2i, comfy) is False
        t2i.writing = True  # a prompt mid-call is still activity
        assert generation_busy(t2i, comfy) is True
    finally:
        await comfy.aclose()


async def test_resume_unloads_before_the_gate_opens_and_holds_meanwhile(env):
    proc, registry, comfy, runner = env
    await registry.command("t2i", "pause")
    assert status(registry, "tagging").state == State.IDLE  # free to reload
    comfy.gated_jobs = True
    runner.unload_gate = asyncio.Event()
    comfy.log.clear()
    resume = asyncio.create_task(registry.command("t2i", "resume"))
    await until(lambda: "unload-start" in runner.log)
    st = status(registry)
    assert st.state == State.RUNNING and st.activity == "Resuming"
    assert status(registry, "tagging").state == State.HELD
    assert ("gate", False) not in comfy.log  # no job can dispatch yet
    runner.unload_gate.set()
    await resume
    assert runner.log == ["unload-start", "unload-end"]
    assert comfy.log == [("gate", False)]
    assert comfy.gated == set()


async def test_resume_with_nothing_gated_unloads_nothing(env):
    proc, registry, comfy, runner = env
    await registry.command("t2i", "pause")
    await registry.command("t2i", "resume")
    assert runner.log == []
    assert comfy.gated == set()


async def test_the_runner_unload_is_off_when_the_config_says_so(tmp_path):
    from metascan.core.i2v_runner import I2vRunner

    class Vlm:
        model_id = "m"
        shut = 0

        async def shutdown(self):
            self.shut += 1

    vlm = Vlm()
    for flag, expected in ((True, 1), (False, 0)):
        vlm.shut = 0
        runner = I2vRunner(
            db=None,
            comfy=None,
            get_vlm=lambda: vlm,
            output_root=tmp_path,
            unload_vlm_during_generation=flag,
        )
        await runner.unload_vlms()
        assert vlm.shut == expected


async def test_resume_does_not_block_a_new_submission(env):
    proc, registry, comfy, runner = env
    await registry.command("t2i", "pause")
    comfy.gated_jobs = True
    runner.unload_gate = asyncio.Event()  # a slow unload
    resume = asyncio.create_task(registry.command("t2i", "resume"))
    await until(lambda: "unload-start" in runner.log)
    await asyncio.wait_for(proc.owner_batch_starting(), 1)
    runner.rows = [row()]
    proc.poke()
    runner.unload_gate.set()
    await resume
