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
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.embedding_queue import EmbeddingQueue
from metascan.core.inference_client import InferenceClient, InferenceError
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/similarity", tags=["similarity"])

# Lazy-loaded singletons
_faiss_manager: Optional[FaissIndexManager] = None
_embedding_queue: Optional[EmbeddingQueue] = None
_embed_poll_task: Optional[asyncio.Task] = None
_inference_client: Optional[InferenceClient] = None
_EMBED_POLL_INTERVAL_SECONDS = 0.5


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


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


async def _ensure_worker_ready(client: InferenceClient) -> None:
    """Belt-and-suspenders: make sure the worker is spawned with the
    currently-configured CLIP model before a search endpoint tries to use
    it. Without this, a search that arrives before the (optional) startup
    preload finishes would just block on ``_wait_ready`` forever because
    nothing else triggers a spawn."""
    sim_cfg = load_app_config().get("similarity", {}) or {}
    model_key = str(sim_cfg.get("clip_model") or "small")
    device = str(sim_cfg.get("device") or "auto")
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
    global _embed_poll_task
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


@router.post("/search")
async def search_similar(
    body: SimilaritySearchRequest,
    service: MediaService = Depends(_get_service),
):
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
    try:
        await _inference_client.reload(model_key=model_key, device=device)
    except Exception:
        logger.exception("Inference client reload failed")


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
