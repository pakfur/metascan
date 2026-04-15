# Hybrid Scan + Embedding Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicate the PyQt6 desktop scan + embed UX in the Vue/FastAPI web stack. Switch to the same `EmbeddingQueue` subprocess pipeline used by PyQt; auto-trigger embedding after successful scans (gated by a persisted toggle); offer manual Build/Rebuild and CLIP/pHash configuration in the Similarity Settings dialog; surface progress in a single multi-phase scan dialog with a step tracker; preserve favorites across full-clean rescans.

**Architecture:**
- **Subprocess EmbeddingQueue** replaces the existing (broken) in-process FAISS pipeline in `backend/api/similarity.py`. The subprocess pattern mirrors the upscale wiring shipped in commit `fdb6c90`: lazy singleton, asyncio poller loop, callbacks bridged to the `embedding` WebSocket channel, init/shutdown hooks. After a worker completes, the main process reloads the FAISS index from disk so search endpoints see new vectors.
- **Auto-trigger** runs after `scan/complete` only when (a) the config has a `similarity` block, (b) the new `similarity.auto_index_after_scan` flag is true, and (c) `db.get_unembedded_file_paths()` returns ≥1 path.
- **Full clean** wipes media/index tables but snapshots `is_favorite` paths beforehand and restores them after the rescan completes.
- **Frontend** redesigns `ScanDialog.vue` as a single 5-step modal (Prepare → Confirm → Scan → Embed → Done) with a step tracker; adds Build/Rebuild + auto-index + compute-pHash toggles + stats line to `SimilaritySettings.vue`.

**Tech Stack:** FastAPI, Pydantic, asyncio, subprocess, Pinia, Vue 3 Composition API, TypeScript, PrimeVue, pytest.

---

## Important context for implementers

These facts have been verified before plan was written. Don't re-verify; use them.

- **`backend/api/similarity.py` is dead code in places.** It calls `fm.add_files`, `fm.save_index`, `fm.search_similar`, `fm.search_by_text` — none of which exist on `FaissIndexManager`. Real methods are `add_batch`, `save`, `search`, `load`. The current `_run_index_build`, `search_similar`, and `content_search` paths all crash at runtime. This plan replaces them.
- **`EmbeddingQueue` lives at `metascan/core/embedding_queue.py`.** Public surface: `start_indexing(file_paths, clip_model_key, device, db_path, compute_phash, video_keyframes) -> bool`, `cancel_indexing()`, `is_indexing() -> bool`, `poll_updates()`, `get_last_progress()`, `index_dir` property. Callbacks: `on_progress(current, total, status)`, `on_complete(total)`, `on_error(msg)`. The status string passed to `on_progress` is a human label (e.g. `"Indexing 3/100 — file.png (1 errors)"`); the raw structured fields (`status`, `current_file`, `errors_count`, `current`, `total`) live in `_last_progress` accessible via `get_last_progress()`.
- **Worker writes FAISS to `EmbeddingQueue.index_dir`** which is `get_data_dir() / "similarity"`. The current `_get_faiss_manager()` uses `get_data_dir() / "faiss_index"` — a different directory. **Plan switches the manager to share `eq.index_dir`** so the index the worker writes is the index the search endpoints read.
- **Existing `db` helpers we'll use:** `get_unembedded_file_paths() -> List[str]`, `get_existing_file_paths() -> Set[str]`, `get_embedding_stats() -> dict`, `clear_embeddings() -> bool`, `mark_embedded(paths, model)`, `truncate_all_data() -> bool`, `set_favorite(path: Path, is_favorite: bool) -> bool`. **Missing:** `get_favorite_file_paths()` — Task 8 adds it.
- **`ws_manager.broadcast_sync` works from worker threads** (fixed in `fdb6c90`). Use it for callbacks invoked by the poll loop.
- **The poller pattern** is established: see `backend/api/upscale.py` `_poll_loop`, `init_upscale_queue`, `shutdown_upscale_queue` — mirror that exactly for embeddings.
- **CLAUDE.md says:** core modules must not import `PyQt6`/`qt_material`; backend must use `asyncio.to_thread` to wrap sync DB calls; all 90 tests must continue to pass.

---

## Decisions (locked in unless overridden)

1. **Subprocess (`EmbeddingQueue`) for embeddings**, not in-process. The in-process code is broken and the user explicitly requested this switch.
2. **Single shared index dir.** Both the queue and the FAISS manager use `get_data_dir() / "similarity"`. The legacy `get_data_dir() / "faiss_index"` directory is abandoned (no working code path used it).
3. **Auto-trigger gate:** all three of (a) `similarity` config block exists, (b) `similarity.auto_index_after_scan` is true (default true), (c) at least one unembedded file. Mirrors PyQt `_auto_trigger_embeddings()` plus a persistent toggle.
4. **Auto-trigger scope:** unembedded files only. Manual "Rebuild All" calls `db.clear_embeddings()` first.
5. **pHash during scan:** existing `similarity.compute_phash_during_scan` flag (default true), now exposed in the UI as a checkbox.
6. **Cancel semantics:** scan cancel does NOT trigger embeddings. Embedding cancel is separate (calls `EmbeddingQueue.cancel_indexing()`); the worker handles SIGTERM and writes a final `cancelled` progress entry.
7. **Full clean** = `db.truncate_all_data()` + `fm.clear()` after snapshotting favorites; rescan; re-apply favorites for matching paths post-scan. The user is warned it's destructive in the dialog.
8. **No background polling for stats.** SimilaritySettings refreshes stats on dialog open and after embedding completes.

---

## File Structure

**Backend — modify:**
- `backend/api/similarity.py` — full rewrite of build/cancel/search endpoints onto subprocess pattern; add singleton + callbacks + poller (mirror upscale).
- `backend/api/scan.py` — auto-trigger after success; full-clean snapshot/restore of favorites.
- `backend/main.py` — register embedding init/shutdown hooks alongside the existing upscale ones.
- `metascan/core/database_sqlite.py` — add `get_favorite_file_paths()`.

**Backend — new:**
- `tests/test_embedding_index_api.py` — endpoint, auto-trigger, cancel, full-clean tests.

**Frontend — modify:**
- `frontend/src/api/similarity.ts` — add `cancelIndex()`; extend `SimilaritySettings` interface for new fields.
- `frontend/src/api/scan.ts` — extend start payload to support `mode: 'incremental' | 'full_clean'`.
- `frontend/src/stores/scan.ts` — enrich embedding handler with `status`/`current_file`/`errors_count`; add `cancelEmbedding()`; add `scanMode` ref.
- `frontend/src/components/dialogs/ScanDialog.vue` — single multi-phase modal with step tracker; radio for incremental/full-clean; auto-advance to embed phase.
- `frontend/src/components/dialogs/SimilaritySettings.vue` — stats line + Build/Rebuild buttons + `auto_index_after_scan` + `compute_phash_during_scan` checkboxes.

---

## Phase A — Backend: subprocess EmbeddingQueue infrastructure

### Task A1: Wire EmbeddingQueue singleton, callbacks, and poller

**Files:**
- Modify: `backend/api/similarity.py`
- Test: `tests/test_embedding_index_api.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_embedding_index_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source venv/bin/activate && python -m pytest tests/test_embedding_index_api.py::test_get_embedding_queue_creates_singleton_with_callbacks -v
```

Expected: FAIL — `AttributeError: module 'backend.api.similarity' has no attribute '_get_embedding_queue'`.

- [ ] **Step 3: Refactor `backend/api/similarity.py` to wire EmbeddingQueue**

Replace the entire current contents of `backend/api/similarity.py` with:

```python
"""Similarity search and embedding-index endpoints (subprocess pattern)."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import load_app_config, save_app_config
from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService
from backend.ws.manager import ws_manager
from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager
from metascan.core.embedding_queue import EmbeddingQueue
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/similarity", tags=["similarity"])

# Lazy-loaded singletons
_embedding_manager: Optional[EmbeddingManager] = None
_faiss_manager: Optional[FaissIndexManager] = None
_embedding_queue: Optional[EmbeddingQueue] = None
_embed_poll_task: Optional[asyncio.Task] = None
_EMBED_POLL_INTERVAL_SECONDS = 0.5


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


def _get_embedding_manager() -> EmbeddingManager:
    global _embedding_manager
    if _embedding_manager is None:
        config = load_app_config()
        sim_config = config.get("similarity", {})
        model_size = sim_config.get("clip_model", "small")
        device = sim_config.get("device", "auto")
        _embedding_manager = EmbeddingManager(model_size=model_size, device=device)
    return _embedding_manager


def _get_faiss_manager() -> FaissIndexManager:
    """FAISS index manager. Shares its directory with the EmbeddingQueue
    so the index written by the worker is the index the search endpoints
    read."""
    global _faiss_manager
    if _faiss_manager is None:
        _faiss_manager = FaissIndexManager(_get_embedding_queue().index_dir)
        _faiss_manager.load()  # No-op if no on-disk index exists
    return _faiss_manager


def _get_embedding_queue() -> EmbeddingQueue:
    """Lazily construct the embedding queue and bridge its callbacks to
    the WebSocket 'embedding' channel."""
    global _embedding_queue
    if _embedding_queue is None:
        eq = EmbeddingQueue()
        _attach_embedding_callbacks(eq)
        _embedding_queue = eq
    return _embedding_queue


def _attach_embedding_callbacks(eq: EmbeddingQueue) -> None:
    def on_progress(current: int, total: int, label: str) -> None:
        # The label is human-formatted; raw structured fields live in
        # eq._last_progress (exposed via get_last_progress()).
        raw = eq.get_last_progress()
        ws_manager.broadcast_sync(
            "embedding",
            "progress",
            {
                "current": current,
                "total": total,
                "label": label,
                "status": raw.get("status", ""),
                "current_file": raw.get("current_file", ""),
                "errors_count": raw.get("errors_count", 0),
            },
        )

    def on_complete(total: int) -> None:
        # Reload the FAISS index so search endpoints see the new vectors.
        try:
            _reload_faiss_after_index_build()
        except Exception as e:
            logger.exception(f"Failed to reload FAISS after index build: {e}")
        ws_manager.broadcast_sync("embedding", "complete", {"total": total})

    def on_error(msg: str) -> None:
        ws_manager.broadcast_sync("embedding", "error", {"message": msg})

    eq.on_progress = on_progress
    eq.on_complete = on_complete
    eq.on_error = on_error


def _reload_faiss_after_index_build() -> None:
    """Reset the cached FAISS manager so the next search call reloads
    fresh data from disk."""
    global _faiss_manager
    _faiss_manager = None


async def _embed_poll_loop() -> None:
    """Drive the EmbeddingQueue: read worker progress and emit callbacks."""
    eq = _get_embedding_queue()
    logger.info("Embedding poller started")
    try:
        while True:
            try:
                await asyncio.to_thread(eq.poll_updates)
            except Exception as e:
                logger.exception(f"Embedding poll_updates failed: {e}")
            await asyncio.sleep(_EMBED_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Embedding poller cancelled")
        raise


def init_embedding_queue() -> None:
    """Startup hook: create singleton + start poll loop."""
    global _embed_poll_task
    _get_embedding_queue()
    if _embed_poll_task is None or _embed_poll_task.done():
        _embed_poll_task = asyncio.create_task(_embed_poll_loop())


async def shutdown_embedding_queue() -> None:
    """Shutdown hook: cancel poller and any running worker."""
    global _embed_poll_task, _embedding_queue
    if _embed_poll_task is not None:
        _embed_poll_task.cancel()
        try:
            await _embed_poll_task
        except (asyncio.CancelledError, Exception):
            pass
        _embed_poll_task = None
    if _embedding_queue is not None:
        try:
            await asyncio.to_thread(_embedding_queue.cancel_indexing)
        except Exception as e:
            logger.warning(f"Error cancelling embedding worker on shutdown: {e}")


# ----- Search endpoints (rewritten to use real FaissIndexManager methods) -----


class SimilaritySearchRequest(BaseModel):
    file_path: str
    threshold: float = 0.7
    max_results: int = 100


class ContentSearchRequest(BaseModel):
    query: str
    max_results: int = 100


class SimilaritySettingsUpdate(BaseModel):
    clip_model: Optional[str] = None
    device: Optional[str] = None
    phash_threshold: Optional[int] = None
    clip_threshold: Optional[float] = None
    search_results_count: Optional[int] = None
    video_keyframes: Optional[int] = None
    compute_phash_during_scan: Optional[bool] = None
    auto_index_after_scan: Optional[bool] = None


@router.post("/search")
async def search_similar(
    body: SimilaritySearchRequest,
    service: MediaService = Depends(_get_service),
):
    """Search for media similar to the given file path using FAISS."""
    em = _get_embedding_manager()
    fm = _get_faiss_manager()

    if not fm.is_loaded:
        raise HTTPException(status_code=503, detail="No embedding index loaded yet")

    # Compute query vector for the input file
    is_video = Path(body.file_path).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi"}

    def _compute() -> Any:
        if is_video:
            return em.compute_video_embedding(body.file_path)
        return em.compute_image_embedding(body.file_path)

    vec = await asyncio.to_thread(_compute)
    if vec is None:
        raise HTTPException(status_code=400, detail="Failed to compute query embedding")

    raw = await asyncio.to_thread(fm.search, vec, body.max_results)

    output = []
    for file_path, score in raw:
        if float(score) < body.threshold:
            continue
        media = await service.get_media(file_path)
        if media:
            d = service.media_to_dict(media)
            d["similarity_score"] = float(score)
            output.append(d)
    return output


@router.post("/content-search")
async def content_search(
    body: ContentSearchRequest,
    service: MediaService = Depends(_get_service),
):
    """Search for media matching a text query using CLIP embeddings."""
    em = _get_embedding_manager()
    fm = _get_faiss_manager()

    if not fm.is_loaded:
        raise HTTPException(status_code=503, detail="No embedding index loaded yet")

    vec = await asyncio.to_thread(em.compute_text_embedding, body.query)
    if vec is None:
        raise HTTPException(status_code=400, detail="Failed to compute text embedding")

    raw = await asyncio.to_thread(fm.search, vec, body.max_results)

    output = []
    for file_path, score in raw:
        media = await service.get_media(file_path)
        if media:
            d = service.media_to_dict(media)
            d["similarity_score"] = float(score)
            output.append(d)
    return output


# ----- Settings -----


@router.get("/settings")
async def get_similarity_settings():
    config = load_app_config()
    sim_config = config.get("similarity", {})
    db = get_db()
    stats = await asyncio.to_thread(db.get_embedding_stats)
    return {
        "clip_model": sim_config.get("clip_model", "small"),
        "device": sim_config.get("device", "auto"),
        "phash_threshold": sim_config.get("phash_threshold", 10),
        "clip_threshold": sim_config.get("clip_threshold", 0.7),
        "search_results_count": sim_config.get("search_results_count", 100),
        "video_keyframes": sim_config.get("video_keyframes", 4),
        "compute_phash_during_scan": sim_config.get("compute_phash_during_scan", True),
        "auto_index_after_scan": sim_config.get("auto_index_after_scan", True),
        "embedding_stats": stats,
    }


@router.put("/settings")
async def update_similarity_settings(body: SimilaritySettingsUpdate):
    global _embedding_manager
    config = load_app_config()
    sim_config = config.setdefault("similarity", {})

    changed_model = False
    for field, value in body.dict(exclude_none=True).items():
        if field in ("clip_model", "device") and sim_config.get(field) != value:
            changed_model = True
        sim_config[field] = value

    save_app_config(config)

    if changed_model:
        _embedding_manager = None  # Force reload on next use

    return sim_config


# ----- Index build/cancel (subprocess-driven) -----


@router.post("/index/build")
async def build_index(rebuild: bool = False) -> Dict[str, Any]:
    """Start an embedding worker subprocess for unembedded (or all) files."""
    eq = _get_embedding_queue()
    if eq.is_indexing():
        raise HTTPException(status_code=409, detail="Index build already in progress")

    db = get_db()
    config = load_app_config()
    sim = config.get("similarity", {})

    if rebuild:
        await asyncio.to_thread(db.clear_embeddings)

    paths = await asyncio.to_thread(db.get_unembedded_file_paths)
    if not paths:
        # Nothing to do — emit a synthetic complete event for the UI.
        await ws_manager.broadcast("embedding", "complete", {"total": 0})
        return {"status": "noop", "total": 0}

    started = await asyncio.to_thread(
        eq.start_indexing,
        paths,
        sim.get("clip_model", "small"),
        sim.get("device", "auto"),
        str(get_data_dir()),
        bool(sim.get("compute_phash_during_scan", True)) and not rebuild,
        int(sim.get("video_keyframes", 4)),
    )
    if not started:
        raise HTTPException(status_code=409, detail="Embedding worker did not start")

    await ws_manager.broadcast(
        "embedding", "started", {"rebuild": rebuild, "total": len(paths)}
    )
    return {"status": "started", "total": len(paths)}


@router.post("/index/cancel")
async def cancel_index_build() -> Dict[str, str]:
    eq = _get_embedding_queue()
    if not eq.is_indexing():
        raise HTTPException(status_code=409, detail="No index build in progress")
    await asyncio.to_thread(eq.cancel_indexing)
    return {"status": "cancelling"}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
source venv/bin/activate && python -m pytest tests/test_embedding_index_api.py -v
```

Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add backend/api/similarity.py tests/test_embedding_index_api.py
git commit -m "$(printf 'refactor(embeddings): switch to EmbeddingQueue subprocess pipeline\n\nReplaces broken in-process FAISS pipeline (calls phantom add_files /\nsave_index / search_similar / search_by_text methods) with the same\nsubprocess pattern used by upscale. Adds singleton, WS-bridged\ncallbacks, and async poll loop. Search endpoints now use the real\nFaissIndexManager.search() method.')"
```

---

### Task A2: Register startup/shutdown hooks for the embedding poller

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add the import**

In `backend/main.py`, find the line `from backend.api import (` (around line 13) and ensure `similarity` is imported (it already is). No change needed — the existing import covers it.

- [ ] **Step 2: Wire the hooks into the existing `_on_startup` / `_on_shutdown`**

Replace the existing startup/shutdown block (the `@app.on_event("startup")` and `@app.on_event("shutdown")` functions added in commit `fdb6c90`):

```python
    # Startup/shutdown hooks
    @app.on_event("startup")
    async def _on_startup() -> None:
        ws_manager.attach_loop(asyncio.get_running_loop())
        upscale.init_upscale_queue()
        similarity.init_embedding_queue()

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        await upscale.shutdown_upscale_queue()
        await similarity.shutdown_embedding_queue()
```

- [ ] **Step 3: Smoke-import the app**

```bash
source venv/bin/activate && python -c "from backend.main import app; print('OK')"
```

Expected: prints `OK` with no exceptions.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat(embeddings): register embedding poller startup/shutdown hooks"
```

---

### Task A3: Test build / cancel endpoints with mocked subprocess

**Files:**
- Modify: `tests/test_embedding_index_api.py`

- [ ] **Step 1: Add tests for build and cancel endpoints**

Add to `tests/test_embedding_index_api.py`:

```python
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
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_embedding_index_api.py -v
```

Expected: PASS (6 tests total).

- [ ] **Step 3: Commit**

```bash
git add tests/test_embedding_index_api.py
git commit -m "test(embeddings): cover build, noop, and cancel endpoints"
```

---

## Phase B — Backend: scan integration (auto-trigger)

### Task B1: Auto-trigger embedding subprocess after successful scan

**Files:**
- Modify: `backend/api/scan.py`
- Test: `tests/test_embedding_index_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_embedding_index_api.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_embedding_index_api.py::test_scan_auto_triggers_when_config_present_and_unembedded -v
```

Expected: FAIL — `_run_scan` does not call `build_index`.

- [ ] **Step 3: Add the auto-trigger to `_run_scan`**

In `backend/api/scan.py`, near the top of the file (with the other `from backend.*` imports), add:

```python
from backend.api import similarity as similarity_api
```

In `_run_scan`, just before the existing `await ws_manager.broadcast("scan", "complete", ...)` line, insert:

```python
        # Auto-trigger embedding build when:
        #   - the scan was not cancelled,
        #   - config has a similarity block with auto_index_after_scan true (default true),
        #   - at least one file is missing an embedding.
        # Mirrors PyQt _auto_trigger_embeddings (metascan/ui/main_window.py:2231).
        config = load_app_config()
        sim = config.get("similarity") if isinstance(config, dict) else None
        if (
            not _cancel_requested
            and sim is not None
            and sim.get("auto_index_after_scan", True)
        ):
            unembedded = await asyncio.to_thread(db.get_unembedded_file_paths)
            if unembedded:
                try:
                    await similarity_api.build_index(rebuild=False)
                except Exception as e:
                    logger.warning(f"Auto-trigger embedding build failed: {e}")
```

If `config = load_app_config()` is already loaded earlier in the function, reuse it instead of reloading.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_embedding_index_api.py -v
```

Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/api/scan.py tests/test_embedding_index_api.py
git commit -m "feat(scan): auto-trigger embedding build after successful scan"
```

---

## Phase C — Backend: full clean with favorites preservation

### Task C1: Add `db.get_favorite_file_paths()`

**Files:**
- Modify: `metascan/core/database_sqlite.py`
- Test: extend an existing test or add to `tests/test_embedding_index_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_embedding_index_api.py`:

```python
def test_get_favorite_file_paths_returns_only_favorites(tmp_path):
    """Verify the new helper returns paths where is_favorite=1."""
    from metascan.core.database_sqlite import DatabaseManager
    from metascan.core.media import Media
    from pathlib import Path as P

    db = DatabaseManager(tmp_path)
    a = Media(file_path=P("/m/a.png"))
    b = Media(file_path=P("/m/b.png"))
    c = Media(file_path=P("/m/c.png"))
    a.is_favorite = True
    c.is_favorite = True
    db.save_media(a); db.save_media(b); db.save_media(c)

    favs = db.get_favorite_file_paths()
    assert sorted(favs) == ["/m/a.png", "/m/c.png"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_embedding_index_api.py::test_get_favorite_file_paths_returns_only_favorites -v
```

Expected: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'get_favorite_file_paths'`.

- [ ] **Step 3: Add the method**

In `metascan/core/database_sqlite.py`, add this method to the `DatabaseManager` class right after the existing `get_existing_file_paths` method (around line 325):

```python
    def get_favorite_file_paths(self) -> List[str]:
        """Return all file paths flagged as favorite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT file_path FROM media WHERE is_favorite = 1"
                )
                return [to_native_path(row["file_path"]) for row in cursor]
        except Exception as e:
            logger.error(f"Failed to get favorite file paths: {e}")
            return []
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_embedding_index_api.py::test_get_favorite_file_paths_returns_only_favorites -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_embedding_index_api.py
git commit -m "feat(db): add get_favorite_file_paths helper"
```

---

### Task C2: Full-clean scan mode that preserves favorites

**Files:**
- Modify: `backend/api/scan.py`
- Test: `tests/test_embedding_index_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_embedding_index_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_embedding_index_api.py::test_full_clean_snapshots_and_restores_favorites -v
```

Expected: FAIL — `_run_scan()` doesn't accept `full_clean`.

- [ ] **Step 3: Update `ScanRequest`, `start_scan`, and `_run_scan`**

In `backend/api/scan.py`:

Update the `ScanRequest` model:

```python
class ScanRequest(BaseModel):
    full_cleanup: bool = False
    full_clean: bool = False  # destructive: truncates DB, preserves favorites
```

Update `start_scan` to pass the new flag:

```python
@router.post("/start")
async def start_scan(body: ScanRequest):
    global _scan_task, _cancel_requested

    if _scan_task and not _scan_task.done():
        raise HTTPException(status_code=409, detail="Scan already in progress")

    _cancel_requested = False
    _scan_task = asyncio.create_task(_run_scan(body.full_cleanup, body.full_clean))
    return {"status": "started"}
```

Update `_run_scan` signature and body. Add at the very top of the function (after the existing `db = get_db()` line):

```python
async def _run_scan(full_cleanup: bool, full_clean: bool = False) -> None:
    db = get_db()
    thumbnail_cache = get_thumbnail_cache()
    config = load_app_config()
    directories = get_directories(config)

    scanner = Scanner(db, thumbnail_cache)

    await ws_manager.broadcast("scan", "started", {"full_clean": full_clean})

    # Snapshot favorites before any destructive operation
    favorites_snapshot: list = []
    if full_clean:
        favorites_snapshot = await asyncio.to_thread(db.get_favorite_file_paths)
        await asyncio.to_thread(db.truncate_all_data)
        await ws_manager.broadcast(
            "scan",
            "phase_changed",
            {"phase": "full_clean", "favorites_preserved": len(favorites_snapshot)},
        )
```

After the rescan loop (just after the existing stale-cleanup block, before the auto-trigger block from Task B1), add:

```python
        # Restore favorites for files that re-appeared in the rescan
        if full_clean and favorites_snapshot:
            existing = await asyncio.to_thread(db.get_existing_file_paths)
            restored = 0
            for path in favorites_snapshot:
                if path in existing:
                    await asyncio.to_thread(db.set_favorite, Path(path), True)
                    restored += 1
            await ws_manager.broadcast(
                "scan", "favorites_restored", {"count": restored}
            )
```

- [ ] **Step 4: Run all backend tests**

```bash
python -m pytest tests/test_embedding_index_api.py -v
```

Expected: PASS (11 tests total).

- [ ] **Step 5: Commit**

```bash
git add backend/api/scan.py tests/test_embedding_index_api.py
git commit -m "feat(scan): full-clean mode preserves favorites across rescan"
```

---

## Phase D — Backend: quality gates

### Task D1: Run flake8/black/mypy/pytest

**Files:** none modified (formatter may auto-fix).

- [ ] **Step 1: flake8 fatal**

```bash
source venv/bin/activate && python -m flake8 metascan/ backend/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
```

Expected: `0` errors.

- [ ] **Step 2: black**

```bash
python -m black --check metascan/ backend/ tests/
```

Expected: `would be left unchanged`. If not, run `python -m black metascan/ backend/ tests/`.

- [ ] **Step 3: mypy**

```bash
python -m mypy --check-untyped-defs metascan/
```

Expected: `Success: no issues found`.

- [ ] **Step 4: full test suite**

```bash
python -m pytest
```

Expected: all tests pass (90 existing + 11 new = 101+).

- [ ] **Step 5: Commit any formatter changes**

```bash
git add -A
git commit -m "chore: format with black" || echo "nothing to commit"
```

---

## Phase E — Frontend: similarity settings UI

### Task E1: Add `cancelIndex` API + extend SimilaritySettings interface

**Files:**
- Modify: `frontend/src/api/similarity.ts`

- [ ] **Step 1: Edit the API client**

In `frontend/src/api/similarity.ts`, replace the existing `SimilaritySettings` interface (lines 3-17) with:

```typescript
export interface SimilaritySettings {
  clip_model: string
  device: string
  phash_threshold: number
  clip_threshold: number
  search_results_count: number
  video_keyframes: number
  compute_phash_during_scan: boolean
  auto_index_after_scan: boolean
  embedding_stats: {
    total_media: number
    hashed: number
    embedded: number
    clip_model: string | null
  }
}
```

After the existing `buildIndex` function (around line 29), add:

```typescript
export function cancelIndex(): Promise<{ status: string }> {
  return post<{ status: string }>('/similarity/index/cancel')
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/similarity.ts
git commit -m "feat(frontend): add cancelIndex API and auto_index_after_scan field"
```

---

### Task E2: Enrich embedding event handler in scan store

**Files:**
- Modify: `frontend/src/stores/scan.ts`

- [ ] **Step 1: Add new state refs and the cancel action**

In `frontend/src/stores/scan.ts`, change the imports (line 4):

```typescript
import { buildIndex, cancelIndex, fetchEmbeddingStatus } from '../api/similarity'
```

In the embedding state block (around line 38-41), add:

```typescript
  const embeddingStatus = ref<
    'idle' | 'starting' | 'downloading_model' | 'loading_model' | 'processing'
  >('idle')
  const embeddingLabel = ref('')
  const embeddingCurrentFile = ref('')
  const embeddingErrorsCount = ref(0)
```

Replace the `useWebSocket('embedding', ...)` block (lines 85-102) with:

```typescript
  useWebSocket('embedding', (event, data) => {
    switch (event) {
      case 'started':
        embeddingPhase.value = 'building'
        embeddingStatus.value = 'starting'
        embeddingLabel.value = ''
        embeddingCurrentFile.value = ''
        embeddingErrorsCount.value = 0
        embeddingError.value = ''
        if (data.total !== undefined) {
          embeddingTotal.value = (data.total as number) || 0
        }
        if (phase.value === 'complete') phase.value = 'embedding'
        break
      case 'progress':
        embeddingCurrent.value = (data.current as number) || 0
        embeddingTotal.value = (data.total as number) || 0
        if (data.label) embeddingLabel.value = data.label as string
        if (data.status) embeddingStatus.value = data.status as typeof embeddingStatus.value
        if (data.current_file !== undefined) {
          embeddingCurrentFile.value = (data.current_file as string) || ''
        }
        if (data.errors_count !== undefined) {
          embeddingErrorsCount.value = (data.errors_count as number) || 0
        }
        break
      case 'complete':
        embeddingPhase.value = 'complete'
        if (phase.value === 'embedding') phase.value = 'complete'
        break
      case 'cancelled':
        embeddingPhase.value = 'idle'
        if (phase.value === 'embedding') phase.value = 'complete'
        break
      case 'error':
        embeddingPhase.value = 'error'
        embeddingError.value = (data.message as string) || 'Unknown error'
        break
    }
  })
```

Add this action below the existing `startEmbeddingBuild` (around line 148):

```typescript
  async function cancelEmbedding() {
    try { await cancelIndex() } catch { /* 409 = already finished */ }
  }
```

Replace `resetEmbedding`:

```typescript
  function resetEmbedding() {
    embeddingPhase.value = 'idle'
    embeddingStatus.value = 'idle'
    embeddingCurrent.value = 0
    embeddingTotal.value = 0
    embeddingLabel.value = ''
    embeddingCurrentFile.value = ''
    embeddingErrorsCount.value = 0
    embeddingError.value = ''
  }
```

Add the new refs and `cancelEmbedding` to the returned object:

```typescript
    embeddingStatus,
    embeddingLabel,
    embeddingCurrentFile,
    embeddingErrorsCount,
    cancelEmbedding,
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/scan.ts
git commit -m "feat(frontend): enrich embedding state with status, label, file, errors"
```

---

### Task E3: Update `SimilaritySettings.vue` (stats line + Build/Rebuild + toggles)

**Files:**
- Modify: `frontend/src/components/dialogs/SimilaritySettings.vue`

- [ ] **Step 1: Read the existing file to find where settings render**

```bash
cat frontend/src/components/dialogs/SimilaritySettings.vue
```

Locate (a) the script `setup` block, (b) the template's settings form, and (c) the styles section.

- [ ] **Step 2: Edit the script setup**

In the `<script setup>` block, ensure these imports are present (extend the existing imports — do NOT duplicate):

```typescript
import { ref, computed, onMounted } from 'vue'
import {
  fetchSimilaritySettings,
  updateSimilaritySettings,
  buildIndex,
  type SimilaritySettings as SimilaritySettingsType,
} from '../../api/similarity'
import { useScanStore } from '../../stores/scan'
```

Add (or extend) state and helpers:

```typescript
const settings = ref<SimilaritySettingsType | null>(null)
const scanStore = useScanStore()

async function refreshSettings() {
  settings.value = await fetchSimilaritySettings()
}

onMounted(refreshSettings)

const missingCount = computed(() => {
  if (!settings.value) return 0
  const s = settings.value.embedding_stats
  return Math.max(0, s.total_media - s.embedded)
})

async function toggleAutoIndex(value: boolean) {
  if (!settings.value) return
  settings.value.auto_index_after_scan = value
  await updateSimilaritySettings({ auto_index_after_scan: value })
}

async function toggleComputePhash(value: boolean) {
  if (!settings.value) return
  settings.value.compute_phash_during_scan = value
  await updateSimilaritySettings({ compute_phash_during_scan: value })
}

async function buildMissing() {
  await buildIndex(false)
}

async function rebuildAll() {
  if (!confirm(
    'Rebuild ALL embeddings? This clears the existing index and re-indexes every media file. May take a long time.',
  )) return
  await buildIndex(true)
}

// Refresh stats once a build completes
import { watch } from 'vue'
watch(() => scanStore.embeddingPhase, (phase) => {
  if (phase === 'complete' || phase === 'idle') refreshSettings()
})
```

- [ ] **Step 3: Add UI sections to the template**

In the settings dialog template (near the bottom of the settings card, above any "Save" footer), add:

```vue
<section v-if="settings" class="embed-section">
  <h4>Embedding Index</h4>
  <p class="stats">
    Files:
    <strong>{{ settings.embedding_stats.total_media }}</strong> total |
    <strong>{{ settings.embedding_stats.hashed }}</strong> hashed |
    <strong>{{ settings.embedding_stats.embedded }}</strong> embedded
    <span v-if="missingCount > 0" class="missing">
      ({{ missingCount }} missing)
    </span>
  </p>

  <p v-if="scanStore.embeddingPhase === 'building'" class="building">
    {{ scanStore.embeddingLabel || 'Indexing...' }}
    <span v-if="scanStore.embeddingTotal > 0">
      — {{ scanStore.embeddingCurrent }} / {{ scanStore.embeddingTotal }}
    </span>
  </p>

  <div class="embed-actions">
    <button
      class="btn-primary"
      :disabled="missingCount === 0 || scanStore.embeddingPhase === 'building'"
      @click="buildMissing"
    >
      Build Index ({{ missingCount }} missing)
    </button>
    <button
      class="btn-secondary"
      :disabled="scanStore.embeddingPhase === 'building'"
      @click="rebuildAll"
    >
      Rebuild All
    </button>
    <button
      v-if="scanStore.embeddingPhase === 'building'"
      class="btn-danger"
      @click="scanStore.cancelEmbedding()"
    >
      Cancel
    </button>
  </div>

  <label class="setting-toggle">
    <input
      type="checkbox"
      :checked="settings.auto_index_after_scan"
      @change="toggleAutoIndex(($event.target as HTMLInputElement).checked)"
    />
    Auto-index after scan completes
  </label>

  <label class="setting-toggle">
    <input
      type="checkbox"
      :checked="settings.compute_phash_during_scan"
      @change="toggleComputePhash(($event.target as HTMLInputElement).checked)"
    />
    Compute pHash during scan (used for duplicate detection)
  </label>
</section>
```

- [ ] **Step 4: Add styles**

In the `<style scoped>` block, add:

```css
.embed-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-border);
}
.embed-section h4 { margin: 0 0 8px; font-size: 14px; color: var(--text-color); }
.stats { font-size: 13px; color: var(--text-color-secondary); margin-bottom: 8px; }
.stats strong { color: var(--text-color); }
.missing { color: var(--danger-color, #d33); margin-left: 6px; }
.building { font-size: 12px; color: var(--primary-color); margin-bottom: 8px; }
.embed-actions { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.setting-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-color); cursor: pointer; margin-bottom: 6px; }
.btn-primary:disabled, .btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
```

- [ ] **Step 5: Type-check**

```bash
cd frontend && npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/dialogs/SimilaritySettings.vue
git commit -m "feat(frontend): add embedding stats, Build/Rebuild buttons, and persistent toggles"
```

---

## Phase F — Frontend: scan dialog redesign

### Task F1: Rewrite `ScanDialog.vue` with step tracker + incremental/full-clean mode

**Files:**
- Modify: `frontend/src/api/scan.ts`
- Modify: `frontend/src/stores/scan.ts`
- Rewrite: `frontend/src/components/dialogs/ScanDialog.vue`

- [ ] **Step 1: Extend the scan API**

In `frontend/src/api/scan.ts`, find the `startScan` function and update its body to accept a mode payload. If the existing signature is `startScan(fullCleanup: boolean)`, change it to:

```typescript
export interface StartScanPayload {
  full_cleanup: boolean
  full_clean: boolean
}

export function startScan(payload: StartScanPayload): Promise<{ status: string }> {
  return post('/scan/start', payload)
}
```

- [ ] **Step 2: Update the scan store**

In `frontend/src/stores/scan.ts`:

Add a `scanMode` ref near `fullCleanup`:

```typescript
const scanMode = ref<'incremental' | 'full_clean'>('incremental')
```

Update the `start()` function:

```typescript
async function start() {
  phase.value = 'scanning'
  fileCurrent.value = 0
  fileTotal.value = 0
  currentFile.value = ''
  processedCount.value = 0
  staleRemoved.value = 0
  await startScan({
    full_cleanup: fullCleanup.value,
    full_clean: scanMode.value === 'full_clean',
  })
}
```

Add `scanMode` to the returned object.

- [ ] **Step 3: Replace the dialog file**

Overwrite `frontend/src/components/dialogs/ScanDialog.vue` with:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useScanStore } from '../../stores/scan'

const emit = defineEmits<{ close: [] }>()
const scanStore = useScanStore()

const steps = [
  { key: 'preparing', label: 'Prepare' },
  { key: 'confirming', label: 'Confirm' },
  { key: 'scanning', label: 'Scan' },
  { key: 'embedding', label: 'Embed' },
  { key: 'complete', label: 'Done' },
]

function stepIndex(phase: string): number {
  switch (phase) {
    case 'preparing': return 0
    case 'confirming': return 1
    case 'scanning':
    case 'stale_cleanup': return 2
    case 'embedding': return 3
    case 'complete':
    case 'cancelled':
    case 'error': return 4
    default: return 0
  }
}
const activeStep = computed(() => stepIndex(scanStore.phase))

const fileProgressPct = computed(() =>
  scanStore.fileTotal > 0
    ? Math.round((scanStore.fileCurrent / scanStore.fileTotal) * 100)
    : 0,
)
const embeddingProgressPct = computed(() =>
  scanStore.embeddingTotal > 0
    ? Math.round((scanStore.embeddingCurrent / scanStore.embeddingTotal) * 100)
    : 0,
)

const currentFileName = computed(() => {
  const f = scanStore.currentFile
  if (!f) return ''
  const parts = f.split('/')
  return parts[parts.length - 1]
})

const embedFileName = computed(() => {
  const f = scanStore.embeddingCurrentFile
  if (!f) return ''
  const parts = f.split('/')
  return parts[parts.length - 1]
})

const embedHumanLabel = computed(() => {
  switch (scanStore.embeddingStatus) {
    case 'downloading_model': return 'Downloading CLIP model...'
    case 'loading_model': return 'Loading CLIP model...'
    case 'starting': return 'Starting...'
    case 'processing':
      return `Indexing ${scanStore.embeddingCurrent} / ${scanStore.embeddingTotal}` +
        (embedFileName.value ? ` — ${embedFileName.value}` : '') +
        (scanStore.embeddingErrorsCount > 0
          ? ` (${scanStore.embeddingErrorsCount} errors)`
          : '')
    default: return scanStore.embeddingLabel || ''
  }
})

function handleClose() {
  scanStore.reset()
  scanStore.resetEmbedding()
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="handleClose">
    <div class="dialog-card">
      <ol class="step-tracker">
        <li
          v-for="(step, idx) in steps"
          :key="step.key"
          :class="{ done: idx < activeStep, active: idx === activeStep }"
        >
          <span class="step-circle">{{ idx < activeStep ? '✓' : idx + 1 }}</span>
          <span class="step-label">{{ step.label }}</span>
        </li>
      </ol>

      <template v-if="scanStore.phase === 'preparing'">
        <h3>Preparing Scan...</h3>
        <p class="muted">Counting files in configured directories</p>
        <div class="progress-bar indeterminate"><div class="progress-fill" /></div>
      </template>

      <template v-else-if="scanStore.phase === 'confirming' && scanStore.prepareResult">
        <h3>Scan Directories</h3>
        <div class="dir-list">
          <div
            v-for="dir in scanStore.prepareResult.directories"
            :key="dir.path"
            class="dir-item"
          >
            <span class="dir-path" :title="dir.path">{{ dir.path }}</span>
            <span class="dir-count">{{ dir.file_count }} files</span>
            <span v-if="dir.search_subfolders" class="dir-tag">subfolders</span>
          </div>
        </div>
        <div class="stats-row">
          <span>Total: <strong>{{ scanStore.prepareResult.total_files }}</strong></span>
          <span>In DB: <strong>{{ scanStore.prepareResult.existing_in_db }}</strong></span>
        </div>

        <fieldset class="mode-fieldset">
          <legend>Mode</legend>
          <label class="mode-radio">
            <input type="radio" value="incremental" v-model="scanStore.scanMode" />
            <span>
              <strong>Incremental</strong>
              <em>Skip files already in the database</em>
            </span>
          </label>
          <label class="mode-radio">
            <input type="radio" value="full_clean" v-model="scanStore.scanMode" />
            <span>
              <strong>Full clean &amp; rescan</strong>
              <em>Wipe the database and rescan every file. Favorites are preserved.</em>
            </span>
          </label>
        </fieldset>

        <label class="cleanup-toggle">
          <input type="checkbox" v-model="scanStore.fullCleanup" />
          Also remove DB entries for files that no longer exist on disk
        </label>

        <div class="dialog-actions">
          <button class="btn-primary" @click="scanStore.start()">Start Scan</button>
          <button class="btn-secondary" @click="handleClose">Cancel</button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'scanning'">
        <h3>Scanning...</h3>
        <div v-if="scanStore.dirTotal > 0" class="step-indicator">
          Directory {{ scanStore.dirCurrent }} / {{ scanStore.dirTotal }}
        </div>
        <p v-if="scanStore.currentDir" class="current-dir" :title="scanStore.currentDir">
          {{ scanStore.currentDir }}
        </p>
        <div class="progress-section">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: fileProgressPct + '%' }" />
          </div>
          <span class="progress-text">
            {{ scanStore.fileCurrent }} / {{ scanStore.fileTotal }} files ({{ fileProgressPct }}%)
          </span>
        </div>
        <p v-if="currentFileName" class="current-file" :title="scanStore.currentFile">
          {{ currentFileName }}
        </p>
        <div class="dialog-actions">
          <button class="btn-danger" @click="scanStore.cancel()">Cancel Scan</button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'stale_cleanup'">
        <h3>Cleaning Up...</h3>
        <p class="muted">Removing stale database entries for deleted files</p>
        <div class="progress-bar indeterminate"><div class="progress-fill" /></div>
      </template>

      <template v-else-if="scanStore.phase === 'embedding'">
        <h3>Building Embeddings...</h3>
        <p class="muted">{{ embedHumanLabel }}</p>
        <div class="progress-section">
          <div
            class="progress-bar"
            :class="{
              indeterminate:
                scanStore.embeddingStatus === 'downloading_model' ||
                scanStore.embeddingStatus === 'loading_model' ||
                scanStore.embeddingStatus === 'starting',
            }"
          >
            <div
              class="progress-fill"
              :style="
                scanStore.embeddingStatus === 'processing'
                  ? { width: embeddingProgressPct + '%' }
                  : undefined
              "
            />
          </div>
          <span
            v-if="scanStore.embeddingStatus === 'processing'"
            class="progress-text"
          >
            {{ embeddingProgressPct }}%
          </span>
        </div>
        <div class="dialog-actions">
          <button class="btn-danger" @click="scanStore.cancelEmbedding()">
            Cancel Embedding
          </button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'complete'">
        <h3>Scan Complete</h3>
        <div class="result-stats">
          <div class="stat">
            <span class="stat-value">{{ scanStore.processedCount }}</span>
            <span class="stat-label">Files processed</span>
          </div>
          <div v-if="scanStore.staleRemoved > 0" class="stat">
            <span class="stat-value">{{ scanStore.staleRemoved }}</span>
            <span class="stat-label">Stale removed</span>
          </div>
          <div v-if="scanStore.embeddingTotal > 0" class="stat">
            <span class="stat-value">{{ scanStore.embeddingCurrent }}</span>
            <span class="stat-label">Embeddings built</span>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'error'">
        <h3>Scan Error</h3>
        <p class="error-msg">{{ scanStore.errorMessage }}</p>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'cancelled'">
        <h3>Scan Cancelled</h3>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed; inset: 0; z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
}
.dialog-card {
  background: var(--surface-section); border-radius: 12px;
  padding: 24px 28px; min-width: 480px; max-width: 600px;
  max-height: 80vh; overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.step-tracker { display: flex; gap: 4px; list-style: none; margin: 0 0 24px; padding: 0; }
.step-tracker li {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  gap: 6px; position: relative; font-size: 11px; color: var(--text-color-secondary);
}
.step-tracker li::after {
  content: ''; position: absolute; top: 12px; left: 60%;
  width: 80%; height: 2px; background: var(--surface-border); z-index: 0;
}
.step-tracker li:last-child::after { display: none; }
.step-circle {
  position: relative; z-index: 1; width: 24px; height: 24px;
  border-radius: 50%; background: var(--surface-ground);
  border: 2px solid var(--surface-border);
  display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 11px; color: var(--text-color-secondary);
}
.step-tracker li.done .step-circle {
  background: var(--primary-color); border-color: var(--primary-color); color: #fff;
}
.step-tracker li.active .step-circle {
  background: var(--surface-section); border-color: var(--primary-color); color: var(--primary-color);
}
.step-tracker li.active .step-label,
.step-tracker li.done .step-label { color: var(--text-color); }

h3 { margin: 0 0 12px; font-size: 18px; color: var(--text-color); }
.muted { color: var(--text-color-secondary); font-size: 13px; margin-bottom: 12px; }

.dir-list {
  display: flex; flex-direction: column; gap: 6px;
  margin-bottom: 16px; max-height: 200px; overflow-y: auto;
}
.dir-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; background: var(--surface-ground);
  border-radius: 6px; font-size: 13px;
}
.dir-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-color); }
.dir-count { color: var(--text-color-secondary); font-size: 12px; white-space: nowrap; }
.dir-tag { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: color-mix(in srgb, var(--primary-color) 15%, transparent); color: var(--primary-color); }

.stats-row { display: flex; gap: 24px; margin-bottom: 12px; font-size: 14px; color: var(--text-color-secondary); }
.stats-row strong { color: var(--text-color); }

.mode-fieldset {
  border: 1px solid var(--surface-border); border-radius: 8px;
  padding: 10px 14px; margin: 8px 0; background: var(--surface-ground);
}
.mode-fieldset legend { padding: 0 6px; font-size: 12px; color: var(--text-color-secondary); }
.mode-radio { display: flex; gap: 10px; padding: 4px 0; cursor: pointer; }
.mode-radio span { display: flex; flex-direction: column; }
.mode-radio strong { font-size: 13px; color: var(--text-color); }
.mode-radio em { font-size: 11px; color: var(--text-color-secondary); font-style: normal; }

.cleanup-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-color); cursor: pointer; margin-bottom: 8px; }

.step-indicator { font-size: 13px; color: var(--text-color-secondary); margin-bottom: 8px; }
.current-dir, .current-file {
  font-size: 12px; color: var(--text-color-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 8px;
}
.current-dir { color: var(--text-color); }

.progress-section { margin-bottom: 12px; }
.progress-bar { height: 8px; background: var(--surface-ground); border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
.progress-fill { height: 100%; background: var(--primary-color); border-radius: 4px; transition: width 0.3s; }
.progress-bar.indeterminate .progress-fill { width: 40%; animation: indeterminate 1.5s ease-in-out infinite; }
@keyframes indeterminate { 0% { margin-left: 0; } 50% { margin-left: 60%; } 100% { margin-left: 0; } }
.progress-text { font-size: 12px; color: var(--text-color-secondary); }

.result-stats { display: flex; gap: 32px; margin-bottom: 20px; }
.stat { display: flex; flex-direction: column; align-items: center; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--primary-color); }
.stat-label { font-size: 12px; color: var(--text-color-secondary); }

.error-msg { color: var(--danger-color, #d33); font-size: 14px; margin-bottom: 16px; }

.dialog-actions { display: flex; gap: 10px; margin-top: 16px; }
.btn-primary, .btn-secondary, .btn-danger {
  padding: 8px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; border: none;
}
.btn-primary { background: var(--primary-color); color: #fff; font-weight: 600; }
.btn-primary:hover { opacity: 0.9; }
.btn-secondary { background: var(--surface-ground); border: 1px solid var(--surface-border); color: var(--text-color); }
.btn-secondary:hover { background: var(--surface-hover); }
.btn-danger { background: var(--danger-color, #d33); color: #fff; font-weight: 600; }
.btn-danger:hover { opacity: 0.9; }
</style>
```

- [ ] **Step 4: Type-check**

```bash
cd frontend && npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/scan.ts frontend/src/stores/scan.ts frontend/src/components/dialogs/ScanDialog.vue
git commit -m "feat(frontend): unified multi-phase scan dialog with step tracker and full-clean mode"
```

---

## Phase G — Frontend: production build verification

### Task G1: Run frontend build

**Files:** none modified.

- [ ] **Step 1: Build**

```bash
cd frontend && npm run build
```

Expected: vue-tsc passes, Vite build completes with no errors.

- [ ] **Step 2: Final commit**

```bash
git status
# If only frontend/dist/ shows up and it's gitignored, nothing to commit.
```

---

## Self-Review Checklist

Run these before declaring the plan implemented:

- [ ] **Spec coverage:** every locked-in decision has a task — subprocess switch (A1), startup wiring (A2), build/cancel endpoints (A1, A3), search rewrite (A1), auto-trigger (B1), persistent auto-index toggle (A1, B1, E3), compute-pHash toggle (A1, E3), favorites preservation (C1, C2), unified dialog with step tracker (F1).
- [ ] **Backend regression:** flake8/black/mypy/pytest all green (Task D1).
- [ ] **Frontend regression:** `npm run build` succeeds (Task G1).
- [ ] **Manual smoke test:** scan a small directory, verify Prepare → Confirm → Scan → Embed → Done; toggling auto-index off in Settings prevents the embed phase from auto-starting; full-clean preserves favorites; Build Index in Settings works; Cancel during embedding works.
