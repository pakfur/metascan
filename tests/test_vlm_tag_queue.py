"""The durable VLM tag queue: v8 migration, queue methods, pump behaviour."""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from backend.services.vlm_tag_pump import VlmTagPump
from metascan.core.database_sqlite import (
    MAX_TAG_ATTEMPTS,
    DatabaseManager,
    MigrationError,
)
from metascan.utils.path_utils import to_posix_path
from unittest.mock import AsyncMock

from tests.test_vlm_tag_pump import make_client, make_db


def _make_v7(tmp_path: Path, names):
    """A database at version 7 (no queue table) holding media rows."""
    db, paths = make_db(tmp_path, names=names, queue=False)
    with sqlite3.connect(str(db.db_file)) as conn:
        conn.execute("DROP TABLE vlm_tag_queue")
        conn.execute("PRAGMA user_version = 7")
    return paths


def _write_pending(tmp_path: Path, lines):
    d = tmp_path / "db" / "similarity"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "vlm_pending.jsonl"
    f.write_text("".join(lines))
    return f


def test_v8_imports_leftover_lines_dedupes_and_removes_the_file(tmp_path):
    a, b = _make_v7(tmp_path, ("a.jpg", "b.jpg"))
    f = _write_pending(
        tmp_path,
        [
            json.dumps({"path": str(a)}) + "\n",
            json.dumps({"path": str(a)}) + "\n",  # duplicate
            json.dumps({"path": str(tmp_path / "gone.jpg")}) + "\n",  # no media row
            "not json\n",
        ],
    )
    db = DatabaseManager(tmp_path / "db")
    assert db.next_tag_batch(10) == [to_posix_path(a)]
    assert not f.exists()


def test_v8_runs_once_and_counts_line_is_debug_when_empty(tmp_path, caplog):
    _make_v7(tmp_path, ("a.jpg",))
    with caplog.at_level(logging.DEBUG, logger="metascan.core.database_sqlite"):
        db = DatabaseManager(tmp_path / "db")
    rec = [r for r in caplog.records if "version 8" in r.getMessage()]
    assert rec and rec[0].levelno == logging.DEBUG
    # Runs once: a file appearing later is not imported on a reopen.
    f = _write_pending(tmp_path, [json.dumps({"path": str(tmp_path / "a.jpg")}) + "\n"])
    DatabaseManager(tmp_path / "db")
    assert db.tag_queue_count() == 0 and f.exists()


def test_v8_failure_raises_migration_error_and_keeps_version(tmp_path, monkeypatch):
    _make_v7(tmp_path, ("a.jpg",))
    f = _write_pending(tmp_path, ["x\n"])
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(MigrationError):
        DatabaseManager(tmp_path / "db")
    monkeypatch.undo()
    assert f.exists()
    with sqlite3.connect(str(tmp_path / "db" / "metascan.db")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7


async def test_row_survives_until_tags_are_written(tmp_path):
    db, (a, _b) = make_db(tmp_path)
    started = asyncio.Event()

    async def hang(_p):
        started.set()
        await asyncio.sleep(60)

    client = make_client(side_effect=hang)
    pump = VlmTagPump(client, db, model_id="m", concurrency=1)
    task = asyncio.create_task(pump.drain_once())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()  # the process dies mid-call
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.tag_queue_count() == 2


async def test_unload_mid_drain_keeps_queue_and_counts_no_failure(tmp_path):
    from metascan.core.vlm_client import VlmNotReady

    names = tuple(f"{i}.jpg" for i in range(6))
    db, _ = make_db(tmp_path, names=names)
    calls = {"n": 0}

    async def gen(_p):
        calls["n"] += 1
        if calls["n"] > 2:
            raise VlmNotReady("VlmClient not ready (state=stopped)")
        return ["t"]

    client = make_client(side_effect=gen)
    pump = VlmTagPump(client, db, model_id="m", concurrency=1)
    await pump.drain_once()
    assert db.tag_queue_count() == 4
    with sqlite3.connect(str(db.db_file)) as conn:
        assert (
            conn.execute("SELECT MAX(attempts) FROM vlm_tag_queue").fetchone()[0] == 0
        )


async def test_unloaded_error_from_a_re_ensure_also_stops_quietly(tmp_path):
    from metascan.core.vlm_client import VlmError

    db, _ = make_db(tmp_path)
    client = make_client(side_effect=VlmError("VLM was unloaded; not bringing it back"))
    await VlmTagPump(client, db, model_id="m", concurrency=1).drain_once()
    assert db.tag_queue_count() == 2


async def test_missing_file_is_removed_without_a_failure(tmp_path):
    db, _ = make_db(tmp_path, names=("a.jpg",))
    client = make_client(side_effect=FileNotFoundError("a.jpg"))
    pump = VlmTagPump(client, db, model_id="m")
    await pump.drain_once()
    assert db.tag_queue_count() == 0


async def test_third_failure_removes_row_and_drain_does_not_spin(tmp_path):
    db, _ = make_db(tmp_path, names=("a.jpg", "b.jpg"))
    client = make_client(side_effect=ValueError("bad image"))
    pump = VlmTagPump(client, db, model_id="m", concurrency=1)
    await pump.drain_once()
    assert client.generate_tags.await_count == 2  # once each in a pass
    assert db.tag_queue_count() == 2
    for _ in range(MAX_TAG_ATTEMPTS - 1):
        await pump.drain_once()
    assert db.tag_queue_count() == 0
    assert client.generate_tags.await_count == 2 * MAX_TAG_ATTEMPTS


async def test_pause_finishes_in_flight_and_takes_no_more(tmp_path):
    db, _ = make_db(tmp_path, names=tuple(f"{i}.jpg" for i in range(5)))
    pump: VlmTagPump

    async def gen(_p):
        pump.pause()
        return ["t"]

    client = make_client(side_effect=gen)
    pump = VlmTagPump(client, db, model_id="m", concurrency=1)
    await pump.drain_once()
    assert client.generate_tags.await_count == 1
    assert db.tag_queue_count() == 4
    pump.resume()
    client.generate_tags = make_client(return_value=["t"]).generate_tags
    await pump.drain_once()
    assert db.tag_queue_count() == 0
    assert pump.progress().total >= pump.progress().done


def test_deleting_media_removes_queue_row(tmp_path):
    db, (a, b) = make_db(tmp_path)
    db.delete_media(a)
    assert db.next_tag_batch(10) == [to_posix_path(b)]


def test_enqueue_skips_unknown_and_duplicates(tmp_path):
    db, (a, _b) = make_db(tmp_path, queue=False)
    assert db.enqueue_tags([a, a, tmp_path / "nope.jpg"]) == 1
    assert db.enqueue_tags([a]) == 0


def test_retag_all_clip_enqueues_images_with_no_vlm_tags(tmp_path):
    db, (a, b) = make_db(tmp_path, queue=False)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    from datetime import datetime

    from metascan.core.media import Media

    db.save_media(
        Media(
            file_path=v,
            file_size=0,
            width=1,
            height=1,
            format="mp4",
            created_at=datetime(2024, 1, 1),
            modified_at=datetime(2024, 1, 1),
        )
    )
    db.add_tag_indices(a, ["cat"], "vlm")
    db.add_tag_indices(b, ["dog"], "clip")
    assert db.images_without_vlm_tags() == [to_posix_path(b)]


def test_embedding_worker_enqueues_images_only(tmp_path):
    from metascan.workers.embedding_worker import EmbeddingWorker

    db, (a, _b) = make_db(tmp_path, queue=False)
    w = EmbeddingWorker.__new__(EmbeddingWorker)
    w.logger = logging.getLogger("t")
    w._enqueue_for_vlm(db, str(a), is_video=False)
    w._enqueue_for_vlm(db, str(tmp_path / "b.jpg"), is_video=True)
    assert db.next_tag_batch(10) == [to_posix_path(a)]


def _attempts(db):
    with sqlite3.connect(str(db.db_file)) as conn:
        return conn.execute(
            "SELECT COALESCE(MAX(attempts), 0) FROM vlm_tag_queue"
        ).fetchone()[0]


async def test_gpu_busy_leaves_the_queue_unchanged_with_no_failure(tmp_path):
    from metascan.core.gpu_guard import GPU_BUSY_MESSAGE
    from metascan.core.vlm_client import VlmNotReady

    db, _ = make_db(tmp_path)
    client = make_client(side_effect=VlmNotReady(GPU_BUSY_MESSAGE))
    await VlmTagPump(client, db, model_id="m", concurrency=1).drain_once()
    assert db.tag_queue_count() == 2 and _attempts(db) == 0
    # ... and the same from ensure_started: the drain defers quietly.
    client2 = make_client(return_value=["t"])
    client2.ensure_started = AsyncMock(side_effect=VlmNotReady(GPU_BUSY_MESSAGE))
    assert await VlmTagPump(client2, db, model_id="m").drain_once() == 0
    assert db.tag_queue_count() == 2 and _attempts(db) == 0


async def test_stopped_real_client_raises_vlm_not_ready(tmp_path):
    from metascan.core.vlm_client import VlmClient, VlmNotReady

    client = VlmClient(spawn_override=lambda mid: "http://127.0.0.1:1")
    with pytest.raises(VlmNotReady):
        await client.generate_tags(tmp_path / "x.png")


async def test_in_flight_failure_while_gpu_reserved_counts_no_failure(tmp_path):
    from metascan.core import gpu_guard
    from metascan.core.vlm_client import VlmError

    db, _ = make_db(tmp_path)
    client = make_client(side_effect=VlmError("llama-server request failed: eof"))
    gpu_guard.set_gpu_reservation(lambda: True)
    try:
        await VlmTagPump(client, db, model_id="m", concurrency=1).drain_once()
    finally:
        gpu_guard.set_gpu_reservation(None)
    assert db.tag_queue_count() == 2 and _attempts(db) == 0


async def test_empty_tags_from_a_server_that_died_keep_the_row(tmp_path):
    db, _ = make_db(tmp_path, names=("a.jpg",))
    client = make_client(return_value=[])
    client.state = "stopped"
    await VlmTagPump(client, db, model_id="m").drain_once()
    assert db.tag_queue_count() == 1
