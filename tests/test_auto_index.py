"""Debounced incremental index build after T2I ingests, so fresh
generations get CLIP vectors (and then taste scores) without a manual
"Build Index" click."""

import asyncio
from typing import List

from backend.services.auto_index import AutoIndexScheduler

DELAY = 0.05


class Env:
    def __init__(self) -> None:
        self.builds: List[float] = []
        self.indexing = False
        self.busy = False
        self.gate: "asyncio.Event | None" = None

    async def build(self) -> None:
        self.builds.append(asyncio.get_running_loop().time())
        if self.gate is not None:
            await self.gate.wait()

    def scheduler(self) -> AutoIndexScheduler:
        return AutoIndexScheduler(
            start_build=self.build,
            is_indexing=lambda: self.indexing,
            is_busy=lambda: self.busy,
            delay_s=DELAY,
        )


async def test_ingest_schedules_one_build_after_the_debounce():
    env = Env()
    s = env.scheduler()
    t0 = asyncio.get_running_loop().time()
    s.on_t2i_event("t2i", "t2i_images_changed", {"files": ["/a.png"]})
    await asyncio.sleep(DELAY / 2)
    assert env.builds == []
    await asyncio.sleep(DELAY * 2)
    assert len(env.builds) == 1
    assert env.builds[0] - t0 >= DELAY
    await s.aclose()


async def test_other_events_do_not_schedule():
    env = Env()
    s = env.scheduler()
    s.on_t2i_event("t2i", "batch_progress", {})
    s.on_t2i_event("comfy", "t2i_images_changed", {})
    await asyncio.sleep(DELAY * 3)
    assert env.builds == []
    await s.aclose()


async def test_repeated_ingests_coalesce_and_restart_the_debounce():
    env = Env()
    s = env.scheduler()
    for _ in range(5):
        s.notify()
        await asyncio.sleep(DELAY / 3)
    # Still inside the debounce of the last notify.
    assert env.builds == []
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 1
    await s.aclose()


async def test_no_build_while_indexing_then_rearms():
    env = Env()
    env.indexing = True
    s = env.scheduler()
    s.notify()
    await asyncio.sleep(DELAY * 4)
    assert env.builds == []
    env.indexing = False
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 1
    await s.aclose()


async def test_no_build_while_a_batch_is_active_then_rearms():
    env = Env()
    env.busy = True
    s = env.scheduler()
    s.notify()
    await asyncio.sleep(DELAY * 4)
    assert env.builds == []
    env.busy = False
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 1
    await s.aclose()


async def test_ingest_during_a_build_schedules_one_more():
    env = Env()
    env.gate = asyncio.Event()
    s = env.scheduler()
    s.notify()
    await asyncio.sleep(DELAY * 2)
    assert len(env.builds) == 1
    s.notify()  # new image arrives while the first build is starting
    s.notify()
    env.gate.set()
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 2
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 2
    await s.aclose()


async def test_build_errors_are_swallowed():
    env = Env()

    async def boom() -> None:
        env.builds.append(0.0)
        raise RuntimeError("409")

    s = AutoIndexScheduler(
        start_build=boom,
        is_indexing=lambda: False,
        is_busy=lambda: False,
        delay_s=DELAY,
    )
    s.notify()
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 1
    s.notify()
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 2
    await s.aclose()


async def test_busy_probe_errors_count_as_busy_and_never_crash():
    env = Env()
    calls = {"n": 0}

    def flaky() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise AttributeError("process vanished")
        return False

    s = AutoIndexScheduler(
        start_build=env.build,
        is_indexing=flaky,
        is_busy=lambda: False,
        delay_s=DELAY,
    )
    s.notify()
    await asyncio.sleep(DELAY * 4)
    assert len(env.builds) == 1
    await s.aclose()


async def test_aclose_cancels_a_pending_build():
    env = Env()
    s = env.scheduler()
    s.notify()
    await s.aclose()
    await asyncio.sleep(DELAY * 3)
    assert env.builds == []
    s.notify()  # after close: ignored
    await asyncio.sleep(DELAY * 3)
    assert env.builds == []


async def test_notify_from_another_thread_posts_onto_the_loop():
    env = Env()
    s = env.scheduler()
    await asyncio.to_thread(s.notify)
    await asyncio.sleep(DELAY * 3)
    assert len(env.builds) == 1
    await s.aclose()


async def test_generation_busy_reads_t2i_batches_and_comfy_jobs(tmp_path):
    from backend.services.auto_index import generation_busy
    from metascan.core.comfy_client import ComfyClient

    class T2i:
        rows: list = []

        def active_batches(self) -> list:
            return self.rows

    comfy = ComfyClient(base_url="http://127.0.0.1:1", output_root=tmp_path, db=None)
    t2i = T2i()
    try:
        assert generation_busy(t2i, comfy) is False
        t2i.rows = [{"batch_id": "b"}]
        assert generation_busy(t2i, comfy) is True
        t2i.rows = []
        comfy._queue.append(7)
        assert generation_busy(t2i, comfy) is True
        comfy._queue.clear()
        comfy._running.add(8)
        assert generation_busy(t2i, comfy) is True
        comfy._running.clear()
        assert generation_busy(t2i, comfy) is False
    finally:
        await comfy.aclose()


async def test_generation_busy_counts_a_writing_i2v_run():
    from backend.services.auto_index import generation_busy

    class I2v:
        prompting = False

        def is_prompting(self) -> bool:
            return self.prompting

    i2v = I2v()
    assert generation_busy(None, None, i2v) is False
    i2v.prompting = True
    assert generation_busy(None, None, i2v) is True
