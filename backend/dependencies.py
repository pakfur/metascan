"""FastAPI dependency injection for shared resources."""

from threading import Lock
from typing import Optional

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.utils.app_paths import get_data_dir, get_thumbnail_cache_dir

from backend.config import load_app_config

# `functools.lru_cache` has a race: it releases the cache lock before
# calling the wrapped function, so two concurrent misses with the same
# key both execute the body. On FastAPI startup, the first few incoming
# requests resolve their `Depends(get_db)` in parallel on the thread
# pool — with lru_cache that produced *two* DatabaseManager inits (each
# running the schema migration on a big SQLite file on WSL /mnt/c).
# A classic double-checked lock gives a real singleton.

_db_singleton: Optional[DatabaseManager] = None
_db_lock = Lock()

_thumbnail_cache_singleton: Optional[ThumbnailCache] = None
_thumbnail_cache_lock = Lock()


def get_db() -> DatabaseManager:
    """Process-wide DatabaseManager singleton."""
    global _db_singleton
    if _db_singleton is not None:
        return _db_singleton
    with _db_lock:
        if _db_singleton is None:
            _db_singleton = DatabaseManager(get_data_dir())
    return _db_singleton


def get_thumbnail_cache() -> ThumbnailCache:
    """Process-wide ThumbnailCache singleton."""
    global _thumbnail_cache_singleton
    if _thumbnail_cache_singleton is not None:
        return _thumbnail_cache_singleton
    with _thumbnail_cache_lock:
        if _thumbnail_cache_singleton is None:
            config = load_app_config()
            size = tuple(config.get("thumbnail_size", [256, 256]))
            _thumbnail_cache_singleton = ThumbnailCache(
                get_thumbnail_cache_dir(), thumbnail_size=size
            )
    return _thumbnail_cache_singleton
