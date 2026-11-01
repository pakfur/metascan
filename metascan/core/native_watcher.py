"""Native file notifications for the always-on import (``watchdog``).

Behind ``watchdog``: ReadDirectoryChangesW on Windows, inotify on Linux, FSEvents
on macOS. Pure, like the poller: no database, no ingest, no backend import.

A notification is a hint and nothing more: "look at this path now". It never
adds or removes anything; the consumer hands it to ``FolderPoller.poll_paths``,
which applies the ordinary rules. So a lost event costs time, not a file, and
the poller's walk stays the backstop for what this cannot see: a buffer that
overflowed, a change made while the server was down, a drive that left and came
back.

Nothing here is trusted to be working. A silent mount is never armed
(``native_watch_reliable``), and ``sync`` rebuilds an observer whose thread has
died, which a pulled drive does without a word. Any failure leaves the folder to
the poller.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from metascan.core.folder_poller import FolderSpec
from metascan.utils.fs_compat import native_watch_reliable

logger = logging.getLogger(__name__)

JOIN_TIMEOUT_S = 2.0

_READS = ("opened", "closed_no_write")


class _Handler:
    """Turns events into hints, on the observer's thread. A directory's own
    "modified" is dropped: it fires for every child, and the look it would
    start walks the whole folder. A directory's other events are kept (a folder
    moved in raises only its own)."""

    def __init__(self, on_path: Callable[[str], None]) -> None:
        self._on_path = on_path

    def dispatch(self, event: Any) -> None:
        kind = event.event_type
        if kind in _READS or (event.is_directory and kind == "modified"):
            return
        try:
            self._on_path(str(event.src_path))
            dest = getattr(event, "dest_path", "")
            if dest:
                self._on_path(str(dest))
        except Exception:
            # An exception here would end the observer's thread for good.
            logger.exception("a file notification could not be handled")


def _default_observer() -> Any:
    from watchdog.observers import Observer

    return Observer()


class NativeWatcher:
    def __init__(
        self,
        on_path: Callable[[str], None],
        reliable: Callable[[str], bool] = native_watch_reliable,
        make_observer: Callable[[], Any] = _default_observer,
    ) -> None:
        self._handler = _Handler(on_path)
        self._reliable = reliable
        self._make_observer = make_observer
        self._observer: Any = None
        self._watches: Dict[FolderSpec, Any] = {}
        self._warned: Dict[FolderSpec, str] = {}
        self._backend_warned = False

    @property
    def armed(self) -> Tuple[FolderSpec, ...]:
        return tuple(self._watches)

    def sync(self, folders: Sequence[FolderSpec]) -> List[FolderSpec]:
        """Make the armed set match ``folders`` as far as it can, and return
        what is armed. Blocking (arming a tree can take a while): a thread's
        job. Safe to call on every walk: it does nothing when all is well."""
        if self._observer is not None and not self._healthy():
            logger.warning("the file watcher stopped; rebuilding it")
            self.close()
        wanted = [f for f in folders if self._trusted(f)]
        for spec in [s for s in self._watches if s not in wanted]:
            self._disarm(spec)
        for spec in wanted:
            if spec not in self._watches:
                self._arm(spec)
        return [s for s in folders if s in self._watches]

    def close(self) -> None:
        observer, self._observer = self._observer, None
        self._watches.clear()
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(JOIN_TIMEOUT_S)
        except Exception as exc:
            logger.warning("the file watcher did not stop: %s", type(exc).__name__)

    def _healthy(self) -> bool:
        try:
            emitters = list(self._observer.emitters)
            return self._observer.is_alive() and all(e.is_alive() for e in emitters)
        except Exception:
            return False

    def _trusted(self, spec: FolderSpec) -> bool:
        try:
            return self._reliable(spec.filepath)
        except Exception as exc:
            logger.warning(
                "could not tell whether a folder can be watched: %s",
                type(exc).__name__,
            )
            return False

    def _ensure_observer(self) -> Optional[Any]:
        if self._observer is None:
            try:
                observer = self._make_observer()
                observer.start()
            except Exception as exc:
                # ImportError included: watchdog is a dependency, not a need.
                if not self._backend_warned:
                    self._backend_warned = True
                    logger.warning(
                        "no native file watcher, polling only: %s",
                        type(exc).__name__,
                    )
                return None
            self._observer = observer
        return self._observer

    def _arm(self, spec: FolderSpec) -> None:
        observer = self._ensure_observer()
        if observer is None:
            return
        try:
            self._watches[spec] = observer.schedule(
                self._handler, spec.filepath, recursive=spec.search_subfolders
            )
            self._warned.pop(spec, None)
        except Exception as exc:
            # OSError: the folder went away, or the inotify watch limit.
            name = type(exc).__name__
            if self._warned.get(spec) != name:
                self._warned[spec] = name
                logger.warning("a folder could not be watched, polling it: %s", name)

    def _disarm(self, spec: FolderSpec) -> None:
        watch = self._watches.pop(spec)
        try:
            self._observer.unschedule(watch)
        except Exception as exc:
            logger.debug("unschedule failed: %s", type(exc).__name__)
