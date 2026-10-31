"""FastAPI dependency injection for shared resources."""

import contextlib
from threading import Lock
from typing import TYPE_CHECKING, ContextManager, Optional

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.utils.app_paths import get_data_dir, get_thumbnail_cache_dir

from backend.config import load_app_config
from backend.services.background.registry import INTERACTIVE_GRACE_S, ProcessRegistry

if TYPE_CHECKING:
    from backend.services.background.embedding import EmbeddingProcess
    from backend.services.background.tagging import TaggingProcess

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
    """Process-wide DatabaseManager singleton.

    Creating it runs the schema migrations. The server's lifespan creates it
    before accepting any request (``backend/main.py``), so a failed migration
    (``MigrationError``) stops startup with its owner-facing message instead
    of failing request after request; the singleton then stays unset and the
    next start retries the migration."""
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


_registry_singleton: Optional[ProcessRegistry] = None


def set_registry(registry: Optional[ProcessRegistry]) -> None:
    """Install the background-process registry (FastAPI lifespan)."""
    global _registry_singleton
    _registry_singleton = registry


def get_registry() -> ProcessRegistry:
    if _registry_singleton is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="registry not initialized")
    return _registry_singleton


def interactive_vlm_hold(by: str) -> ContextManager[None]:
    """Hold Tagging for one interactive VLM request plus its grace (spec 7).

    A no-op when no registry is installed (tests, a half-started server)."""
    registry = _registry_singleton
    if registry is None:
        return contextlib.nullcontext()
    return registry.hold_for(["tagging"], by=by, grace_s=INTERACTIVE_GRACE_S)


_tagging_singleton: Optional["TaggingProcess"] = None


def set_tagging_process(process: Optional["TaggingProcess"]) -> None:
    """Install the Tagging process (FastAPI lifespan)."""
    global _tagging_singleton
    _tagging_singleton = process


def get_tagging_process() -> Optional["TaggingProcess"]:
    return _tagging_singleton


_embedding_singleton: Optional["EmbeddingProcess"] = None


def set_embedding_process(process: Optional["EmbeddingProcess"]) -> None:
    """Install the Embedding process (FastAPI lifespan)."""
    global _embedding_singleton
    _embedding_singleton = process


def get_embedding_process() -> Optional["EmbeddingProcess"]:
    return _embedding_singleton
