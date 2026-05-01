"""Embedding management endpoints."""

import asyncio

from fastapi import APIRouter

from backend.dependencies import get_db

router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])


@router.get("/status")
async def get_status():
    """Get embedding index status and stats."""
    db = get_db()
    stats = await asyncio.to_thread(db.get_embedding_stats)
    return stats
