"""VlmTagPump drains the embedding worker's vlm_pending.jsonl into VlmClient."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.vlm_tag_pump import VlmTagPump


async def test_pump_processes_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        queue_dir = Path(tmp)
        a = queue_dir / "a.jpg"
        a.write_bytes(b"\xff\xd8\xff\xd9")
        b = queue_dir / "b.jpg"
        b.write_bytes(b"\xff\xd8\xff\xd9")
        pending = queue_dir / "vlm_pending.jsonl"
        pending.write_text(
            json.dumps({"path": str(a)}) + "\n" + json.dumps({"path": str(b)}) + "\n"
        )
        client = MagicMock()
        client.ensure_started = AsyncMock()
        client.generate_tags = AsyncMock(side_effect=[["x"], ["y"]])
        client.model_id = "qwen3vl-4b"

        db = MagicMock()
        pump = VlmTagPump(queue_dir, client, db, model_id="qwen3vl-4b")
        await pump.drain_once()

        assert client.generate_tags.await_count == 2
        assert db.add_tag_indices.call_count == 2
        assert pending.read_text() == ""


async def test_pump_handles_missing_file_gracefully():
    with tempfile.TemporaryDirectory() as tmp:
        queue_dir = Path(tmp)
        client = MagicMock()
        client.ensure_started = AsyncMock()
        db = MagicMock()
        pump = VlmTagPump(queue_dir, client, db, model_id="qwen3vl-4b")
        await pump.drain_once()
        client.generate_tags.assert_not_called()


async def test_failed_start_keeps_batch_queued(tmp_path):
    pending = tmp_path / "vlm_pending.jsonl"
    body = json.dumps({"path": str(tmp_path / "a.jpg")}) + "\n"
    pending.write_text(body)
    client = MagicMock()
    client.ensure_started = AsyncMock(side_effect=RuntimeError("no llama-server"))
    client.generate_tags = AsyncMock()
    pump = VlmTagPump(tmp_path, client, MagicMock(), model_id="m")

    with pytest.raises(RuntimeError):
        await pump.drain_once()
    assert pending.read_text() == body
    client.generate_tags.assert_not_awaited()


async def test_lines_appended_during_drain_survive(tmp_path):
    a = tmp_path / "a.jpg"
    pending = tmp_path / "vlm_pending.jsonl"
    pending.write_text(json.dumps({"path": str(a)}) + "\n")
    late = json.dumps({"path": str(tmp_path / "late.jpg")}) + "\n"

    async def start(_mid):
        with pending.open("a", encoding="utf-8") as f:  # worker appends mid-start
            f.write(late)

    client = MagicMock()
    client.ensure_started = AsyncMock(side_effect=start)
    client.generate_tags = AsyncMock(return_value=["x"])
    pump = VlmTagPump(tmp_path, client, MagicMock(), model_id="m")
    await pump.drain_once()

    assert client.generate_tags.await_count == 1
    assert pending.read_text() == late


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
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\xff\xd8\xff\xd9")
    pending = tmp_path / "vlm_pending.jsonl"
    line = json.dumps({"path": str(a)}) + "\n"
    pending.write_text(line)

    pump = VlmTagPump(
        tmp_path,
        client,
        MagicMock(),
        model_id="qwen3vl-4b",
        unless_stopped_since=since,
    )
    assert await pump.drain_once() == 0
    assert spawned == []
    assert pending.read_text() == line


async def test_drain_with_since_starts_when_no_unload_landed(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"\xff\xd8\xff\xd9")
    pending = tmp_path / "vlm_pending.jsonl"
    pending.write_text(json.dumps({"path": str(a)}) + "\n")
    client = MagicMock()
    client._explicit_stops = 3
    client.ensure_started = AsyncMock()
    client.generate_tags = AsyncMock(return_value=["x"])
    pump = VlmTagPump(
        tmp_path, client, MagicMock(), model_id="m", unless_stopped_since=3
    )
    assert await pump.drain_once() == 1
    client.ensure_started.assert_awaited_once_with("m", unless_stopped_since=3)
    assert pending.read_text() == ""
