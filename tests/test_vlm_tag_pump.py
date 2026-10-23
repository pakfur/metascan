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
