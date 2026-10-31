"""Hygiene's checks framework and idle rule (spec 10)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api import scan as scan_api
from backend.services.background.hygiene import (
    HYGIENE_INTERVAL_S,
    CheckResult,
    HygieneProcess,
)
from backend.services.background.idle import (
    IDLE_AFTER_S,
    ActivityMiddleware,
    IdleTracker,
)
from backend.services.background.protocol import (
    Capabilities,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Other:
    """Another process whose state the test sets."""

    holds: tuple = ()
    order = 1

    def __init__(self, pid="embedding"):
        self.id = self.label = pid
        self.state = State.IDLE

    def capabilities(self):
        return Capabilities(True, True, True)

    def report(self):
        return ProcessReport(self.state, None, "")

    async def start(self): ...
    async def pause(self): ...
    async def resume(self): ...
    async def stop(self): ...


class Check:
    def __init__(self, name, log, raises=None, gate=None):
        self.name = name
        self.log = log
        self.raises = raises
        self.gate = gate

    async def run(self, ctx):
        self.log.append(self.name)
        ctx.report(f"{self.name} /home/user/secret/pic.png", 1, 2)
        if self.gate is not None:
            await self.gate.wait()
        if self.raises:
            raise self.raises
        return CheckResult()


class Rig:
    def __init__(self, tmp_path, checks=(), shared=None):
        self.clock = shared.clock if shared else Clock()
        self.tracker = shared.tracker if shared else IdleTracker(self.clock)
        self.path = tmp_path / "bg.json"
        self.registry = ProcessRegistry(self.path, lambda st: None)
        self.other = Other()
        self.registry.register(self.other)
        self.log = []
        self.proc = HygieneProcess(
            self.registry,
            self.tracker,
            get_db=lambda: SimpleNamespace(),
            get_config=lambda: {},
            get_data_dir=lambda db: None,
            checks=checks,
            clock=self.clock,
        )
        self.registry.register(self.proc)

    async def aclose(self):
        await self.proc.aclose()
        await self.registry.aclose()

    def quiet(self, s=IDLE_AFTER_S):
        self.clock.advance(s)

    async def run_pass(self):
        assert self.proc.tick()
        await self.proc.wait_pass()


@pytest.fixture
async def mk(tmp_path):
    made = []

    def make(shared=None):
        r = Rig(tmp_path, shared=shared)
        made.append(r)
        return r

    yield make
    for r in made:
        await r.aclose()


@pytest.fixture
def rig(mk):
    r = mk()
    r.proc.checks = [Check("a", r.log), Check("b", r.log)]
    return r


async def test_a_pass_waits_for_a_quiet_moment(rig):
    assert not rig.proc.tick()
    rig.quiet(IDLE_AFTER_S - 1)
    assert not rig.proc.tick()
    rig.quiet(1)
    await rig.run_pass()
    assert rig.log == ["a", "b"]
    assert rig.registry.status("hygiene").activity.startswith("In sync")


async def test_the_first_pass_ignores_the_interval_the_next_does_not(rig):
    rig.quiet()
    await rig.run_pass()  # no interval before the first pass
    rig.quiet()  # quiet again, but the pass is recent
    assert not rig.proc.tick()
    rig.quiet(HYGIENE_INTERVAL_S)
    rig.tracker.note_request()
    assert not rig.proc.tick()  # interval over, owner just used the app
    rig.quiet()
    await rig.run_pass()
    assert rig.log == ["a", "b", "a", "b"]


async def test_a_write_request_resets_the_idle_timer_a_get_does_not(rig):
    sent = []

    async def inner(scope, receive, send):
        sent.append(scope.get("method"))

    app = ActivityMiddleware(inner, rig.tracker)
    rig.quiet(IDLE_AFTER_S - 10)
    await app({"type": "http", "method": "GET"}, None, None)
    await app({"type": "websocket"}, None, None)
    rig.quiet(10)
    assert rig.tracker.idle_for() >= IDLE_AFTER_S  # reads did not count
    await app({"type": "http", "method": "POST"}, None, None)
    assert rig.tracker.idle_for() == 0
    assert len(sent) == 3
    assert not rig.proc.tick()


async def test_a_running_process_blocks_it_but_a_waiting_one_does_not(rig):
    rig.quiet()
    rig.other.state = State.RUNNING
    assert not rig.proc.tick()
    rig.other.state = State.WAITING  # Import polling reads as waiting / idle
    await rig.run_pass()


async def test_play_runs_now_ignoring_both_timers(rig):
    assert rig.registry.status("hygiene").capabilities.can_start
    await rig.registry.command("hygiene", "start")
    await rig.proc.wait_pass()
    assert rig.log == ["a", "b"]
    await rig.registry.command("hygiene", "start")  # inside the interval, not idle
    await rig.proc.wait_pass()
    assert rig.log == ["a", "b", "a", "b"]


async def test_pause_lands_between_checks(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    st = r.registry.status("hygiene")
    assert st.state == State.RUNNING and st.capabilities.can_stop
    await r.registry.command("hygiene", "pause")
    assert r.registry.status("hygiene").activity == "Stopping after this step"
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a"]  # b did not start
    assert r.registry.status("hygiene").state == State.PAUSED
    r.quiet(HYGIENE_INTERVAL_S)
    assert not r.proc.tick()  # paused: no pass by itself
    await r.registry.command("hygiene", "resume")
    await r.run_pass()  # continues with a fresh pass at the next quiet moment
    assert r.log == ["a", "a", "b"]


async def test_a_process_starting_mid_pass_stops_it_between_checks(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    r.other.state = State.RUNNING
    r.quiet(1)  # past the 1 s cache of should_stop
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a"]
    # not complete: no interval, the next quiet moment runs a new pass
    r.other.state = State.IDLE
    assert r.registry.status("hygiene").activity == "Waiting for a quiet moment"
    await r.run_pass()
    assert r.log.count("b") == 1


async def test_hygiene_is_in_nobodys_holds(rig):
    assert all("hygiene" not in p.holds for p in (rig.other, rig.proc))
    assert rig.proc.holds == ()


async def test_a_held_hygiene_stays_paused_until_released(rig):
    rig.quiet()
    with rig.registry.hold(["hygiene"], by="Full clean"):
        assert not rig.proc.tick()
        assert rig.registry.status("hygiene").state == State.HELD
    await rig.run_pass()


async def test_a_stop_stays_stopped_across_a_restart(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    await r.registry.command("hygiene", "stop")  # only while a pass runs
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a"]
    assert r.registry.status("hygiene").state == State.STOPPED
    r.quiet(HYGIENE_INTERVAL_S)
    assert not r.proc.tick()
    r2 = mk(shared=r)  # a restart: a new registry reads the same state file
    r2.proc.checks = [Check("a", r2.log)]
    assert not r2.proc.tick()
    assert r2.registry.status("hygiene").state == State.STOPPED
    await r2.registry.command("hygiene", "start")  # the owner's play clears it
    await r2.proc.wait_pass()
    assert r2.log == ["a"]


async def test_a_raising_check_fails_the_pass_with_the_type_only(mk):
    r = mk()
    r.proc.checks = [
        Check("a", r.log, raises=ValueError("/secret/path")),
        Check("b", r.log),
    ]
    r.quiet()
    await r.run_pass()
    st = r.registry.status("hygiene")
    assert (st.state, st.error) == (State.FAILED, "ValueError")
    assert st.capabilities.can_start
    assert r.log == ["a"]
    assert not r.proc.tick()  # the interval holds a failing pass off
    r.proc.checks[0].raises = None
    await r.registry.command("hygiene", "start")  # play retries
    await r.proc.wait_pass()
    assert r.log == ["a", "a", "b"]
    assert r.registry.status("hygiene").state == State.IDLE


async def test_activity_never_holds_a_full_path(rig):
    rig.quiet()
    gate = asyncio.Event()
    rig.proc.checks = [Check("a", rig.log, gate=gate)]
    assert rig.proc.tick()
    await asyncio.sleep(0)
    st = rig.registry.status("hygiene")
    assert "/" not in st.activity and "secret" not in st.activity
    assert st.progress.total == 2
    gate.set()
    await rig.proc.wait_pass()


async def test_no_checks_reads_in_sync_after_a_pass(mk):
    r = mk()
    st = r.registry.status("hygiene")
    assert (st.order, st.label, st.activity) == (
        80,
        "Library hygiene",
        "Waiting for a quiet moment",
    )
    r.quiet()
    await r.run_pass()
    assert r.registry.status("hygiene").activity.startswith("In sync")


# ---- scan.finish_ordinary_pass --------------------------------------------


@pytest.fixture
def finish(monkeypatch):
    calls = []
    monkeypatch.setattr(scan_api, "get_db", lambda: object())
    monkeypatch.setattr(scan_api, "_data_dir", lambda db: Path("/data"))
    monkeypatch.setattr(
        scan_api, "clear_full_clean_marker", lambda d: calls.append("marker")
    )
    monkeypatch.setattr(
        scan_api, "apply_pending_taste_snapshot", lambda *a: calls.append("apply")
    )
    return calls


def test_finish_clears_the_marker_only_for_a_completed_walk(finish):
    scan_api.finish_ordinary_pass(scan_api.PassReport(walked_folders=2))
    assert finish == ["marker", "apply"]


@pytest.mark.parametrize(
    "report",
    [
        scan_api.PassReport(cancelled=True, walked_folders=1),
        scan_api.PassReport(generated_back=False, walked_folders=1),
        scan_api.PassReport(missing_folders=["/m"], walked_folders=1),
        scan_api.PassReport(walked_folders=0),
    ],
)
def test_finish_keeps_the_marker_otherwise(finish, report):
    scan_api.finish_ordinary_pass(report)
    assert finish == ["apply"]


# ---- Play never runs alongside other work ----------------------------------


async def test_play_waits_for_a_running_process_then_runs(rig):
    rig.other.state = State.RUNNING
    await rig.registry.command("hygiene", "start")
    st = rig.registry.status("hygiene")
    assert (st.state, st.activity) == (State.WAITING, "Waiting for embedding")
    assert st.capabilities.can_stop and not st.capabilities.can_start
    assert not rig.proc.tick()  # still busy; no idle needed, no pass yet
    assert rig.log == []
    rig.other.state = State.IDLE
    await rig.run_pass()  # not idle, inside no interval: Play ignores both
    assert rig.log == ["a", "b"]
    assert rig.registry.status("hygiene").activity.startswith("In sync")


async def test_a_process_starting_mid_manual_pass_stops_it_and_it_resumes(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    await r.registry.command("hygiene", "start")
    await asyncio.sleep(0)
    assert r.proc._running()
    r.other.state = State.RUNNING
    r.quiet(1)
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a"]  # stopped between checks
    assert r.registry.status("hygiene").state == State.WAITING  # still pending
    r.other.state = State.IDLE
    await r.run_pass()
    assert r.log == ["a", "a", "b"]


async def test_stop_clears_a_pending_play(rig):
    rig.other.state = State.RUNNING
    await rig.registry.command("hygiene", "start")
    await rig.registry.command("hygiene", "stop")
    rig.other.state = State.IDLE
    rig.quiet(HYGIENE_INTERVAL_S)
    assert not rig.proc.tick()
    assert rig.registry.status("hygiene").state == State.STOPPED
    assert rig.log == []


async def test_a_pass_cut_short_after_the_last_check_is_not_complete(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    r.other.state = State.RUNNING
    r.quiet(1)
    gate.set()
    await r.proc.wait_pass()
    assert r.registry.status("hygiene").activity == "Waiting for a quiet moment"
    r.other.state = State.IDLE
    assert r.proc.tick()  # no interval: the pass did not complete
    await r.proc.wait_pass()


async def test_a_winding_down_process_stops_a_pass(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    r.other.report = lambda: ProcessReport(
        State.RUNNING, None, "Saving", winding_down=True
    )
    r.quiet(1)
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a"]


async def test_a_polling_only_import_does_not_stop_a_pass(tmp_path, mk):
    r = mk()
    r.other.report = lambda: ProcessReport(State.IDLE, None, "Watching 2 folders")
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate), Check("b", r.log)]
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    r.quiet(2)
    gate.set()
    await r.proc.wait_pass()
    assert r.log == ["a", "b"]


async def test_should_stop_snapshots_the_registry_once_a_second(rig):
    calls = []
    real = rig.registry.statuses
    rig.registry.statuses = lambda: calls.append(1) or real()
    rig.proc.tick()  # one fresh read
    n = len(calls)
    for _ in range(1000):
        rig.proc.should_stop()
    assert len(calls) <= n + 1
    rig.quiet(1)
    rig.proc.should_stop()
    assert len(calls) <= n + 2


async def test_aclose_stops_the_poll_and_the_pass(mk):
    r = mk()
    gate = asyncio.Event()
    r.proc.checks = [Check("a", r.log, gate=gate)]
    r.proc.start_loop()
    r.quiet()
    assert r.proc.tick()
    await asyncio.sleep(0)
    loop_task, pass_task = r.proc._loop_task, r.proc._pass
    await r.proc.aclose()
    assert loop_task.done() and pass_task.done()
    assert not r.proc.tick()
