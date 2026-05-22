"""YAML-backed prompt store with optional file-system hot reload.

Loads ``data/meta_prompt.yml`` at construction and exposes its keys via
:meth:`PromptStore.get`. When :meth:`start_watching` is called the store
installs a ``watchdog`` observer on the parent directory; subsequent
edits trigger a re-parse and notify any callbacks registered with
:meth:`on_reload` so caches downstream of the raw prompt strings can
invalidate themselves.

A module-level singleton :data:`prompt_store` is exposed so call sites
can do ``from metascan.core.prompt_store import prompt_store`` without
threading the instance around manually.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import yaml  # type: ignore[import-untyped]

from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)


DEFAULT_FILENAME = "meta_prompt.yml"


class PromptStoreError(RuntimeError):
    """Raised when the YAML file is missing, unparseable, or empty."""


class PromptStore:
    """Thread-safe loader for YAML-backed prompt strings."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._prompts: Dict[str, str] = {}
        self._listeners: List[Callable[[], None]] = []
        # ``Optional[Any]`` rather than the concrete watchdog types so the
        # import can stay lazy inside :meth:`start_watching`.
        self._observer: Optional[Any] = None
        self._handler: Optional[Any] = None
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    # -- Loading -----------------------------------------------------------

    def _load(self) -> None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise PromptStoreError(f"prompt file not found: {self._path}") from e

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise PromptStoreError(f"failed to parse {self._path}: {e}") from e

        if not isinstance(data, dict) or not data:
            raise PromptStoreError(
                f"{self._path} must contain a non-empty mapping at the top level"
            )

        cleaned: Dict[str, str] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                raise PromptStoreError(
                    f"{self._path}: non-string key {k!r}; all top-level keys "
                    "must be string identifiers"
                )
            if not isinstance(v, str):
                raise PromptStoreError(
                    f"{self._path}: value for {k!r} is {type(v).__name__}, "
                    "expected string"
                )
            cleaned[k] = v

        with self._lock:
            self._prompts = cleaned

    def reload(self) -> None:
        """Re-read the YAML file and fire registered listeners."""
        try:
            self._load()
        except PromptStoreError as e:
            logger.error("prompt-store reload failed; keeping previous values: %s", e)
            return
        logger.info(
            "prompt-store reloaded %d entries from %s", len(self._prompts), self._path
        )
        self._fire_listeners()

    def _fire_listeners(self) -> None:
        # Copy under lock so a listener that registers a peer can't mutate
        # mid-iteration.
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb()
            except Exception:
                logger.exception("prompt-store reload listener raised")

    # -- Access ------------------------------------------------------------

    def get(self, key: str) -> str:
        """Return the prompt string for ``key``.

        Raises :class:`KeyError` with a descriptive message if the key is
        missing — surfaces typos early rather than silently returning the
        empty string.
        """
        with self._lock:
            try:
                return self._prompts[key]
            except KeyError:
                raise KeyError(
                    f"prompt {key!r} not found in {self._path}; "
                    f"available keys: {sorted(self._prompts)}"
                ) from None

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._prompts.keys())

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._prompts

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._prompts.keys()))

    # -- Listeners ---------------------------------------------------------

    def on_reload(self, callback: Callable[[], None]) -> None:
        """Register ``callback`` to fire after every successful reload.

        Callbacks run in the watchdog observer thread, so they should be
        cheap (typically clearing a cache). Exceptions are logged and
        swallowed so a single broken listener can't break the others.
        """
        with self._lock:
            self._listeners.append(callback)

    # -- Watcher -----------------------------------------------------------

    def start_watching(self) -> None:
        """Begin watching the YAML file for changes.

        Safe to call multiple times — subsequent calls are no-ops. The
        observer thread is daemon-spawned so it won't block process exit.
        """
        if self._observer is not None:
            return

        # Imported lazily so tests that don't exercise watching avoid the
        # watchdog dependency boot path. We use ``PollingObserver`` rather
        # than the inotify/FSEvents/ReadDirectoryChangesW backends because
        # the prompt YAML often lives on a WSL2 drvfs mount
        # (``/mnt/c/...``) where kernel filesystem events don't propagate
        # from Windows-side edits — the inotify-based Observer silently
        # misses every change. Polling adds a single scandir per second
        # against one small directory, which is cheap.
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers.polling import PollingObserver

        store = self
        target_path = self._path.resolve()

        class _Handler(FileSystemEventHandler):  # type: ignore[misc]
            def _matches(self, event: FileSystemEvent) -> bool:
                if event.is_directory:
                    return False
                src = getattr(event, "src_path", "") or ""
                dest = getattr(event, "dest_path", "") or ""
                try:
                    if src and Path(src).resolve() == target_path:
                        return True
                    if dest and Path(dest).resolve() == target_path:
                        return True
                except OSError:
                    return False
                return False

            def on_modified(self, event: FileSystemEvent) -> None:
                if self._matches(event):
                    logger.debug(
                        "prompt-store: detected modification of %s", target_path
                    )
                    store.reload()

            def on_created(self, event: FileSystemEvent) -> None:
                if self._matches(event):
                    logger.debug("prompt-store: detected creation of %s", target_path)
                    store.reload()

            def on_moved(self, event: FileSystemEvent) -> None:
                # Editors that write via rename ("atomic save") show up as
                # a move into the target path.
                if self._matches(event):
                    logger.debug("prompt-store: detected move onto %s", target_path)
                    store.reload()

        observer = PollingObserver(timeout=1.0)
        handler = _Handler()
        # Watch the directory rather than the file — most editors replace
        # the file (write to tmp + rename) on save, which file-level watches
        # miss on some platforms (notably WSL).
        observer.schedule(handler, str(self._path.parent), recursive=False)
        observer.daemon = True
        observer.start()

        self._observer = observer
        self._handler = handler
        logger.info("prompt-store watching %s", self._path)

    def stop_watching(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception:
            logger.exception("prompt-store: failed to stop observer cleanly")
        finally:
            self._observer = None
            self._handler = None


# --- Module-level singleton ----------------------------------------------


def _default_path() -> Path:
    return get_data_dir() / DEFAULT_FILENAME


_singleton: Optional[PromptStore] = None
_singleton_lock = threading.Lock()


def get_prompt_store() -> PromptStore:
    """Return the lazily-constructed singleton.

    Tests that need an isolated store should construct :class:`PromptStore`
    directly with their own path rather than going through this helper.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PromptStore(_default_path())
    return _singleton


def reset_singleton_for_tests() -> None:
    """Drop the cached singleton so the next ``get_prompt_store()`` rebuilds it.

    Hooked into pytest fixtures that swap the data directory; never call
    this from runtime code.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.stop_watching()
        _singleton = None
