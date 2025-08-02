from pathlib import Path
from typing import Callable, Set, Optional
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from metascan.core.scanner import MediaScanner

logger = logging.getLogger(__name__)


class MediaFileHandler(FileSystemEventHandler):
    """Handles file system events for media files"""
    
    def __init__(self, 
                 scanner: MediaScanner,
                 on_created: Optional[Callable] = None,
                 on_modified: Optional[Callable] = None,
                 on_deleted: Optional[Callable] = None):
        self.scanner = scanner
        self.on_created = on_created
        self.on_modified = on_modified
        self.on_deleted = on_deleted
        
        # Track processing to avoid duplicates
        self._processing = set()
    
    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._handle_file_event(event.src_path, self.on_created, "created")
    
    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._handle_file_event(event.src_path, self.on_modified, "modified")
    
    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            path = Path(event.src_path)
            if self._is_media_file(path):
                logger.info(f"Media file deleted: {path}")
                if self.on_deleted:
                    self.on_deleted(path)
    
    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            # Treat as deletion + creation
            old_path = Path(event.src_path)
            new_path = Path(event.dest_path)
            
            if self._is_media_file(old_path):
                logger.info(f"Media file moved from {old_path} to {new_path}")
                if self.on_deleted:
                    self.on_deleted(old_path)
            
            if self._is_media_file(new_path):
                self._handle_file_event(event.dest_path, self.on_created, "moved")
    
    def _handle_file_event(self, file_path: str, callback: Optional[Callable], event_type: str):
        """Handle a file event with deduplication"""
        path = Path(file_path)
        
        if not self._is_media_file(path):
            return
        
        # Avoid processing the same file multiple times
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
        return path.suffix.lower() in MediaScanner.SUPPORTED_EXTENSIONS


class DirectoryWatcher:
    """Watches directories for media file changes"""
    
    def __init__(self, scanner: MediaScanner):
        self.scanner = scanner
        self.observer = Observer()
        self.watched_paths: Set[Path] = set()
        self._running = False
    
    def watch_directory(self, 
                       directory: Path,
                       recursive: bool = True,
                       on_created: Optional[Callable] = None,
                       on_modified: Optional[Callable] = None,
                       on_deleted: Optional[Callable] = None):
        """Start watching a directory"""
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        if directory in self.watched_paths:
            logger.warning(f"Already watching directory: {directory}")
            return
        
        handler = MediaFileHandler(
            self.scanner,
            on_created=on_created,
            on_modified=on_modified,
            on_deleted=on_deleted
        )
        
        self.observer.schedule(handler, str(directory), recursive=recursive)
        self.watched_paths.add(directory)
        
        logger.info(f"Started watching directory: {directory} (recursive={recursive})")
    
    def start(self):
        """Start the file system observer"""
        if not self._running:
            self.observer.start()
            self._running = True
            logger.info("Directory watcher started")
    
    def stop(self):
        """Stop the file system observer"""
        if self._running:
            self.observer.stop()
            self.observer.join()
            self._running = False
            logger.info("Directory watcher stopped")
    
    def is_running(self) -> bool:
        """Check if watcher is running"""
        return self._running
    
    def __del__(self):
        """Ensure observer is stopped on deletion"""
        if hasattr(self, 'observer') and self._running:
            self.stop()