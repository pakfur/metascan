"""Configuration management endpoints."""

from fastapi import APIRouter

from backend.config import load_app_config, save_app_config

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config():
    """Get the current application configuration."""
    return load_app_config()


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
