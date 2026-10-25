"""Similarity search and embedding-index endpoints (subprocess pattern)."""

import asyncio
import concurrent.futures
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import load_app_config, save_app_config
from backend.dependencies import get_db
from backend.services.scan_dispatch import preferred_vlm_model_id, should_tag_with_vlm
from backend.ws.manager import ws_manager
from backend.services.gpu_lock import GPU_FREE, gpu_busy_http_error
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.embedding_queue import EmbeddingQueue
from metascan.core.gpu_guard import gpu_reserved
from metascan.core.hardware import detect_hardware
from metascan.core.inference_client import InferenceClient, InferenceError
from metascan.utils.app_paths import get_data_dir
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/similarity", tags=["similarity"])

# Lazy-loaded singletons
_faiss_manager: Optional[FaissIndexManager] = None
_embedding_queue: Optional[EmbeddingQueue] = None
_embed_poll_task: Optional[asyncio.Task] = None
_inference_client: Optional[InferenceClient] = None
_EMBED_POLL_INTERVAL_SECONDS = 0.5

# Called (from the embedding poller's worker thread) after every completed
# embedding run; the lifespan installs the taste scorer's rescore here.
_on_embedding_complete: Optional[Callable[[], None]] = None

# Tracks whether the *current* (or most recent) scan was started with VLM
# tagging enabled.  Set by build_index; read by the on_complete callback.
_current_scan_tag_with_vlm: bool = False
# (general client, its ``_explicit_stops``) captured when that scan
# started: the post-scan drain is a background re-ensure, so an explicit
# unload (ComfyUI render, /unload) landing after the scan began must win.
_vlm_scan_stops: Optional[Tuple[Any, int]] = None

# The server's event loop, attached from the lifespan.  EmbeddingQueue
# callbacks fire on a worker thread (the poller uses asyncio.to_thread), so
# coroutines they need must be handed to this loop thread-safely.
_server_loop: Optional[asyncio.AbstractEventLoop] = None
# Touched only on the loop thread (inside _drain), so no lock is needed.
_vlm_drain_running: bool = False
# A request that arrived mid-drain: drain_once snapshots the pending file at
# its start, so lines appended meanwhile need one more pass.
_vlm_drain_rerun: bool = False
# The in-flight scheduled drain, cancelled on shutdown.
_vlm_drain_future: Optional["concurrent.futures.Future[None]"] = None
# True while ComfyUI generation work is in flight (installed from the
# lifespan: auto_index.generation_busy). The post-embedding VLM drain waits
# for it to settle -- no VLM tagging competes with a render for the GPU.
_generation_busy: Optional[Callable[[], bool]] = None
_VLM_DRAIN_DEFER_POLL_S = 5.0


def _capture_vlm_scan_stops() -> Optional[Tuple[Any, int]]:
    """Snapshot the general client's explicit-stop count at scan start."""
    from backend.api.vlm import get_vlm_client

    client = get_vlm_client()
    stops = getattr(client, "_explicit_stops", None)
    if client is None or not isinstance(stops, int):
        return None
    return (client, stops)


def _vlm_stops_since_scan(client: Any) -> Optional[int]:
    """The ``_explicit_stops`` value the drain must still see to (re)start
    ``client``, or None (no snapshot, or the role rebuilt the client since,
    so the scan never saw it unloaded)."""
    snap = _vlm_scan_stops
    if snap is None or snap[0] is not client:
        return None
    return snap[1]


def cancel_vlm_drain() -> None:
    """Shutdown hook: cancel an in-flight post-embedding drain."""
    global _vlm_drain_future
    fut = _vlm_drain_future
    _vlm_drain_future = None
    if fut is not None and not fut.done():
        fut.cancel()


def set_server_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Attach (or clear) the loop used to run the post-embedding VLM drain."""
    global _server_loop
    _server_loop = loop


def set_generation_busy_probe(probe: Optional[Callable[[], bool]]) -> None:
    """Install (or clear) the "generation in flight" probe the post-embedding
    VLM drain waits on."""
    global _generation_busy
    _generation_busy = probe


def _generation_in_flight() -> bool:
    probe = _generation_busy
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:
        # A probe that cannot answer counts as busy: check again later.
        logger.debug("generation busy probe failed", exc_info=True)
        return True


def generation_in_flight() -> bool:
    """Public form of the generation-busy probe (other background VLM jobs
    wait on the same signal)."""
    return _generation_in_flight()


async def _wait_for_generation_to_settle() -> bool:
    """Wait (on the loop) while generation work is in flight. True when it
    had to wait."""
    if not _generation_in_flight():
        return False
    logger.info(
        "VLM drain after scan deferred: ComfyUI generation in flight; "
        "it runs when the generation settles"
    )
    while _generation_in_flight():
        await asyncio.sleep(_VLM_DRAIN_DEFER_POLL_S)
    return True


def set_embedding_complete_hook(hook: Optional[Callable[[], None]]) -> None:
    """Install (or clear, with None) the post-embedding-run hook. It runs
    on a worker thread, so it must only schedule work, never block."""
    global _on_embedding_complete
    _on_embedding_complete = hook


def get_inference_client() -> InferenceClient:
    """Return the module-level ``InferenceClient`` created by the server
    lifespan. Raises 503 if the client hasn't been initialized yet (e.g.
    during very early startup)."""
    if _inference_client is None:
        raise HTTPException(status_code=503, detail="inference client not initialized")
    return _inference_client


def set_inference_client(client: InferenceClient) -> None:
    """Install the ``InferenceClient`` singleton. Called once by the
    FastAPI lifespan after the client has been constructed."""
    global _inference_client
    _inference_client = client


def clip_worker_settings() -> Tuple[str, str]:
    """``(model_key, device)`` the live CLIP inference worker runs with:
    the configured ``similarity.clip_model`` / ``device``. Searches start
    the worker with these, and so does the tombstone re-embedder, so
    neither restarts the other's worker. Blocking (reads config.json)."""
    sim_cfg = load_app_config().get("similarity", {}) or {}
    return (
        str(sim_cfg.get("clip_model") or "small"),
        str(sim_cfg.get("device") or "auto"),
    )


async def _ensure_worker_ready(client: InferenceClient) -> None:
    """Belt-and-suspenders: make sure the worker is spawned with the
    currently-configured CLIP model before a search endpoint tries to use
    it. Without this, a search that arrives before the (optional) startup
    preload finishes would just block on ``_wait_ready`` forever because
    nothing else triggers a spawn."""
    model_key, device = clip_worker_settings()
    try:
        await client.ensure_started(model_key=model_key, device=device)
    except Exception:
        logger.exception("Inference worker start failed")


def _get_faiss_manager() -> FaissIndexManager:
    """FAISS index manager. Shares its directory with the EmbeddingQueue
    so the index written by the worker is the index the search endpoints
    read."""
    global _faiss_manager
    if _faiss_manager is None:
        fm = FaissIndexManager(_get_embedding_queue().index_dir)
        fm.load()  # No-op if no on-disk index exists
        _faiss_manager = fm
        if fm.is_loaded:
            _start_tombstone_backfill(fm)
    return _faiss_manager


def _start_tombstone_backfill(fm: FaissIndexManager) -> None:
    """Fill tombstones whose CLIP vector was NULL (deleted while the index
    wasn't in memory) from the freshly loaded index. Runs on a daemon
    thread: callers of _get_faiss_manager may be on the event loop, and
    the backfill takes the DB lock (never while reading FAISS)."""

    def _run() -> None:
        try:
            from backend.services.judgments_service import (
                backfill_tombstone_embeddings,
            )

            backfill_tombstone_embeddings(get_db(), fm)
        except Exception:
            logger.warning("Tombstone embedding backfill failed", exc_info=True)

    threading.Thread(
        target=_run, name="tombstone-embedding-backfill", daemon=True
    ).start()


def embedding_index_dir() -> Path:
    """Directory holding the FAISS index the embedding worker writes."""
    return _get_embedding_queue().index_dir


def warm_faiss_index() -> None:
    """Construct the FaissIndexManager singleton and load the on-disk
    index. Blocking — must be called from a worker thread. Safe to invoke
    multiple times; returns immediately once the singleton is populated."""
    _get_faiss_manager()


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
                "vocab_current": raw.get("vocab_current", 0),
                "vocab_total": raw.get("vocab_total", 0),
            },
        )

    def on_complete(total: int) -> None:
        # Reload the FAISS index so search endpoints see the new vectors.
        try:
            _reload_faiss_after_index_build()
        except Exception as e:
            logger.exception(f"Failed to reload FAISS after index build: {e}")
        ws_manager.broadcast_sync("embedding", "complete", {"total": total})
        hook = _on_embedding_complete
        if hook is not None:
            try:
                hook()
            except Exception:
                logger.exception("Post-embedding hook failed")
        if _current_scan_tag_with_vlm:
            _schedule_vlm_drain_after_complete(eq)

    def on_error(msg: str) -> None:
        ws_manager.broadcast_sync("embedding", "error", {"message": msg})

    eq.on_progress = on_progress
    eq.on_complete = on_complete
    eq.on_error = on_error


# Serialises installs of a rebuilt FAISS manager; ``_faiss_reload_seq``
# numbers the reloads so a slow older reload never replaces a newer one.
_faiss_swap_lock = threading.Lock()
_faiss_reload_seq = 0
_faiss_installed_seq = 0


def _rebuild_faiss_manager(seq: int) -> None:
    """Load a fresh FaissIndexManager from disk and swap it in. Blocking:
    runs in a worker thread, never under the DB lock. The old manager
    stays installed (and searchable) until the new one is loaded."""
    global _faiss_manager, _faiss_installed_seq
    fm = FaissIndexManager(_get_embedding_queue().index_dir)
    fm.load()  # No-op if no on-disk index exists
    with _faiss_swap_lock:
        if seq <= _faiss_installed_seq:
            return  # a newer reload already installed its manager
        _faiss_installed_seq = seq
        _faiss_manager = fm
    if fm.is_loaded:
        _start_tombstone_backfill(fm)


def _reload_faiss_after_index_build() -> None:
    """Refresh the in-memory FAISS index after an embedding run.

    Called from ``on_complete`` (the embedding poller's worker thread).
    When a manager is in memory it is rebuilt off the loop and swapped in
    atomically -- readers that never load from disk (taste examples, the
    tombstone path) keep seeing the old index meanwhile instead of None,
    which used to silently downgrade every later guided write to ``base``.
    With no manager in memory nothing loads: search stays lazy."""
    global _faiss_reload_seq
    if _faiss_manager is None:
        return
    with _faiss_swap_lock:
        _faiss_reload_seq += 1
        seq = _faiss_reload_seq
    loop = _server_loop
    if loop is not None and not loop.is_closed():
        coro = asyncio.to_thread(_rebuild_faiss_manager, seq)
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).add_done_callback(
                _log_faiss_reload_failure
            )
            return
        except RuntimeError:
            coro.close()
    # No usable server loop (tests, shutdown): we are on a worker thread
    # already, so load here.
    _rebuild_faiss_manager(seq)


def _log_faiss_reload_failure(fut: "concurrent.futures.Future[None]") -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        logger.error("Failed to reload FAISS after index build", exc_info=exc)


def _schedule_vlm_drain_after_complete(eq: EmbeddingQueue) -> None:
    """Run VlmTagPump.drain_once() on the server loop after an embedding build.

    Called from the ``on_complete`` sync callback, which runs on a worker
    thread (the poller uses ``asyncio.to_thread``), so ``get_running_loop``
    would always raise here.  The coroutine is handed to the loop captured at
    startup via ``run_coroutine_threadsafe``.  With no usable loop we warn and
    leave ``vlm_pending.jsonl`` untouched for the next drain.  Drains never
    overlap: a request arriving mid-drain sets a rerun flag and the running
    drain makes one more pass (coalesced), so lines appended meanwhile are
    not stranded.
    """

    async def _drain() -> None:
        global _vlm_drain_running, _vlm_drain_rerun
        if _vlm_drain_running:
            _vlm_drain_rerun = True
            logger.info("VLM drain after scan: one already running, will rerun after")
            return
        _vlm_drain_running = True
        try:
            # Never tag while ComfyUI renders: wait for the generation to
            # settle (requests arriving meanwhile coalesce into the rerun).
            deferred = await _wait_for_generation_to_settle()
            from backend.api.vlm import get_vlm_client
            from backend.services.vlm_tag_pump import VlmTagPump

            from metascan.core.vlm_models import REGISTRY as VLM_REGISTRY
            from metascan.core.vlm_select import vlm_model_installed

            client = get_vlm_client()  # the general role's client
            if client is None:
                logger.debug("VLM drain skipped: no VlmClient installed")
                return
            # The general role's model when it is installed, else the
            # installed hardware pick (tagging never waits on a download).
            mid = getattr(client, "model_id", None) or getattr(
                client, "preferred_model_id", None
            )
            if not mid or not await asyncio.to_thread(vlm_model_installed, mid):
                mid = preferred_vlm_model_id(detect_hardware())
            if not mid:
                logger.debug("VLM drain skipped: no preferred VLM model for this host")
                return
            spec = VLM_REGISTRY.get(mid)
            # A drain that waited for a render: the render's own VLM unload
            # (an explicit stop) is behind it, so snapshot again now that
            # the GPU is free; otherwise an unload since the scan began wins.
            since = (
                getattr(client, "_explicit_stops", None)
                if deferred
                else _vlm_stops_since_scan(client)
            )
            if not isinstance(since, int):
                since = None
            pump = VlmTagPump(
                queue_dir=eq.index_dir,
                client=client,
                db=get_db(),
                model_id=mid,
                concurrency=spec.parallel_slots if spec is not None else 2,
                # An explicit unload (ComfyUI render, /unload) since the
                # scan started wins: the drain must not bring the VLM back.
                unless_stopped_since=since,
            )
            while True:
                _vlm_drain_rerun = False
                if await _wait_for_generation_to_settle():
                    stops = getattr(client, "_explicit_stops", None)
                    pump._unless_stopped_since = (
                        stops if isinstance(stops, int) else None
                    )
                drained = await pump.drain_once()
                logger.info(f"VLM drain after scan: tagged {drained} file(s)")
                if not _vlm_drain_rerun:
                    break
        except Exception:
            logger.exception("VLM drain after scan failed")
        finally:
            _vlm_drain_running = False
            _vlm_drain_rerun = False

    global _vlm_drain_future
    loop = _server_loop
    if loop is None or loop.is_closed():
        logger.warning(
            "VLM drain after scan not scheduled: server event loop unavailable; "
            "pending tags stay queued for the next drain"
        )
        return
    coro = _drain()
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        # A coalesced request returns at once; keep tracking the real drain.
        if _vlm_drain_future is None or _vlm_drain_future.done():
            _vlm_drain_future = fut
    except RuntimeError:
        coro.close()
        logger.warning(
            "VLM drain after scan not scheduled: server event loop closed; "
            "pending tags stay queued for the next drain"
        )


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
    global _embed_poll_task
    cancel_vlm_drain()
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
    max_results: Optional[int] = None


class ContentSearchRequest(BaseModel):
    query: str
    threshold: float = 0.0
    max_results: Optional[int] = None


class SimilaritySettingsUpdate(BaseModel):
    clip_model: Optional[str] = None
    device: Optional[str] = None
    search_results_count: Optional[int] = None
    video_keyframes: Optional[int] = None
    compute_phash_during_scan: Optional[bool] = None
    auto_index_after_scan: Optional[bool] = None


def _assert_dim_matches(fm: FaissIndexManager, vec_dim: int) -> None:
    """Raise 409 if the query vector's dim doesn't match the on-disk index.

    Most common cause: the user switched ``similarity.clip_model`` after
    the index was built. Previously the FAISS search would return zero
    results silently; now the UI can surface an actionable rebuild prompt.
    """
    index_dim = int(fm.meta.get("embedding_dim", 0)) or None
    if index_dim is None and fm._index is not None:  # type: ignore[truthy-bool]
        index_dim = int(getattr(fm._index, "d", 0)) or None
    if index_dim and index_dim != vec_dim:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "dim_mismatch",
                "message": (
                    "The current CLIP model produces embeddings of size "
                    f"{vec_dim}, but the FAISS index was built with size "
                    f"{index_dim}. Rebuild the index to use this model."
                ),
                "index_dim": index_dim,
                "model_dim": vec_dim,
                "index_model_key": fm.meta.get("model_key"),
            },
        )


@router.post("/search", dependencies=GPU_FREE)
async def search_similar(
    body: SimilaritySearchRequest,
) -> List[Dict[str, Any]]:
    """Search for media similar to the given file path using FAISS."""
    client = get_inference_client()
    fm = _get_faiss_manager()

    if not fm.is_loaded:
        raise HTTPException(status_code=503, detail="No embedding index loaded yet")

    await _ensure_worker_ready(client)

    is_video = Path(body.file_path).suffix.lower() in {
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
        ".avi",
    }

    try:
        if is_video:
            sim_cfg = load_app_config().get("similarity", {})
            keyframes = int(sim_cfg.get("video_keyframes", 4))
            vec = await client.encode_video(body.file_path, num_keyframes=keyframes)
        else:
            vec = await client.encode_image(body.file_path)
    except InferenceError as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to compute query embedding: {e}"
        )

    _assert_dim_matches(fm, int(vec.shape[0]))

    k = body.max_results if body.max_results else fm.size
    raw = await asyncio.to_thread(fm.search, vec, k)

    return [
        {"file_path": to_native_path(p), "similarity_score": float(s)}
        for p, s in raw
        if float(s) >= body.threshold
    ]


@router.post("/content-search", dependencies=GPU_FREE)
async def content_search(
    body: ContentSearchRequest,
) -> List[Dict[str, Any]]:
    """Search for media matching a text query using CLIP embeddings."""
    client = get_inference_client()
    fm = _get_faiss_manager()

    if not fm.is_loaded:
        raise HTTPException(status_code=503, detail="No embedding index loaded yet")

    await _ensure_worker_ready(client)

    try:
        vec = await client.encode_text(body.query)
    except InferenceError as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to compute text embedding: {e}"
        )

    _assert_dim_matches(fm, int(vec.shape[0]))

    k = body.max_results if body.max_results else fm.size
    raw = await asyncio.to_thread(fm.search, vec, k)

    return [
        {"file_path": to_native_path(p), "similarity_score": float(s)}
        for p, s in raw
        if float(s) >= body.threshold
    ]


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
        "search_results_count": sim_config.get("search_results_count", 100),
        "video_keyframes": sim_config.get("video_keyframes", 4),
        "compute_phash_during_scan": sim_config.get("compute_phash_during_scan", True),
        "auto_index_after_scan": sim_config.get("auto_index_after_scan", True),
        "embedding_stats": stats,
    }


@router.put("/settings")
async def update_similarity_settings(body: SimilaritySettingsUpdate):
    config = load_app_config()
    sim_config = config.setdefault("similarity", {})

    changed_model = False
    for field, value in body.dict(exclude_none=True).items():
        if field in ("clip_model", "device") and sim_config.get(field) != value:
            changed_model = True
        sim_config[field] = value

    save_app_config(config)

    if changed_model and _inference_client is not None:
        model_key = sim_config.get("clip_model", "small")
        device = sim_config.get("device", "auto")
        # Fire and forget: reloading the worker takes 15-30s, and the
        # server must respond to the PUT within the request timeout. The
        # client will broadcast loading/ready events on its status channel
        # as it reloads.
        asyncio.create_task(_reload_inference_client(model_key, device))

    return sim_config


async def _reload_inference_client(model_key: str, device: str) -> None:
    if _inference_client is None:
        return
    if gpu_reserved():
        # Training holds the GPU: the next search after it ends spawns the
        # worker with the new settings (ensure_started compares them).
        return
    try:
        await _inference_client.reload(model_key=model_key, device=device)
    except Exception:
        logger.exception("Inference client reload failed")


# ----- Index build/cancel (subprocess-driven) -----


@router.post("/index/build", dependencies=GPU_FREE)
async def build_index(rebuild: bool = False) -> Dict[str, Any]:
    """Start an embedding worker subprocess for unembedded (or all) files."""
    return await _start_index_build(rebuild=rebuild, allow_vlm_tagging=True)


async def start_auto_index_build() -> Dict[str, Any]:
    """Incremental build started by the server itself (after T2I ingests,
    see ``backend/services/auto_index.py``). It never queues VLM tagging:
    that would load the VLM on the GPU behind the owner's back between
    generation batches; the new files get CLIP tags instead."""
    return await _start_index_build(rebuild=False, allow_vlm_tagging=False)


def is_indexing() -> bool:
    """True while the embedding worker subprocess runs."""
    return _get_embedding_queue().is_indexing()


async def _start_index_build(rebuild: bool, allow_vlm_tagging: bool) -> Dict[str, Any]:
    global _current_scan_tag_with_vlm, _vlm_scan_stops

    if gpu_reserved():
        # Writer training holds the GPU (every entry point: the route, the
        # post-scan auto trigger, the Models tab rebuild, the auto index).
        raise gpu_busy_http_error()
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

    tag_with_vlm = allow_vlm_tagging and should_tag_with_vlm(detect_hardware())
    # Re-checked after the last await and before the scan-tag state is
    # touched: the lock may have been taken while the paths were read.
    if gpu_reserved():
        raise gpu_busy_http_error()
    _current_scan_tag_with_vlm = tag_with_vlm
    _vlm_scan_stops = _capture_vlm_scan_stops() if tag_with_vlm else None

    started = await asyncio.to_thread(
        eq.start_indexing,
        paths,
        sim.get("clip_model", "small"),
        sim.get("device", "auto"),
        str(get_data_dir()),
        bool(sim.get("compute_phash_during_scan", True)) and not rebuild,
        int(sim.get("video_keyframes", 4)),
        tag_with_vlm,
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
