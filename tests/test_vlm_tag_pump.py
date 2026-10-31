"""VlmTagPump drains the durable vlm_tag_queue table into VlmClient."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.vlm_tag_pump import VlmTagPump
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def make_db(tmp_path: Path, names=("a.jpg", "b.jpg"), queue=True):
    db = DatabaseManager(tmp_path / "db")
    paths = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"\xff\xd8\xff\xd9")
        db.save_media(
            Media(
                file_path=p,
                file_size=4,
                width=1,
                height=1,
                format="jpeg",
                created_at=datetime(2024, 1, 1),
                modified_at=datetime(2024, 1, 1),
            )
        )
        paths.append(p)
    if queue:
        db.enqueue_tags(paths)
    return db, paths


def make_client(**kw):
    client = MagicMock()
    client.ensure_started = AsyncMock()
    client.generate_tags = AsyncMock(**kw)
    client.model_id = "qwen3vl-4b"
    return client


async def test_pump_processes_queue(tmp_path):
    db, _ = make_db(tmp_path)
    client = make_client(side_effect=[["x"], ["y"]])
    pump = VlmTagPump(client, db, model_id="qwen3vl-4b")
    assert await pump.drain_once() == 2
    assert client.generate_tags.await_count == 2
    assert db.tag_queue_count() == 0


async def test_pump_handles_empty_queue_gracefully(tmp_path):
    db, _ = make_db(tmp_path, names=(), queue=False)
    client = make_client()
    pump = VlmTagPump(client, db, model_id="qwen3vl-4b")
    assert await pump.drain_once() == 0
    client.ensure_started.assert_not_awaited()
    client.generate_tags.assert_not_called()


async def test_failed_start_keeps_batch_queued(tmp_path):
    db, _ = make_db(tmp_path)
    client = make_client()
    client.ensure_started = AsyncMock(side_effect=RuntimeError("no llama-server"))
    pump = VlmTagPump(client, db, model_id="m")
    with pytest.raises(RuntimeError):
        await pump.drain_once()
    assert db.tag_queue_count() == 2
    client.generate_tags.assert_not_awaited()


async def test_rows_queued_during_drain_are_drained_too(tmp_path):
    db, _ = make_db(tmp_path, names=("a.jpg", "late.jpg"), queue=False)
    db.enqueue_tags([tmp_path / "a.jpg"])

    async def start(_mid):
        db.enqueue_tags([tmp_path / "late.jpg"])  # the worker queues mid-start

    client = make_client(return_value=["x"])
    client.ensure_started = AsyncMock(side_effect=start)
    pump = VlmTagPump(client, db, model_id="m")
    await pump.drain_once()
    assert client.generate_tags.await_count == 2
    assert db.tag_queue_count() == 0


async def test_drain_does_not_restart_a_vlm_unloaded_since_the_scan(tmp_path):
    """A background drain must not bring back a VLM explicitly unloaded
    (ComfyUI render, /unload) after the scan began; the batch stays queued."""
    from metascan.core.vlm_client import VlmClient

    spawned: list = []

    def spawn(mid):
        spawned.append(mid)
        return "http://127.0.0.1:1"

    client = VlmClient(spawn_override=spawn)
    since = client._explicit_stops
    await client.shutdown()
    db, _ = make_db(tmp_path)
    pump = VlmTagPump(client, db, model_id="qwen3vl-4b", unless_stopped_since=since)
    assert await pump.drain_once() == 0
    assert spawned == []
    assert db.tag_queue_count() == 2


async def test_drain_with_since_starts_when_no_unload_landed(tmp_path):
    db, _ = make_db(tmp_path, names=("a.jpg",))
    client = make_client(return_value=["x"])
    client._explicit_stops = 3
    pump = VlmTagPump(client, db, model_id="m", unless_stopped_since=3)
    assert await pump.drain_once() == 1
    client.ensure_started.assert_awaited_once_with("m", unless_stopped_since=3)
    assert db.tag_queue_count() == 0
