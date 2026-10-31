"""The Tagging process: kick, coalescing, pause, holds, owner unload, retry."""

import asyncio
import logging
import threading

import pytest

import backend.services.background.tagging as tagging_mod
from backend import dependencies
from backend.services.background.protocol import Progress, State
from backend.services.background.registry import (
    INTERACTIVE_GRACE_S,
    ProcessRegistry,
)
from backend.services.background.tagging import TaggingProcess


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Db:
    def __init__(self, queued=0):
        self.queued = queued

    def tag_queue_count(self):
        return self.queued


class Client:
    model_id = "m"
    preferred_model_id = "m"
    _explicit_stops = 4


class Pump:
    """Stands in for VlmTagPump; ``script`` decides what a pass does."""

    instances: list = []
    gate = None
    on_pass = None

    def __init__(self, **kw):
        self.kw = kw
        self.paused = False
        self.passes = 0
        Pump.instances.append(self)

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def progress(self):
        return Progress(done=2, total=5, unit="images")

    async def drain_once(self):
        self.passes += 1
        if Pump.gate is not None:
            await Pump.gate.wait()
        if Pump.on_pass is not None:
            Pump.on_pass(self)
        return 1


@pytest.fixture
async def env(monkeypatch, tmp_path):
    Pump.instances = []
    Pump.gate = None
    Pump.on_pass = None
    monkeypatch.setattr(tagging_mod, "VlmTagPump", Pump)
    monkeypatch.setattr(tagging_mod, "vlm_model_installed", lambda m: True)
    monkeypatch.setattr(tagging_mod, "preferred_vlm_model_id", lambda hw: "m")
    monkeypatch.setattr(tagging_mod, "detect_hardware", lambda: None)
    clock = Clock()
    events: list = []
    registry = ProcessRegistry(tmp_path / "bg.json", events.append, clock)
    db = Db()
    client = Client()
    busy = {"on": False}
    proc = TaggingProcess(
        registry,
        lambda: client,
        lambda: db,
        lambda: busy["on"],
        retry_s=0.05,
        defer_poll_s=0.01,
    )
    registry.register(proc)
    dependencies.set_registry(registry)
    yield proc, registry, db, client, busy, clock
    dependencies.set_registry(None)
    await proc.aclose()
    await registry.aclose()


async def until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


async def test_idle_with_an_empty_queue(env):
    proc, registry, *_ = env
    st = registry.status("tagging")
    assert st.state == State.IDLE
    assert st.activity == "Nothing to tag"
    assert (st.label, st.order) == ("Tagging", 30)


async def test_running_shows_progress_in_images(env):
    proc, registry, db, *_ = env
    db.queued = 5
    Pump.gate = asyncio.Event()
    assert proc.kick()
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    st = registry.status("tagging")
    assert st.state == State.RUNNING
    assert st.progress == Progress(2, 5, "images")
    assert "2 of 5" in st.activity
    db.queued = 0
    Pump.gate.set()
    await until(lambda: registry.status("tagging").state == State.IDLE)


async def test_pause_stops_kicks(env):
    proc, registry, db, *_ = env
    db.queued = 3
    await registry.command("tagging", "pause")
    st = registry.status("tagging")
    assert st.state == State.PAUSED
    assert proc.kick() is False
    await asyncio.sleep(0.05)
    assert Pump.instances == []
    await registry.command("tagging", "resume")
    await until(lambda: Pump.instances)


async def test_the_queue_drains_by_itself_after_a_restart(env):
    proc, registry, db, *_ = env
    db.queued = 7
    assert await proc.kick_if_queued()
    await until(lambda: Pump.instances and Pump.instances[0].passes)


async def test_a_persisted_owner_pause_keeps_it_paused_at_startup(env, tmp_path):
    proc, registry, db, client, busy, clock = env
    await registry.command("tagging", "pause")
    # A new process after a restart reads the persisted intent.
    registry2 = ProcessRegistry(tmp_path / "bg.json", lambda s: None, clock)
    proc2 = TaggingProcess(registry2, lambda: client, lambda: db, lambda: False)
    registry2.register(proc2)
    db.queued = 4
    assert await proc2.kick_if_queued() is False
    await registry2.settle()
    assert registry2.status("tagging").state == State.PAUSED
    assert Pump.instances == []


async def test_nothing_queued_at_startup_starts_nothing(env):
    proc, *_ = env
    assert await proc.kick_if_queued() is False
    assert Pump.instances == []


async def test_owner_unload_pauses_and_a_hold_release_does_not_resume(env):
    proc, registry, db, *_ = env
    db.queued = 3
    await proc.owner_unloaded()
    st = registry.status("tagging")
    assert st.state == State.PAUSED
    assert st.activity == "Paused: model unloaded"
    with registry.hold(["tagging"], by="Text to image"):
        await registry.settle()
    await registry.settle()
    await asyncio.sleep(0.05)
    assert registry.status("tagging").state == State.PAUSED
    assert Pump.instances == []
    # Only the owner's resume brings it back.
    await registry.command("tagging", "resume")
    await until(lambda: Pump.instances)
    assert registry.status("tagging").activity != "Paused: model unloaded"


async def test_a_render_unload_resumes_at_release_with_fresh_stops(env):
    proc, registry, db, client, *_ = env
    db.queued = 3
    with registry.hold(["tagging"], by="Text to image"):
        await registry.settle()
        assert registry.status("tagging").state == State.HELD
        client._explicit_stops = 9  # the render unloaded the VLM
        assert proc.kick() is False  # held: nothing starts
    await registry.settle()
    await until(lambda: Pump.instances)
    assert Pump.instances[0].kw["unless_stopped_since"] == 9


async def test_a_chat_request_holds_for_the_grace_then_releases(env):
    proc, registry, db, client, busy, clock = env
    with dependencies.interactive_vlm_hold("Chat"):
        await registry.settle()
        st = registry.status("tagging")
        assert st.state == State.HELD
        assert st.held_by == ["Chat"]
    clock.t = INTERACTIVE_GRACE_S - 1
    registry.tick()
    assert registry.status("tagging").state == State.HELD
    clock.t = INTERACTIVE_GRACE_S + 1
    registry.tick()
    await registry.settle()
    assert registry.status("tagging").state != State.HELD


async def test_the_clip_only_tier_is_idle_and_says_so(env, monkeypatch):
    proc, registry, db, *_ = env
    db.queued = 2
    monkeypatch.setattr(tagging_mod, "preferred_vlm_model_id", lambda hw: None)
    monkeypatch.setattr(tagging_mod, "vlm_model_installed", lambda m: False)
    proc.kick()
    await until(lambda: registry.status("tagging").activity.startswith("CLIP"))
    st = registry.status("tagging")
    assert st.state == State.IDLE
    assert st.activity == "CLIP tagging is in use"
    assert Pump.instances == []


async def test_rows_left_after_a_drain_mean_waiting_then_a_timer_retries(env):
    proc, registry, db, *_ = env
    db.queued = 3  # the pump leaves them: the VLM is not ready
    proc.kick()
    await until(lambda: registry.status("tagging").state == State.WAITING)
    assert "Waiting" in registry.status("tagging").activity
    await until(lambda: len(Pump.instances) >= 2)  # the 0.05 s retry
    db.queued = 0
    await until(lambda: registry.status("tagging").state == State.IDLE)


async def test_overlapping_kicks_coalesce_into_one_rerun(env):
    proc, registry, db, *_ = env
    db.queued = 1
    Pump.gate = asyncio.Event()
    proc.kick()
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    proc.kick()
    proc.kick()
    await asyncio.sleep(0.05)
    db.queued = 0
    Pump.gate.set()
    await until(lambda: Pump.instances[0].passes == 2)
    await asyncio.sleep(0.05)
    assert len(Pump.instances) == 1 and Pump.instances[0].passes == 2


async def test_a_kick_from_a_worker_thread_runs_on_the_loop(env):
    proc, registry, db, *_ = env
    db.queued = 1
    seen: list = []
    Pump.on_pass = lambda p: seen.append(threading.current_thread().name)
    assert await asyncio.to_thread(proc.kick)
    await until(lambda: seen)
    assert seen == [threading.main_thread().name]


async def test_the_drain_waits_for_generation_to_settle(env):
    proc, registry, db, client, busy, _ = env
    db.queued = 1
    busy["on"] = True
    proc.kick()
    await asyncio.sleep(0.1)
    assert Pump.instances == []
    st = registry.status("tagging")
    assert (st.state, st.activity) == (State.WAITING, "Waiting for image generation")
    client._explicit_stops = 5  # the render unloaded the VLM
    busy["on"] = False
    await until(lambda: Pump.instances)
    assert Pump.instances[0].kw["unless_stopped_since"] == 5


async def test_a_failing_busy_probe_defers(env):
    proc, registry, db, client, busy, _ = env
    db.queued = 1

    def boom():
        raise RuntimeError("probe down")

    proc._generation_busy = boom
    proc.kick()
    await asyncio.sleep(0.05)
    assert Pump.instances == []
    proc._generation_busy = lambda: False
    await until(lambda: Pump.instances)


async def test_the_scan_snapshot_guards_the_post_scan_drain(env):
    proc, registry, db, client, *_ = env
    db.queued = 1
    stops = (client, 4)
    client._explicit_stops = 5  # an unload landed after the scan began
    proc.kick(scan_stops=stops)
    await until(lambda: Pump.instances)
    kw = Pump.instances[0].kw
    assert kw["unless_stopped_since"] == 4
    assert kw["client"] is client and kw["model_id"] == "m"


async def test_a_drain_failure_is_failed_with_the_type_only(env, caplog):
    proc, registry, db, *_ = env
    db.queued = 1

    def boom(p):
        raise RuntimeError("secret /path/x.jpg")

    Pump.on_pass = boom
    with caplog.at_level(logging.ERROR):
        proc.kick()
        await until(lambda: registry.status("tagging").state == State.FAILED)
    st = registry.status("tagging")
    assert st.error == "Tagging failed: RuntimeError"
    assert "secret" not in st.activity


def test_a_kick_without_a_loop_warns(env, caplog):
    proc, registry, *_ = env
    proc._loop = None
    with caplog.at_level(logging.WARNING, logger=tagging_mod.logger.name):
        assert proc.kick() is False
    assert any("drain" in r.message.lower() for r in caplog.records)


async def test_aclose_cancels_an_inflight_drain(env):
    proc, registry, db, *_ = env
    db.queued = 1
    Pump.gate = asyncio.Event()
    proc.kick()
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    await proc.aclose()
    await until(lambda: not proc._running)


async def test_can_stop_is_false_when_idle_and_empty(env):
    proc, registry, db, *_ = env
    assert registry.status("tagging").capabilities.can_stop is False
    db.queued = 3
    await proc._refresh_queue()
    assert registry.status("tagging").capabilities.can_stop is True


async def test_stop_mid_drain_winds_down_and_leaves_the_rows(env):
    proc, registry, db, *_ = env
    db.queued = 5
    Pump.gate = asyncio.Event()
    proc.kick()
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    await registry.command("tagging", "stop")
    st = registry.status("tagging")
    assert st.state == State.RUNNING  # winding down, holds kept
    assert st.activity == "Stopping after the current images"
    assert Pump.instances[0].paused is True
    Pump.gate.set()
    await until(lambda: registry.status("tagging").state == State.STOPPED)
    await asyncio.sleep(0.1)
    assert len(Pump.instances) == 1 and Pump.instances[0].passes == 1
    assert db.queued == 5  # nothing dropped


async def test_nothing_runs_a_stopped_tagging(env, tmp_path):
    proc, registry, db, client, busy, clock = env
    db.queued = 4
    await proc._refresh_queue()
    await registry.command("tagging", "stop")
    assert proc.kick() is False  # an embedding run's kick
    assert proc.kick(fresh=True) is False
    proc._arm_retry()  # a retry timer that was armed
    await asyncio.sleep(0.15)
    assert Pump.instances == []
    # A restart: the persisted stop keeps the startup kick off.
    registry2 = ProcessRegistry(tmp_path / "bg.json", lambda s: None, clock)
    proc2 = TaggingProcess(registry2, lambda: client, lambda: db, lambda: False)
    registry2.register(proc2)
    assert await proc2.kick_if_queued() is False
    await asyncio.sleep(0.05)
    assert registry2.status("tagging").state == State.STOPPED
    assert Pump.instances == []
    # Rows keep accumulating and show on the panel.
    db.queued = 9
    proc2.kick()
    await until(lambda: "9 queued" in registry2.status("tagging").activity)


async def test_play_drains_the_rest_after_a_stop(env):
    proc, registry, db, *_ = env
    db.queued = 3
    await proc._refresh_queue()
    await registry.command("tagging", "stop")
    await registry.command("tagging", "start")
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    assert registry.status("tagging").state != State.STOPPED


async def test_an_owner_retag_clears_a_stop_but_not_a_pause(env):
    proc, registry, db, *_ = env
    db.queued = 3
    await proc._refresh_queue()
    await registry.command("tagging", "stop")
    assert proc.kick(fresh=True, by_owner=True)
    await until(lambda: Pump.instances and Pump.instances[0].passes)
    assert registry.intent("tagging") == "none"
    await registry.command("tagging", "pause")
    n = len(Pump.instances)
    assert proc.kick(fresh=True, by_owner=True) is False
    await asyncio.sleep(0.05)
    assert registry.status("tagging").state == State.PAUSED
    assert len(Pump.instances) == n


async def test_hold_end_before_resume_never_shows_paused_with_no_intent(env):
    proc, registry, db, *_ = env
    db.queued = 3
    with registry.hold(["tagging"], by="Text to image"):
        await registry.settle()
        assert registry.status("tagging").state == State.HELD
    # The hold is gone; Tagging's own flag may still say paused for a moment.
    st = registry.status("tagging")
    assert st.state != State.PAUSED and st.held_by == []
    await registry.settle()
    assert registry.status("tagging").state != State.PAUSED


async def test_can_start_only_with_something_to_start(env):
    proc, registry, db, *_ = env
    caps = registry.status("tagging").capabilities
    assert (caps.can_start, caps.can_pause) == (False, True)
    db.queued = 3
    await proc._refresh_queue()
    assert registry.status("tagging").capabilities.can_start is True
    db.queued = 0
    await proc._refresh_queue()
    await registry.command("tagging", "pause")
    assert registry.status("tagging").capabilities.can_start is True
