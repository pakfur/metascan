from pathlib import Path
from typing import Callable, Set, Optional, Any
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileMovedEvent

from metascan.core.scanner import Scanner

logger = logging.getLogger(__name__)


class MediaFileHandler(FileSystemEventHandler):
    def __init__(
        self,
        scanner: Scanner,
        on_created: Optional[Callable[..., Any]] = None,
        on_modified: Optional[Callable[..., Any]] = None,
        on_deleted: Optional[Callable[..., Any]] = None,
    ):
        self.scanner = scanner
        self._on_created_callback = on_created
        self._on_modified_callback = on_modified
        self._on_deleted_callback = on_deleted

        self._processing: Set[Path] = set()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle_file_event(
                event.src_path, self._on_created_callback, "created"
            )

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle_file_event(
                event.src_path, self._on_modified_callback, "modified"
            )

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            path = Path(event.src_path)
            if self._is_media_file(path):
                logger.info(f"Media file deleted: {path}")
                if self._on_deleted_callback:
                    self._on_deleted_callback(path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory and isinstance(event, FileMovedEvent):
            # Treat as deletion + creation
            old_path = Path(event.src_path)
            new_path = Path(event.dest_path)

            if self._is_media_file(old_path):
                logger.info(f"Media file moved from {old_path} to {new_path}")
                if self._on_deleted_callback:
                    self._on_deleted_callback(old_path)

            if self._is_media_file(new_path):
                self._handle_file_event(
                    event.dest_path, self._on_created_callback, "moved"
                )

    def _handle_file_event(
        self, file_path: str, callback: Optional[Callable[..., Any]], event_type: str
    ) -> None:
        path = Path(file_path)

        if not self._is_media_file(path):
            return

        if path in self._processing:
            return

        self._processing.add(path)
        try:
            logger.info(f"Media file {event_type}: {path}")

            # Process the file
            media = self.scanner._process_media_file(path)

            if media and callback:
                callback(media)

        except Exception as e:
            logger.error(f"Failed to process {event_type} file {path}: {e}")
        finally:
            self._processing.discard(path)

    def _is_media_file(self, path: Path) -> bool:
        """Check if file is a supported media file"""
        return path.suffix.lower() in Scanner.SUPPORTED_EXTENSIONS


class DirectoryWatcher:
    def __init__(self, scanner: Scanner):
        self.scanner = scanner
        self.observer = Observer()
        self.watched_paths: Set[Path] = set()
        self._running = False

    def watch_directory(
        self,
        directory: Path,
        recursive: bool = True,
        on_created: Optional[Callable[..., Any]] = None,
        on_modified: Optional[Callable[..., Any]] = None,
        on_deleted: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")

        if directory in self.watched_paths:
            logger.warning(f"Already watching directory: {directory}")
            return

        handler = MediaFileHandler(
            self.scanner,
            on_created=on_created,
            on_modified=on_modified,
            on_deleted=on_deleted,
        )

        self.observer.schedule(handler, str(directory), recursive=recursive)
        self.watched_paths.add(directory)

        logger.info(f"Started watching directory: {directory} (recursive={recursive})")

    def start(self) -> None:
        if not self._running:
            self.observer.start()
            self._running = True
            logger.info("Directory watcher started")

    def stop(self) -> None:
        if self._running:
            self.observer.stop()
            self.observer.join()
            self._running = False
            logger.info("Directory watcher stopped")

    def is_running(self) -> bool:
        return self._running

    def __del__(self) -> None:
        if hasattr(self, "observer") and self._running:
            self.stop()
