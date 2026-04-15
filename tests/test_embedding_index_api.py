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
        def is_indexing(self): return self._indexing
        def start_indexing(self, paths, model, device, db_path, compute_phash, kf):
            captured["paths"] = paths
            captured["model"] = model
            captured["device"] = device
            captured["db_path"] = db_path
            captured["compute_phash"] = compute_phash
            captured["video_keyframes"] = kf
            self._indexing = True
            return True
        def cancel_indexing(self):
            self._indexing = False

    fake = FakeEQ()
    monkeypatch.setattr(sim_api, "_embedding_queue", fake)
    monkeypatch.setattr(
        sim_api, "load_app_config",
        lambda: {"similarity": {"clip_model": "small", "device": "cpu", "compute_phash_during_scan": True, "video_keyframes": 4}},
    )
    monkeypatch.setattr(
        sim_api, "get_db",
        lambda: type("D", (), {
            "clear_embeddings": lambda self: True,
            "get_unembedded_file_paths": lambda self: ["/m/a.png", "/m/b.png"],
        })(),
    )

    async def fake_broadcast(channel, event, data=None): pass
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
        def is_indexing(self): return True

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sim_api.build_index(rebuild=False))
    assert exc.value.status_code == 409


def test_build_endpoint_noop_when_no_unembedded(monkeypatch):
    from backend.api import similarity as sim_api

    class FakeEQ:
        on_progress = on_complete = on_error = None
        def is_indexing(self): return False
        def start_indexing(self, *a, **k): raise AssertionError("must not be called")

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())
    monkeypatch.setattr(
        sim_api, "load_app_config",
        lambda: {"similarity": {}},
    )
    monkeypatch.setattr(
        sim_api, "get_db",
        lambda: type("D", (), {
            "clear_embeddings": lambda self: True,
            "get_unembedded_file_paths": lambda self: [],
        })(),
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
        def is_indexing(self): return True
        def cancel_indexing(self): cancelled["called"] = True

    monkeypatch.setattr(sim_api, "_embedding_queue", FakeEQ())
    result = asyncio.run(sim_api.cancel_index_build())
    assert result["status"] == "cancelling"
    assert cancelled["called"] is True


def test_cancel_endpoint_returns_409_when_idle(monkeypatch):
    from fastapi import HTTPException
    from backend.api import similarity as sim_api

    class FakeEQ:
        on_progress = on_complete = on_error = None
        def is_indexing(self): return False

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
    monkeypatch.setattr(scan_api, "get_db", lambda: type("D", (), {
        "get_existing_file_paths": lambda self: set(),
        "delete_media_batch": lambda self, paths: 0,
        "get_unembedded_file_paths": lambda self: ["/m/a.png"],
    })())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None): pass
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
    monkeypatch.setattr(scan_api, "get_db", lambda: type("D", (), {
        "get_existing_file_paths": lambda self: set(),
        "delete_media_batch": lambda self, paths: 0,
        "get_unembedded_file_paths": lambda self: ["/m/a.png"],
    })())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None): pass
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
    monkeypatch.setattr(scan_api, "get_db", lambda: type("D", (), {
        "get_existing_file_paths": lambda self: set(),
        "delete_media_batch": lambda self, paths: 0,
        "get_unembedded_file_paths": lambda self: [],
    })())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())

    async def fake_broadcast(channel, event, data=None): pass
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
    a = Media(file_path=P("/m/a.png"), file_size=100, width=640, height=480, format="png", created_at=now, modified_at=now)
    b = Media(file_path=P("/m/b.png"), file_size=100, width=640, height=480, format="png", created_at=now, modified_at=now)
    c = Media(file_path=P("/m/c.png"), file_size=100, width=640, height=480, format="png", created_at=now, modified_at=now)
    a.is_favorite = True
    c.is_favorite = True
    db.save_media(a)
    db.save_media(b)
    db.save_media(c)

    favs = db.get_favorite_file_paths()
    assert sorted(favs) == ["/m/a.png", "/m/c.png"]


def test_full_clean_snapshots_and_restores_favorites(monkeypatch):
    """In full_clean mode the scan must:
      1) capture favorites before truncating,
      2) truncate the media table,
      3) re-mark favorites that exist after the rescan."""
    from backend.api import scan as scan_api

    favs_before = ["/m/keep.png", "/m/also.png"]
    truncated = {"called": False}
    restored = []
    final_existing = {"/m/keep.png", "/m/new.png"}  # "also.png" gone after rescan

    class FakeDB:
        def get_favorite_file_paths(self): return list(favs_before)
        def truncate_all_data(self):
            truncated["called"] = True
            return True
        def get_existing_file_paths(self): return final_existing
        def delete_media_batch(self, paths): return 0
        def set_favorite(self, path, is_favorite):
            restored.append((str(path), is_favorite))
            return True
        def get_unembedded_file_paths(self): return []

    monkeypatch.setattr(scan_api, "get_db", lambda: FakeDB())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {"similarity": {}})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [])

    async def fake_broadcast(channel, event, data=None): pass
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert truncated["called"] is True
    # Only paths still present after rescan should be restored
    restored_paths = {p for p, fav in restored if fav}
    assert restored_paths == {"/m/keep.png"}


def test_full_clean_restores_favorites_on_cancel(monkeypatch):
    """Cancelling a full_clean scan must still restore favorites for any
    files that exist in the post-truncate DB state."""
    from backend.api import scan as scan_api

    favs = ["/m/keep.png", "/m/lost.png"]
    restored = []

    class FakeDirCfg:
        filepath = "/nonexistent"
        search_subfolders = False

    class FakeDB:
        def get_favorite_file_paths(self): return list(favs)
        def truncate_all_data(self): return True
        def get_existing_file_paths(self): return {"/m/keep.png"}
        def delete_media_batch(self, paths): return 0
        def set_favorite(self, path, is_favorite):
            restored.append((str(path), is_favorite))
            return True
        def get_unembedded_file_paths(self): return []

    monkeypatch.setattr(scan_api, "get_db", lambda: FakeDB())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", lambda *a, **k: object())
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {"similarity": {}})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [FakeDirCfg()])

    async def fake_broadcast(channel, event, data=None): pass
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    # Trip cancel BEFORE the loop so the first iteration short-circuits
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False

    restored_paths = {p for p, fav in restored if fav}
    assert restored_paths == {"/m/keep.png"}, (
        f"Cancel path lost favorites; got {restored_paths!r}"
    )


def test_full_clean_restores_favorites_on_exception(monkeypatch):
    """A scan exception must trigger best-effort favorite restore."""
    from backend.api import scan as scan_api

    favs = ["/m/keep.png"]
    restored = []

    class FakeDirCfg:
        filepath = "/will-explode"
        search_subfolders = False

    class FakeDB:
        def get_favorite_file_paths(self): return list(favs)
        def truncate_all_data(self): return True
        def get_existing_file_paths(self): return {"/m/keep.png"}
        def delete_media_batch(self, paths): return 0
        def set_favorite(self, path, is_favorite):
            restored.append((str(path), is_favorite))
            return True
        def get_unembedded_file_paths(self): return []

    monkeypatch.setattr(scan_api, "get_db", lambda: FakeDB())
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())

    # Scanner that explodes on use
    class BoomScanner:
        def __init__(self, *a, **k): pass
        def scan_directory(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(scan_api, "Scanner", BoomScanner)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {"similarity": {}})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [FakeDirCfg()])

    # Make the directory "exist" so we proceed past the if-not-exists check
    import os
    monkeypatch.setattr(scan_api.Path, "exists", lambda self: True)

    async def fake_broadcast(channel, event, data=None): pass
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    restored_paths = {p for p, fav in restored if fav}
    assert restored_paths == {"/m/keep.png"}, (
        f"Error path lost favorites; got {restored_paths!r}"
    )
