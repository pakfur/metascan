"""FastAPI dependency injection for shared resources."""

from functools import lru_cache

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.utils.app_paths import get_data_dir, get_thumbnail_cache_dir

from backend.config import load_app_config


@lru_cache()
def get_db() -> DatabaseManager:
    """Singleton DatabaseManager instance."""
    return DatabaseManager(get_data_dir())


@lru_cache()
def get_thumbnail_cache() -> ThumbnailCache:
    """Singleton ThumbnailCache instance."""
    config = load_app_config()
    size = tuple(config.get("thumbnail_size", [256, 256]))
    return ThumbnailCache(get_thumbnail_cache_dir(), thumbnail_size=size)
