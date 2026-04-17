"""Configuration management endpoints."""

import logging
import time

from fastapi import APIRouter

from backend.config import load_app_config, save_app_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config():
    """Get the current application configuration."""
    t_start = time.perf_counter()
    try:
        return load_app_config()
    finally:
        logger.info(
            "GET /api/config: total=%.1fms",
            (time.perf_counter() - t_start) * 1000,
        )


@router.put("/config")
async def update_config(body: dict):
    """Update the application configuration."""
    current = load_app_config()
    current.update(body)
    save_app_config(current)
    return current


@router.get("/config/themes")
async def list_themes():
    """List available themes."""
    # qt-material themes that have web equivalents
    return {
        "themes": [
            "light_blue_500",
            "dark_blue_500",
            "light_amber_500",
            "dark_amber_500",
            "light_teal_500",
            "dark_teal_500",
            "light_purple_500",
            "dark_purple_500",
        ]
    }
