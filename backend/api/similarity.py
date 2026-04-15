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
