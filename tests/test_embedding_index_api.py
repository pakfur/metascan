"""Tests for the embedding/indexing API (subprocess-based)."""

import asyncio
import pytest

from backend.api import similarity as sim_api


@pytest.fixture(autouse=True)
def reset_embedding_singleton(monkeypatch):
    """Ensure each test starts with a fresh EmbeddingQueue singleton."""
    monkeypatch.setattr(sim_api, "_embedding_queue", None)
    monkeypatch.setattr(sim_api, "_embed_poll_task", None)
    yield


def test_get_embedding_queue_creates_singleton_with_callbacks():
    eq = sim_api._get_embedding_queue()
    assert eq is not None
    assert eq.on_progress is not None
    assert eq.on_complete is not None
    assert eq.on_error is not None
    # Same instance on second call
    assert sim_api._get_embedding_queue() is eq


def test_build_endpoint_calls_start_indexing(monkeypatch):
    from backend.api import similarity as sim_api

    captured = {}

    class FakeEQ:
        def __init__(self):
            self.on_progress = None
            self.on_complete = None
            self.on_error = None
            self.index_dir = type("D", (), {"__truediv__": lambda *a: None})()
            self._indexing = False

        def is_indexing(self):
            return self._indexing

        def start_indexing(
            self, paths, model, device, db_path, compute_phash, kf, tag_with_vlm=False
        ):
            captured["paths"] = paths
            captured["model"] = model
            captured["device"] = device
            captured["db_path"] = db_path
            captured["compute_phash"] = compute_phash
            captured["video_keyframes"] = kf
            captured["tag_with_vlm"] = tag_with_vlm
            self._indexing = True
            return True

        def cancel_indexing(self):
            self._indexing = False

    fake = FakeEQ()
    monkeypatch.setattr(sim_api, "_embedding_queue", fake)
    monkeypatch.setattr(
        sim_api,
        "load_app_config",
        lambda: {
            "similarity": {
                "clip_model": "small",
                "device": "cpu",
                "compute_phash_during_scan": True,
                "video_keyframes": 4,
            }
        },
    )
    monkeypatch.setattr(
        sim_api,
        "get_db",
        lambda: type(
            "D",
            (),
            {
                "clear_embeddings": lambda self: True,
                "get_unembedded_file_paths": lambda self: ["/m/a.png", "/m/b.png"],
            },
        )(),
    )

    async def fake_broadcast(channel, event, data=None):
        pass

    monkeypatch.setattr(sim_api.ws_manager, "broadcast", fake_broadcast)

    result = asyncio.run(sim_api.build_index(rebuild=False))
    assert result["status"] == "started"
    assert result["total"] == 2
    assert captured["paths"] == ["/m/a.png", "/m/b.png"]
    assert captured["model"] == "small"
    assert captured["compute_phash"] is True


def test_build_endpoint_returns_409_if_already_running(monkeypatch):
    from fastapi import HTTPException
    from backend.api import similarity as sim_api

    class FakeEQ:
        on_progress = on_complete = on_error = None

        def is_indexing(self):
            return True

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sim_api.build_index(rebuild=False))
    assert exc.value.status_code == 409


def test_build_endpoint_noop_when_no_unembedded(monkeypatch):
    from backend.api import similarity as sim_api

    class FakeEQ:
        on_progress = on_complete = on_error = None

        def is_indexing(self):
            return False

        def start_indexing(self, *a, **k):
            raise AssertionError("must not be called")

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())
    monkeypatch.setattr(
        sim_api,
        "load_app_config",
        lambda: {"similarity": {}},
    )
    monkeypatch.setattr(
        sim_api,
        "get_db",
        lambda: type(
            "D",
            (),
            {
                "clear_embeddings": lambda self: True,
                "get_unembedded_file_paths": lambda self: [],
            },
        )(),
    )
    broadcasts = []

    async def fake_broadcast(channel, event, data=None):
        broadcasts.append((channel, event, data))

    monkeypatch.setattr(sim_api.ws_manager, "broadcast", fake_broadcast)

    result = asyncio.run(sim_api.build_index(rebuild=False))
    assert result["status"] == "noop"
    assert ("embedding", "complete", {"total": 0}) in broadcasts


def test_cancel_endpoint_calls_cancel_indexing(monkeypatch):
    from backend.api import similarity as sim_api

    cancelled = {"called": False}

    class FakeEQ:
        on_progress = on_complete = on_error = None

        def is_indexing(self):
            return True

        def cancel_indexing(self):
            cancelled["called"] = True

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())
    result = asyncio.run(sim_api.cancel_index_build())
    assert result["status"] == "cancelling"
    assert cancelled["called"] is True


def test_cancel_endpoint_returns_409_when_idle(monkeypatch):
    from fastapi import HTTPException
    from backend.api import similarity as sim_api

    class FakeEQ:
        on_progress = on_complete = on_error = None

        def is_indexing(self):
            return False

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(sim_api.cancel_index_build())
    assert exc.value.status_code == 409


def test_scan_auto_triggers_when_config_present_and_unembedded(monkeypatch):
    from backend.api import scan as scan_api
    from backend.api import similarity as sim_api

    # Config with similarity block + auto_index enabled
    monkeypatch.setattr(
        scan_api,
        "load_app_config",
        lambda: {
            "similarity": {"clip_model": "small", "auto_index_after_scan": True},
        },
    )
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [])
    monkeypatch.setattr(
        scan_api,
        "get_db",
        lambda: type(
            "D",
            (),
            {
                "get_existing_file_paths": lambda self: set(),
                "delete_media_batch": lambda self, paths: 0,
                "get_unembedded_file_paths": lambda self: ["/m/a.png"],
            },
        )(),
    )
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None):
        pass

    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    triggered = {"called": False, "rebuild": None}

    async def fake_build(rebuild=False):
        triggered["called"] = True
        triggered["rebuild"] = rebuild
        return {"status": "started", "total": 1}

    monkeypatch.setattr(sim_api, "build_index", fake_build)

    asyncio.run(scan_api._run_scan(full_cleanup=False))

    assert triggered["called"] is True
    assert triggered["rebuild"] is False


def test_scan_skips_auto_trigger_when_disabled(monkeypatch):
    from backend.api import scan as scan_api
    from backend.api import similarity as sim_api

    monkeypatch.setattr(
        scan_api,
        "load_app_config",
        lambda: {
            "similarity": {"auto_index_after_scan": False},
        },
    )
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [])
    monkeypatch.setattr(
        scan_api,
        "get_db",
        lambda: type(
            "D",
            (),
            {
                "get_existing_file_paths": lambda self: set(),
                "delete_media_batch": lambda self, paths: 0,
                "get_unembedded_file_paths": lambda self: ["/m/a.png"],
            },
        )(),
    )
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None):
        pass

    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    triggered = {"called": False}

    async def fake_build(rebuild=False):
        triggered["called"] = True
        return {"status": "started"}

    monkeypatch.setattr(sim_api, "build_index", fake_build)

    asyncio.run(scan_api._run_scan(full_cleanup=False))
    assert triggered["called"] is False


def test_scan_skips_auto_trigger_when_no_unembedded(monkeypatch):
    from backend.api import scan as scan_api
    from backend.api import similarity as sim_api

    monkeypatch.setattr(
        scan_api,
        "load_app_config",
        lambda: {"similarity": {"auto_index_after_scan": True}},
    )
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [])
    monkeypatch.setattr(
        scan_api,
        "get_db",
        lambda: type(
            "D",
            (),
            {
                "get_existing_file_paths": lambda self: set(),
                "delete_media_batch": lambda self, paths: 0,
                "get_unembedded_file_paths": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None):
        pass

    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    triggered = {"called": False}

    async def fake_build(rebuild=False):
        triggered["called"] = True
        return {"status": "started"}

    monkeypatch.setattr(sim_api, "build_index", fake_build)

    asyncio.run(scan_api._run_scan(full_cleanup=False))
    assert triggered["called"] is False


def test_get_favorite_file_paths_returns_only_favorites(tmp_path):
    """Verify the new helper returns paths where is_favorite=1."""
    from datetime import datetime
    from metascan.core.database_sqlite import DatabaseManager
    from metascan.core.media import Media
    from pathlib import Path as P

    now = datetime.now()
    db = DatabaseManager(tmp_path)
    a = Media(
        file_path=P("/m/a.png"),
        file_size=100,
        width=640,
        height=480,
        format="png",
        created_at=now,
        modified_at=now,
    )
    b = Media(
        file_path=P("/m/b.png"),
        file_size=100,
        width=640,
        height=480,
        format="png",
        created_at=now,
        modified_at=now,
    )
    c = Media(
        file_path=P("/m/c.png"),
        file_size=100,
        width=640,
        height=480,
        format="png",
        created_at=now,
        modified_at=now,
    )
    a.is_favorite = True
    c.is_favorite = True
    db.save_media(a)
    db.save_media(b)
    db.save_media(c)

    favs = db.get_favorite_file_paths()
    assert sorted(favs) == ["/m/a.png", "/m/c.png"]


# Full-clean snapshot/restore is covered against a real DB in
# tests/test_taste_snapshot_scan.py.


def test_poll_updates_noop_when_no_active_worker(tmp_path, caplog):
    """Regression: once the worker completes _cleanup sets _process to None.
    Subsequent poll_updates ticks must NOT re-read the stale progress file and
    must NOT emit warnings or callbacks — otherwise the poll loop spams
    'Worker progress stale' every tick forever (and keeps broadcasting fake
    'progress' WS events to the frontend)."""
    import json
    import logging
    from metascan.core.embedding_queue import EmbeddingQueue

    eq = EmbeddingQueue()
    # Point the queue at a fresh tmp dir so we don't collide with real state
    eq._queue_dir = tmp_path

    # Simulate the aftermath of a successful run: _cleanup has set _process
    # to None, but the completed-status progress file is still on disk.
    eq._process = None
    eq._last_progress = {
        "current": 10,
        "total": 10,
        "status": "complete",
        "timestamp": 0.0,
    }
    eq._last_progress_time = 0.0  # Very old — would trigger stale warning
    stale_file = tmp_path / "progress_embedding.json"
    stale_file.write_text(json.dumps(eq._last_progress))

    progress_calls: list = []
    eq.on_progress = lambda c, t, s: progress_calls.append((c, t, s))
    complete_calls: list = []
    eq.on_complete = lambda total: complete_calls.append(total)
    error_calls: list = []
    eq.on_error = lambda msg: error_calls.append(msg)

    with caplog.at_level(logging.WARNING, logger="metascan.core.embedding_queue"):
        for _ in range(20):
            eq.poll_updates()

    assert progress_calls == [], f"Unexpected progress emits: {progress_calls!r}"
    assert complete_calls == []
    assert error_calls == []
    stale_warnings = [r for r in caplog.records if "stale" in r.getMessage().lower()]
    assert stale_warnings == [], (
        f"poll_updates kept warning after completion: "
        f"{[r.getMessage() for r in stale_warnings]!r}"
    )


@pytest.mark.parametrize("status", ["cancelled", "complete", "error"])
def test_a_finished_run_holds_the_queue_until_its_worker_exits(
    tmp_path, monkeypatch, status
):
    """A cancelled worker still stamps, saves and marks its last batch after
    it writes "cancelled" (any worker still runs when it writes its last
    status). Until the process has exited the queue keeps indexing -- a new
    start is refused, so two workers never write the index at once -- and
    only then reports the end, so the reload it triggers sees the final
    save."""
    import json
    import subprocess
    from types import SimpleNamespace

    from metascan.core import embedding_queue as eq_mod

    class FakeProcess:
        pid = 4242
        stderr = None
        returncode = None

        def __init__(self):
            self.exited = False

        def poll(self):
            return 0 if self.exited else None

        def terminate(self):
            pass

    spawned: list = []

    def popen(*args, **kwargs):
        spawned.append(args)
        return FakeProcess()

    monkeypatch.setattr(
        eq_mod, "subprocess", SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE)
    )
    eq = eq_mod.EmbeddingQueue()
    eq._queue_dir = tmp_path
    worker = FakeProcess()
    eq._process = worker
    events: list = []
    eq.on_complete = lambda total: events.append(("complete", total))
    eq.on_error = lambda msg: events.append(("error", msg))
    (tmp_path / "progress_embedding.json").write_text(
        json.dumps(
            {
                "current": 3,
                "total": 10,
                "status": status,
                "error": "boom" if status == "error" else "",
            }
        )
    )

    for _ in range(3):  # the worker is still finishing its last batch
        eq.poll_updates()
    assert events == [] and eq.is_indexing()
    assert eq.start_indexing(["/lib/new.png"]) is False and spawned == []

    worker.exited = True
    eq.poll_updates()

    assert events == ([("error", "boom")] if status == "error" else [("complete", 3)])
    assert not eq.is_indexing()
    assert eq.start_indexing(["/lib/new.png"]) is True and len(spawned) == 1


def test_a_cancel_whose_last_save_fails_ends_in_error(tmp_path):
    """The worker writes "cancelled", then its final stamp fails and it
    writes "error" before it exits: the run ends with the status it left
    last."""
    import json

    from metascan.core.embedding_queue import EmbeddingQueue

    class FakeProcess:
        returncode = None
        stderr = None

        def __init__(self):
            self.exited = False

        def poll(self):
            return 0 if self.exited else None

    eq = EmbeddingQueue()
    eq._queue_dir = tmp_path
    worker = FakeProcess()
    eq._process = worker
    events: list = []
    eq.on_complete = lambda total: events.append(("complete", total))
    eq.on_error = lambda msg: events.append(("error", msg))
    progress = tmp_path / "progress_embedding.json"
    progress.write_text(json.dumps({"current": 3, "total": 10, "status": "cancelled"}))
    eq.poll_updates()
    progress.write_text(
        json.dumps({"current": 0, "total": 0, "status": "error", "error": "no save"})
    )
    worker.exited = True

    eq.poll_updates()

    assert events == [("error", "no save")]
    assert not eq.is_indexing()
