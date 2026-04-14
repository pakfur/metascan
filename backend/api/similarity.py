"""Similarity search and settings endpoints."""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config import load_app_config, save_app_config
from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService
from backend.ws.manager import ws_manager
from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager
from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/similarity", tags=["similarity"])

# Lazy-loaded singletons
_embedding_manager: Optional[EmbeddingManager] = None
_faiss_manager: Optional[FaissIndexManager] = None
_index_task: Optional[asyncio.Task] = None


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
    global _faiss_manager
    if _faiss_manager is None:
        index_dir = get_data_dir() / "faiss_index"
        _faiss_manager = FaissIndexManager(index_dir)
    return _faiss_manager


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


@router.post("/search")
async def search_similar(
    body: SimilaritySearchRequest,
    service: MediaService = Depends(_get_service),
):
    """Search for media similar to the given file path using FAISS."""
    em = _get_embedding_manager()
    fm = _get_faiss_manager()

    results = await asyncio.to_thread(
        fm.search_similar,
        body.file_path,
        em,
        threshold=body.threshold,
        max_results=body.max_results,
    )

    # Convert results to dicts with similarity scores
    output = []
    for file_path, score in results:
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

    results = await asyncio.to_thread(
        fm.search_by_text,
        body.query,
        em,
        max_results=body.max_results,
    )

    output = []
    for file_path, score in results:
        media = await service.get_media(file_path)
        if media:
            d = service.media_to_dict(media)
            d["similarity_score"] = float(score)
            output.append(d)

    return output


@router.get("/settings")
async def get_similarity_settings():
    """Get current similarity/embedding settings."""
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
        "embedding_stats": stats,
    }


@router.put("/settings")
async def update_similarity_settings(body: SimilaritySettingsUpdate):
    """Update similarity/embedding settings."""
    global _embedding_manager
    config = load_app_config()
    sim_config = config.setdefault("similarity", {})

    changed_model = False
    for field, value in body.dict(exclude_none=True).items():
        if field in ("clip_model", "device") and sim_config.get(field) != value:
            changed_model = True
        sim_config[field] = value

    save_app_config(config)

    # Reset embedding manager if model/device changed
    if changed_model:
        _embedding_manager = None

    return sim_config


@router.post("/index/build")
async def build_index(rebuild: bool = False):
    """Build or rebuild the FAISS similarity index. Progress via WebSocket."""
    global _index_task

    if _index_task and not _index_task.done():
        raise HTTPException(status_code=409, detail="Index build already in progress")

    _index_task = asyncio.create_task(_run_index_build(rebuild))
    return {"status": "started"}


async def _run_index_build(rebuild: bool) -> None:
    """Run FAISS index building in a background task."""
    db = get_db()
    em = _get_embedding_manager()
    fm = _get_faiss_manager()

    await ws_manager.broadcast("embedding", "started", {"rebuild": rebuild})

    try:
        if rebuild:
            await asyncio.to_thread(db.clear_embeddings)

        unembedded = await asyncio.to_thread(db.get_unembedded_file_paths)
        total = len(unembedded)

        await ws_manager.broadcast(
            "embedding", "progress", {"current": 0, "total": total}
        )

        # Process in batches
        batch_size = 32
        processed = 0
        for i in range(0, total, batch_size):
            batch = unembedded[i : i + batch_size]
            await asyncio.to_thread(fm.add_files, batch, em)

            config = load_app_config()
            clip_model = config.get("similarity", {}).get("clip_model", "small")
            await asyncio.to_thread(db.mark_embedded, batch, clip_model)

            processed += len(batch)
            await ws_manager.broadcast(
                "embedding", "progress", {"current": processed, "total": total}
            )

        await asyncio.to_thread(fm.save_index)
        await ws_manager.broadcast("embedding", "complete", {"total": processed})

    except Exception as e:
        logger.error(f"Index build error: {e}", exc_info=True)
        await ws_manager.broadcast("embedding", "error", {"message": str(e)})
