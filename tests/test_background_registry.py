import pytest

from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)
from backend.services.background.registry import CommandRefused, ProcessRegistry


class FakeProcess:
    def __init__(self, pid, label=None, order=0, holds=()):
        self.id = pid
        self.label = label or pid.upper()
        self.order = order
        self.holds = tuple(holds)
        self.state = State.RUNNING
        self.error = None
        self.calls = []
        self.fail_pause = False

    def capabilities(self):
        return Capabilities(True, True, True)

    def report(self):
        return ProcessReport(self.state, None, "x", self.error)

    async def start(self):
        self.calls.append("start")

    async def pause(self):
        self.calls.append("pause")
        if self.fail_pause:
            raise RuntimeError("boom")

    async def resume(self):
        self.calls.append("resume")

    async def stop(self):
        self.calls.append("stop")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(tmp_path, *procs, clock=None):
    events = []
    reg = ProcessRegistry(tmp_path / "bg.json", events.append, clock or Clock())
    for p in procs:
        reg.register(p)
    return reg, events


async def test_hold_pauses_then_release_resumes(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    with reg.hold(["emb"], by="T2I"):
        await reg.settle()
        st = reg.status("emb")
        assert st.state == State.HELD and st.held_by == ["T2I"]
    await reg.settle()
    assert f.calls == ["pause", "resume"]
    assert reg.status("emb").state == State.RUNNING
    await reg.aclose()


async def test_two_holders_resume_on_the_last_release(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    a = reg.hold(["emb"], by="A")
    b = reg.hold(["emb"], by="B")
    a.__enter__()
    b.__enter__()
    await reg.settle()
    a.__exit__(None, None, None)
    await reg.settle()
    assert f.calls == ["pause"]
    b.__exit__(None, None, None)
    await reg.settle()
    assert f.calls == ["pause", "resume"]
    await reg.aclose()


async def test_owner_pause_survives_release(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    with reg.hold(["emb"], by="T2I"):
        await reg.settle()
        st = await reg.command("emb", "pause")
        assert st.state == State.PAUSED
    await reg.settle()
    assert reg.status("emb").state == State.PAUSED
    assert f.calls == ["pause"]
    await reg.aclose()


async def test_play_while_held_stays_held_until_release(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    with reg.hold(["emb"], by="T2I"):
        await reg.command("emb", "pause")
        st = await reg.command("emb", "resume")
        assert st.state == State.HELD
        assert f.calls == ["pause"]
    await reg.settle()
    assert f.calls == ["pause", "resume"]
    assert reg.status("emb").state == State.RUNNING
    await reg.aclose()


async def test_stopped_ignores_holds_and_releases(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    await reg.command("emb", "stop")
    with reg.hold(["emb"], by="T2I"):
        await reg.settle()
        assert reg.status("emb").state == State.STOPPED
    await reg.settle()
    assert f.calls == ["stop"]
    assert reg.status("emb").state == State.STOPPED
    assert reg.blocked("emb")
    await reg.aclose()


async def test_stopped_and_paused_persist_across_restart(tmp_path):
    a, b = FakeProcess("a"), FakeProcess("b")
    reg, _ = make(tmp_path, a, b)
    await reg.command("a", "pause")
    await reg.command("b", "stop")
    with reg.hold(["a"], by="X"):
        await reg.aclose()
        a2, b2 = FakeProcess("a"), FakeProcess("b")
        reg2, _ = make(tmp_path, a2, b2)
        await reg2.settle()
        assert reg2.status("a").state == State.PAUSED
        assert reg2.status("a").held_by == []
        assert reg2.status("b").state == State.STOPPED
        assert a2.calls == ["pause"] and b2.calls == []
    await reg2.aclose()
    # a pure hold does not persist
    c = FakeProcess("c")
    reg3, _ = make(tmp_path / "other", c)
    with reg3.hold(["c"], by="X"):
        await reg3.settle()
    reg4, _ = make(tmp_path / "other", FakeProcess("c"))
    assert reg4.status("c").state == State.RUNNING
    await reg3.aclose()
    await reg4.aclose()


async def test_held_process_holds_nothing(tmp_path):
    t2i = FakeProcess("t2i", holds=("emb",))
    emb = FakeProcess("emb")
    reg, _ = make(tmp_path, t2i, emb)
    await reg.settle()
    assert reg.status("emb").state == State.HELD
    assert reg.status("emb").held_by == ["T2I"]
    t2i.state = State.WAITING
    reg.notify("t2i")
    await reg.settle()
    assert reg.status("emb").state == State.RUNNING
    assert emb.calls == ["pause", "resume"]
    await reg.aclose()


async def test_holder_failure_releases(tmp_path):
    t2i = FakeProcess("t2i", holds=("emb",))
    emb = FakeProcess("emb")
    reg, _ = make(tmp_path, t2i, emb)
    await reg.settle()
    t2i.state = State.FAILED
    t2i.error = "bad"
    reg.notify("t2i")
    await reg.settle()
    assert emb.calls == ["pause", "resume"]
    assert reg.status("t2i").state == State.FAILED
    await reg.aclose()


def test_cycle_in_holds_rejected(tmp_path):
    reg, _ = make(tmp_path, FakeProcess("a", holds=("b",)))
    with pytest.raises(ValueError):
        reg.register(FakeProcess("b", holds=("a",)))
    with pytest.raises(ValueError):
        reg.register(FakeProcess("a"))


async def test_hold_for_grace_releases_after_exception(tmp_path):
    clock = Clock()
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f, clock=clock)
    with pytest.raises(RuntimeError):
        with reg.hold_for(["emb"], "Chat", grace_s=30):
            raise RuntimeError("request died")
    await reg.settle()
    assert reg.status("emb").state == State.HELD
    clock.t = 29
    reg.tick()
    assert reg.status("emb").state == State.HELD
    clock.t = 31
    reg.tick()
    await reg.settle()
    assert reg.status("emb").state == State.RUNNING
    assert f.calls == ["pause", "resume"]
    await reg.aclose()


async def test_failed_keeps_error_until_play(tmp_path):
    f = FakeProcess("emb")
    reg, _ = make(tmp_path, f)
    f.state, f.error = State.FAILED, "oops"
    reg.notify("emb")
    f.state, f.error = State.IDLE, None
    st = reg.status("emb")
    assert st.state == State.FAILED and st.error == "oops"
    st = await reg.command("emb", "start")
    assert st.state == State.IDLE and st.error is None
    await reg.aclose()


async def test_notify_is_coalesced_to_two_per_second(tmp_path):
    clock = Clock()
    f = FakeProcess("emb")
    reg, events = make(tmp_path, f, clock=clock)
    base = len(events)
    for _ in range(10):
        reg.notify("emb")
    assert len(events) - base <= 1
    clock.t = 0.6
    reg.tick()
    reg.tick()
    assert len(events) - base <= 2
    assert events[-1].id == "emb"
    await reg.aclose()


async def test_failed_pause_records_error_without_corrupting(tmp_path):
    f = FakeProcess("emb")
    f.fail_pause = True
    reg, _ = make(tmp_path, f)
    st = await reg.command("emb", "pause")
    assert st.state == State.PAUSED
    assert "pause failed" in st.error
    st = await reg.command("emb", "resume")
    assert st.state == State.RUNNING
    await reg.aclose()


async def test_unknown_process_refused(tmp_path):
    reg, _ = make(tmp_path)
    with pytest.raises(CommandRefused):
        await reg.command("nope", "pause")
    await reg.aclose()


async def test_notify_from_a_worker_thread_runs_calls_on_the_loop(tmp_path):
    import asyncio
    import threading

    f = FakeProcess("emb")
    seen = []

    async def pause():
        seen.append(threading.get_ident())

    f.pause = pause  # type: ignore[method-assign]
    clock = Clock()
    reg, events = make(tmp_path, f, clock=clock)
    clock.t = 10.0  # past the per-process notify interval
    reg._capture_loop()
    loop_thread = threading.get_ident()

    def work():
        reg.hold(["emb"], by="T2I").__enter__()

    t = threading.Thread(target=work)
    t.start()
    t.join()
    for _ in range(40):
        if seen and any(e.state == State.HELD for e in events):
            break
        await asyncio.sleep(0.05)
    assert seen == [loop_thread]
    assert any(e.state == State.HELD for e in events)
    await reg.aclose()
