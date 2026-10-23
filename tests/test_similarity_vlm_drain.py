"""The post-embedding VLM drain must run on the server loop even though
EmbeddingQueue.on_complete fires from a worker thread (asyncio.to_thread)."""

import asyncio
import logging
import threading

import pytest

from backend.api import similarity


class _FakeQueue:
    index_dir = "/nonexistent-index-dir"


def _install_fakes(monkeypatch, calls):
    class FakePump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            calls.append(threading.current_thread().name)
            await asyncio.sleep(0)
            return 3

    import backend.api.vlm as vlm_api
    import backend.services.vlm_tag_pump as pump_mod

    monkeypatch.setattr(vlm_api, "get_vlm_client", lambda: object())
    monkeypatch.setattr(pump_mod, "VlmTagPump", FakePump)
    monkeypatch.setattr(similarity, "preferred_vlm_model_id", lambda hw: "m")
    monkeypatch.setattr(similarity, "detect_hardware", lambda: None)
    monkeypatch.setattr(similarity, "get_db", lambda: object())


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(similarity, "_server_loop", None, raising=False)
    yield
    similarity.set_server_loop(None)


async def test_drain_scheduled_from_worker_thread(monkeypatch):
    calls: list = []
    _install_fakes(monkeypatch, calls)
    loop = asyncio.get_running_loop()
    similarity.set_server_loop(loop)

    # on_complete runs off-loop, exactly like eq.poll_updates via to_thread.
    await asyncio.to_thread(similarity._schedule_vlm_drain_after_complete, _FakeQueue())
    for _ in range(20):
        if calls:
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 1
    assert calls[0] == threading.main_thread().name  # ran on the loop thread


async def test_drain_does_not_overlap(monkeypatch):
    running = 0
    peak = 0
    done = 0

    class FakePump:
        def __init__(self, **kw):
            pass

        async def drain_once(self):
            nonlocal running, peak, done
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            done += 1
            return 1

    import backend.api.vlm as vlm_api
    import backend.services.vlm_tag_pump as pump_mod

    _install_fakes(monkeypatch, [])
    monkeypatch.setattr(pump_mod, "VlmTagPump", FakePump)
    monkeypatch.setattr(vlm_api, "get_vlm_client", lambda: object())
    similarity.set_server_loop(asyncio.get_running_loop())
    for _ in range(2):
        await asyncio.to_thread(
            similarity._schedule_vlm_drain_after_complete, _FakeQueue()
        )
    await asyncio.sleep(0.4)
    assert peak == 1
    assert done == 1  # overlapping request is skipped


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
