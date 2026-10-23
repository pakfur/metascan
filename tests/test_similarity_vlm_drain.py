"""The post-embedding VLM drain must run on the server loop even though
EmbeddingQueue.on_complete fires from a worker thread (asyncio.to_thread)."""

import asyncio
import logging
import threading

import pytest

from backend.api import similarity


class _FakeQueue:
    index_dir = "/nonexistent-index-dir"


def _install(monkeypatch, pump_cls):
    import backend.api.vlm as vlm_api
    import backend.services.vlm_tag_pump as pump_mod

    monkeypatch.setattr(vlm_api, "get_vlm_client", lambda: object())
    monkeypatch.setattr(pump_mod, "VlmTagPump", pump_cls)
    monkeypatch.setattr(similarity, "preferred_vlm_model_id", lambda hw: "m")
    monkeypatch.setattr(similarity, "detect_hardware", lambda: None)
    monkeypatch.setattr(similarity, "get_db", lambda: object())


def _reset_state():
    similarity.set_server_loop(None)
    similarity.set_generation_busy_probe(None)
    similarity._vlm_drain_running = False
    similarity._vlm_drain_rerun = False
    similarity._vlm_drain_future = None


@pytest.fixture(autouse=True)
def _reset():
    _reset_state()
    yield
    _reset_state()


async def _schedule():
    await asyncio.to_thread(similarity._schedule_vlm_drain_after_complete, _FakeQueue())


async def _until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


async def test_drain_scheduled_from_worker_thread(monkeypatch):
    calls: list = []

    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            calls.append(threading.current_thread().name)
            return 3

    _install(monkeypatch, Pump)
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await _until(lambda: calls)
    assert calls == [threading.main_thread().name]  # ran on the loop thread


async def test_overlapping_request_coalesces_into_one_rerun(monkeypatch):
    running = peak = done = 0
    started = asyncio.Event()
    gate = asyncio.Event()

    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            nonlocal running, peak, done
            running += 1
            peak = max(peak, running)
            started.set()
            await gate.wait()
            running -= 1
            done += 1
            return 1

    _install(monkeypatch, Pump)
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await started.wait()
    # Two requests mid-drain coalesce into a single follow-up pass.
    await _schedule()
    await _schedule()
    await asyncio.sleep(0.05)
    gate.set()
    await _until(lambda: not similarity._vlm_drain_running)
    assert peak == 1
    assert done == 2
    assert similarity._vlm_drain_rerun is False


async def test_drain_failure_logs_and_resets_flags(monkeypatch, caplog):
    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            raise RuntimeError("vlm exploded")

    _install(monkeypatch, Pump)
    similarity.set_server_loop(asyncio.get_running_loop())
    with caplog.at_level(logging.ERROR, logger=similarity.logger.name):
        await _schedule()
        await _until(lambda: caplog.records)
        await _until(lambda: not similarity._vlm_drain_running)
    assert any(
        "drain after scan failed" in r.message and r.exc_info for r in caplog.records
    )
    assert similarity._vlm_drain_rerun is False


async def test_cancel_vlm_drain_cancels_inflight(monkeypatch):
    started = asyncio.Event()

    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            started.set()
            await asyncio.Event().wait()

    _install(monkeypatch, Pump)
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await started.wait()
    similarity.cancel_vlm_drain()
    await _until(lambda: not similarity._vlm_drain_running)


def test_no_loop_attached_warns_not_raises(caplog):
    with caplog.at_level(logging.WARNING, logger=similarity.logger.name):
        similarity._schedule_vlm_drain_after_complete(_FakeQueue())
    assert any("drain" in r.message.lower() for r in caplog.records)


def test_closed_loop_warns_not_raises(caplog):
    loop = asyncio.new_event_loop()
    loop.close()
    similarity.set_server_loop(loop)
    with caplog.at_level(logging.WARNING, logger=similarity.logger.name):
        similarity._schedule_vlm_drain_after_complete(_FakeQueue())
    assert any("drain" in r.message.lower() for r in caplog.records)


async def test_drain_uses_general_client_slots_and_scan_stop_snapshot(monkeypatch):
    """Feature-branch semantics on main's loop-safe drain: the general role's
    client and model, a semaphore sized from parallel_slots, and the
    explicit-stop count captured at scan start."""
    import backend.api.vlm as vlm_api
    import backend.services.vlm_tag_pump as pump_mod
    import metascan.core.vlm_select as vlm_select
    from metascan.core.vlm_models import REGISTRY

    mid = next(iter(REGISTRY))

    class Client:
        model_id = None
        preferred_model_id = mid
        _explicit_stops = 4

    client = Client()
    seen: dict = {}

    class Pump:
        def __init__(self, **kw):
            seen.update(kw)

        async def drain_once(self):
            return 0

    monkeypatch.setattr(vlm_api, "get_vlm_client", lambda: client)
    monkeypatch.setattr(pump_mod, "VlmTagPump", Pump)
    monkeypatch.setattr(vlm_select, "vlm_model_installed", lambda m: True)
    monkeypatch.setattr(similarity, "get_db", lambda: object())
    monkeypatch.setattr(similarity, "_vlm_scan_stops", None)
    similarity._vlm_scan_stops = similarity._capture_vlm_scan_stops()
    client._explicit_stops = 5  # an unload landed after the scan began
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await _until(lambda: seen)
    assert seen["client"] is client
    assert seen["model_id"] == mid
    assert seen["concurrency"] == REGISTRY[mid].parallel_slots
    assert seen["unless_stopped_since"] == 4


async def test_drain_waits_for_generation_to_settle(monkeypatch):
    """No VLM tagging while ComfyUI renders: the drain is deferred while a
    generation is in flight and runs on its own once it settles -- not only
    on the next embedding run."""
    calls: list = []
    busy = {"on": True}

    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            calls.append(busy["on"])
            return 1

    _install(monkeypatch, Pump)
    monkeypatch.setattr(similarity, "_VLM_DRAIN_DEFER_POLL_S", 0.01)
    similarity.set_generation_busy_probe(lambda: busy["on"])
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await asyncio.sleep(0.1)
    assert calls == []
    assert similarity._vlm_drain_running  # deferred, not dropped
    busy["on"] = False
    await _until(lambda: calls)
    assert calls == [False]
    await _until(lambda: not similarity._vlm_drain_running)


async def test_a_deferred_drain_may_reload_after_the_render_unload(monkeypatch):
    """The render's own VLM unload (an explicit stop) happened while the
    drain waited for it; once the generation settled the drain may bring
    the model back (fresh snapshot), instead of leaving the batch queued."""
    import backend.api.vlm as vlm_api
    import backend.services.vlm_tag_pump as pump_mod
    import metascan.core.vlm_select as vlm_select
    from metascan.core.vlm_models import REGISTRY

    mid = next(iter(REGISTRY))

    class Client:
        model_id = None
        preferred_model_id = mid
        _explicit_stops = 4

    client = Client()
    seen: dict = {}
    busy = {"on": True}

    class Pump:
        def __init__(self, **kw):
            seen.update(kw)

        async def drain_once(self):
            return 0

    monkeypatch.setattr(vlm_api, "get_vlm_client", lambda: client)
    monkeypatch.setattr(pump_mod, "VlmTagPump", Pump)
    monkeypatch.setattr(vlm_select, "vlm_model_installed", lambda m: True)
    monkeypatch.setattr(similarity, "get_db", lambda: object())
    monkeypatch.setattr(similarity, "_VLM_DRAIN_DEFER_POLL_S", 0.01)
    monkeypatch.setattr(similarity, "_vlm_scan_stops", None)
    similarity._vlm_scan_stops = similarity._capture_vlm_scan_stops()
    similarity.set_generation_busy_probe(lambda: busy["on"])
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await asyncio.sleep(0.05)
    client._explicit_stops = 5  # the render unloaded the VLM
    busy["on"] = False
    await _until(lambda: seen)
    assert seen["unless_stopped_since"] == 5


async def test_a_failing_busy_probe_defers(monkeypatch):
    calls: list = []
    state = {"fail": True}

    class Pump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            calls.append(1)
            return 1

    def probe():
        if state["fail"]:
            raise RuntimeError("probe down")
        return False

    _install(monkeypatch, Pump)
    monkeypatch.setattr(similarity, "_VLM_DRAIN_DEFER_POLL_S", 0.01)
    similarity.set_generation_busy_probe(probe)
    similarity.set_server_loop(asyncio.get_running_loop())
    await _schedule()
    await asyncio.sleep(0.05)
    assert calls == []
    state["fail"] = False
    await _until(lambda: calls)
