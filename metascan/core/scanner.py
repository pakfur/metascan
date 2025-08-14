from pathlib import Path
from typing import List, Optional, Callable, Tuple, Dict, Any
from queue import Queue
from threading import Thread
import logging
from PIL import Image
from datetime import datetime
import threading
import queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from metascan.core.media import Media, LoRA
from metascan.core.database_sqlite import DatabaseManager
from metascan.extractors import MetadataExtractorManager
from metascan.cache.thumbnail import ThumbnailCache

try:
    import ffmpeg

    HAS_FFMPEG_PYTHON = True
except ImportError:
    HAS_FFMPEG_PYTHON = False

logger = logging.getLogger(__name__)


class Scanner:
    """Main scanner that integrates MediaScanner with database operations"""

    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"}

    def __init__(
        self,
        db_manager: DatabaseManager,
        thumbnail_cache: Optional[ThumbnailCache] = None,
    ):
        self.db_manager = db_manager
        self.extractor_manager = MetadataExtractorManager()
        self.thumbnail_cache = thumbnail_cache

    def scan_directory(
        self,
        directory: str,
        recursive: bool = True,
        progress_callback: Optional[Callable] = None,
        full_scan: bool = False,
    ) -> int:
        """Scan a directory and store results in database
        
        Args:
            directory: Directory path to scan
            recursive: Whether to scan subdirectories
            progress_callback: Callback for progress updates
            full_scan: If True, process all files. If False, skip existing files.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")

        # Get existing file paths from database for optimization
        existing_paths = set() if full_scan else self.db_manager.get_existing_file_paths()
        
        # Find all media files
        media_files = self._find_media_files(dir_path, recursive)
        
        # Filter out existing files if not doing a full scan
        if not full_scan and existing_paths:
            original_count = len(media_files)
            media_files = [f for f in media_files if str(f) not in existing_paths]
            skipped_count = original_count - len(media_files)
            if skipped_count > 0:
                logger.info(f"Skipping {skipped_count} files that already exist in database")
        
        total_files = len(media_files)
        processed_count = 0

        logger.info(f"Processing {total_files} new/updated media files in {directory}")

        for i, file_path in enumerate(media_files):
            try:
                # Progress callback and cancellation check
                if progress_callback:
                    should_continue = progress_callback(i + 1, total_files, file_path)
                    if should_continue is False:
                        logger.info("Scanning cancelled by user")
                        break

                # Process the media file
                media = self._process_media_file(file_path)
                if media:
                    # Store in database
                    self.db_manager.save_media(media)

                    # Generate thumbnail if thumbnail cache is available
                    if self.thumbnail_cache:
                        try:
                            thumbnail_path = (
                                self.thumbnail_cache.get_or_create_thumbnail(file_path)
                            )
                            if thumbnail_path:
                                logger.debug(f"Generated thumbnail for {file_path}")
                            else:
                                logger.debug(
                                    f"Failed to generate thumbnail for {file_path}"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Thumbnail generation failed for {file_path}: {e}"
                            )

                    processed_count += 1

            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")

        logger.info(f"Successfully processed {processed_count} new/updated media files")
        return processed_count

    def _find_media_files(self, directory: Path, recursive: bool) -> List[Path]:
        """Find all supported media files in directory"""
        media_files: List[Path] = []

        if recursive:
            for ext in self.SUPPORTED_EXTENSIONS:
                media_files.extend(directory.rglob(f"*{ext}"))
                media_files.extend(directory.rglob(f"*{ext.upper()}"))
        else:
            for ext in self.SUPPORTED_EXTENSIONS:
                media_files.extend(directory.glob(f"*{ext}"))
                media_files.extend(directory.glob(f"*{ext.upper()}"))

        # Remove duplicates and sort
        media_files = list(set(media_files))
        media_files.sort()

        return media_files

    def _process_media_file(self, file_path: Path) -> Optional[Media]:
        """Process a single media file and extract metadata"""
        try:
            # Get file stats
            stat = file_path.stat()

            # Get media info (image or video)
            width, height, format_name = self._get_media_info(file_path)
            if not width or not height:
                return None

            # Create media object
            media = Media(
                file_path=file_path,
                file_size=stat.st_size,
                width=width,
                height=height,
                format=format_name or "UNKNOWN",
                created_at=datetime.fromtimestamp(stat.st_ctime),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
            )

            # Extract metadata
            metadata = self.extractor_manager.extract_metadata(file_path)
            if metadata:
                # Update media object with extracted metadata
                media.metadata_source = metadata.get("source")
                media.prompt = metadata.get("prompt")
                media.negative_prompt = metadata.get("negative_prompt")
                media.model = metadata.get("model")
                media.sampler = metadata.get("sampler")
                media.scheduler = metadata.get("scheduler")
                media.steps = metadata.get("steps")
                media.cfg_scale = metadata.get("cfg_scale")
                media.seed = metadata.get("seed")

                # Video-specific metadata
                media.frame_rate = metadata.get("frame_rate")
                media.duration = metadata.get("duration")
                media.video_length = metadata.get("length")

                # Store raw metadata for advanced access
                media.generation_data = metadata.get("raw_metadata", {})

                # Process LoRAs
                if "loras" in metadata:
                    loras_data = metadata["loras"]
                    for lora_data in loras_data:
                        if isinstance(lora_data, dict) and "lora_name" in lora_data:
                            lora = LoRA(
                                lora_name=lora_data["lora_name"],
                                lora_weight=lora_data.get("lora_weight", 1.0),
                            )
                            media.loras.append(lora)

            return media

        except Exception as e:
            logger.error(f"Failed to process media file {file_path}: {e}")
            return None

    def _get_media_info(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Get media dimensions and format"""
        try:
            if file_path.suffix.lower() == ".mp4":
                return self._get_video_info(file_path)
            else:
                with Image.open(file_path) as img:
                    return img.width, img.height, img.format
        except Exception as e:
            logger.error(f"Failed to get media info for {file_path}: {e}")
            return None, None, None

    def _get_video_info(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Get video dimensions and format"""
        try:
            # Try python-ffmpeg first
            if HAS_FFMPEG_PYTHON:
                result = self._get_video_info_python(file_path)
                if result and result[0] and result[1]:  # Valid width and height
                    return result

            # Fallback to subprocess ffprobe
            return self._get_video_info_subprocess(file_path)

        except Exception as e:
            logger.error(f"Failed to get video info for {file_path}: {e}")
            return self._get_video_info_fallback(file_path)

    def _get_video_info_python(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Get video info using python-ffmpeg bindings"""
        try:
            probe = ffmpeg.probe(str(file_path))

            # Find video stream
            video_stream = None
            for stream in probe["streams"]:
                if stream["codec_type"] == "video":
                    video_stream = stream
                    break

            if video_stream:
                width = video_stream.get("width")
                height = video_stream.get("height")
                if width and height:
                    return width, height, "MP4"

            return None, None, None

        except ffmpeg.Error as e:
            logger.debug(f"python-ffmpeg probe failed for {file_path}: {e}")
            return None, None, None
        except Exception as e:
            logger.debug(f"python-ffmpeg unexpected error for {file_path}: {e}")
            return None, None, None

    def _get_video_info_subprocess(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Get video dimensions using subprocess ffprobe"""
        try:
            import subprocess
            import json

            # Use ffprobe to get video dimensions
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                str(file_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                # Fallback: try to extract basic info without ffprobe
                return self._get_video_info_fallback(file_path)

            data = json.loads(result.stdout)
            if "streams" in data and len(data["streams"]) > 0:
                stream = data["streams"][0]
                width = stream.get("width")
                height = stream.get("height")
                return width, height, "MP4"

            return None, None, None

        except Exception as e:
            logger.error(f"Subprocess video info failed for {file_path}: {e}")
            return self._get_video_info_fallback(file_path)

    def _get_video_info_fallback(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Fallback method to get basic video info without ffprobe"""
        try:
            # Try using exiftool if available
            import subprocess

            result = subprocess.run(
                ["exiftool", "-ImageWidth", "-ImageHeight", "-json", str(file_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                import json

                data = json.loads(result.stdout)
                if data and len(data) > 0:
                    item = data[0]
                    width = item.get("ImageWidth")
                    height = item.get("ImageHeight")
                    if width and height:
                        return width, height, "MP4"

            # If all else fails, return reasonable defaults
            logger.warning(
                f"Could not determine video dimensions for {file_path}, using defaults"
            )
            return 1920, 1080, "MP4"

        except Exception as e:
            logger.error(f"Fallback video info extraction failed for {file_path}: {e}")
            return 1920, 1080, "MP4"


class ThreadedScanner:
    """Multi-threaded scanner using producer-consumer pattern for improved throughput"""

    def __init__(
        self,
        db_manager: DatabaseManager,
        num_workers: int = 4,
        batch_size: int = 10,
        thumbnail_cache: Optional[ThumbnailCache] = None,
    ):
        self.db_manager = db_manager
        self.extractor_manager = MetadataExtractorManager()
        self.num_workers = num_workers
        self.batch_size = batch_size
        self.thumbnail_cache = thumbnail_cache

        # Thread-safe queues - increased sizes to prevent deadlocks
        self.file_queue: Queue[Optional[Path]] = queue.Queue(
            maxsize=500
        )  # Increased to prevent producer blocking
        self.result_queue: Queue[Tuple[Path, Optional[Media]]] = queue.Queue(
            maxsize=500
        )  # Increased to prevent worker blocking

        # Threading control
        self.workers: List[Thread] = []
        self.producer_thread: Optional[Thread] = None
        self.writer_thread: Optional[Thread] = None
        self.stop_event = threading.Event()
        self.stats_lock = threading.Lock()

        # Progress tracking
        self.total_files = 0
        self.files_processed = 0
        self.files_saved = 0
        self.progress_callback: Optional[Callable] = None

    def scan_directory(
        self,
        directory: str,
        recursive: bool = True,
        progress_callback: Optional[Callable] = None,
        full_scan: bool = False,
    ) -> int:
        """Scan a directory using multi-threaded approach
        
        Args:
            directory: Directory path to scan
            recursive: Whether to scan subdirectories
            progress_callback: Callback for progress updates
            full_scan: If True, process all files. If False, skip existing files.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")

        # Reset state for this scan
        self._reset_scanner_state()
        self.progress_callback = progress_callback

        try:
            # Get existing file paths from database for optimization
            existing_paths = set() if full_scan else self.db_manager.get_existing_file_paths()
            
            # Find all media files first to get total count
            media_files = self._find_media_files(dir_path, recursive)
            
            # Filter out existing files if not doing a full scan
            if not full_scan and existing_paths:
                original_count = len(media_files)
                media_files = [f for f in media_files if str(f) not in existing_paths]
                skipped_count = original_count - len(media_files)
                if skipped_count > 0:
                    logger.info(f"Skipping {skipped_count} files that already exist in database")
            
            self.total_files = len(media_files)
            self.files_processed = 0
            self.files_saved = 0

            if self.total_files == 0:
                logger.info(f"No new media files found in {directory}")
                return 0

            logger.info(f"Processing {self.total_files} new/updated media files in {directory}")

            # Start threads
            self._start_threads(media_files)

            # Wait for completion
            self._wait_for_completion()

            logger.info(f"Successfully processed {self.files_saved} media files")
            return self.files_saved

        except Exception as e:
            logger.error(f"Error during threaded scanning: {e}")
            self.stop_scanning()
            raise
        finally:
            self._cleanup_threads()

    def stop_scanning(self) -> None:
        """Signal all threads to stop"""
        logger.info("Stopping threaded scanner...")
        self.stop_event.set()

    def _find_media_files(self, directory: Path, recursive: bool) -> List[Path]:
        """Find all supported media files in directory"""
        media_files: List[Path] = []
        supported_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"}

        if recursive:
            for ext in supported_extensions:
                media_files.extend(directory.rglob(f"*{ext}"))
                media_files.extend(directory.rglob(f"*{ext.upper()}"))
        else:
            for ext in supported_extensions:
                media_files.extend(directory.glob(f"*{ext}"))
                media_files.extend(directory.glob(f"*{ext.upper()}"))

        # Remove duplicates and sort
        media_files = list(set(media_files))
        media_files.sort()
        return media_files

    def _start_threads(self, media_files: List[Path]) -> None:
        """Start producer, worker, and writer threads"""
        # Start producer thread
        self.producer_thread = threading.Thread(
            target=self._producer_worker, args=(media_files,), name="FileProducer"
        )
        self.producer_thread.start()

        # Start worker threads
        self.workers = []
        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self._file_worker, name=f"FileWorker-{i+1}"
            )
            worker.start()
            self.workers.append(worker)

        # Start database writer thread
        self.writer_thread = threading.Thread(
            target=self._database_writer, name="DatabaseWriter"
        )
        self.writer_thread.start()

    def _producer_worker(self, media_files: List[Path]) -> None:
        """Producer thread: adds files to the work queue"""
        files_added = 0
        try:
            for file_path in media_files:
                if self.stop_event.is_set():
                    break

                # Add file to queue with retry logic for queue.Full exceptions
                retry_count = 0
                while not self.stop_event.is_set() and retry_count < 10:
                    try:
                        self.file_queue.put(file_path, timeout=2.0)
                        files_added += 1
                        break  # Successfully added, move to next file
                    except queue.Full:
                        # Queue is full, wait a bit and retry
                        retry_count += 1
                        if retry_count >= 10:
                            logger.error(
                                f"Failed to add file to queue after 10 retries: {file_path}"
                            )
                            break
                        logger.debug(f"File queue full, retry {retry_count}/10...")
                        time.sleep(0.5)  # Wait longer between retries

            logger.debug(
                f"Producer added {files_added}/{len(media_files)} files to queue"
            )

            # Signal end of files by adding sentinel values
            sentinels_added = 0
            for i in range(self.num_workers):
                retry_count = 0
                while not self.stop_event.is_set() and retry_count < 20:
                    try:
                        self.file_queue.put(None, timeout=2.0)
                        sentinels_added += 1
                        break  # Successfully added sentinel
                    except queue.Full:
                        # Queue is full, wait a bit and retry
                        retry_count += 1
                        if retry_count >= 20:
                            logger.error(
                                f"Failed to add sentinel {i+1}/{self.num_workers} after 20 retries"
                            )
                            break
                        logger.debug(
                            f"File queue full while adding sentinel {i+1}, retry {retry_count}/20..."
                        )
                        time.sleep(1.0)  # Wait longer for queue to drain

            logger.debug(
                f"Producer added {sentinels_added}/{self.num_workers} sentinels"
            )

        except Exception as e:
            logger.error(f"Producer thread error: {e}")
            import traceback

            logger.error(f"Producer thread traceback: {traceback.format_exc()}")
            self.stop_event.set()
        finally:
            logger.debug("Producer thread exiting")

    def _file_worker(self) -> None:
        """Worker thread: processes files and adds results to result queue"""
        # Create scanner instance per thread (thread-local extractors)
        scanner = Scanner(self.db_manager, thumbnail_cache=self.thumbnail_cache)
        worker_id = threading.current_thread().name
        files_processed_by_worker = 0

        try:
            while not self.stop_event.is_set():
                file_path = None
                try:
                    # Get file from queue with timeout
                    file_path = self.file_queue.get(timeout=2.0)

                    # Check for sentinel value (end of work)
                    if file_path is None:
                        logger.debug(f"{worker_id} received sentinel, exiting")
                        self.file_queue.task_done()
                        break

                    # Process the file - catch all exceptions to ensure we always send a result
                    media = None
                    try:
                        media = scanner._process_media_file(file_path)
                        if media:
                            files_processed_by_worker += 1
                    except Exception as e:
                        # Log the error but continue processing
                        logger.debug(f"{worker_id} failed to process {file_path}: {e}")
                        media = None

                    # Update progress
                    with self.stats_lock:
                        self.files_processed += 1
                        if self.progress_callback:
                            try:
                                should_continue = self.progress_callback(
                                    self.files_processed, self.total_files, file_path
                                )
                                if should_continue is False:
                                    logger.info(
                                        f"{worker_id} cancelled by progress callback"
                                    )
                                    self.stop_event.set()
                                    # Still add result to queue even if cancelled
                                    self._put_result_with_timeout((file_path, media))
                                    self.file_queue.task_done()
                                    break
                            except Exception as e:
                                logger.error(
                                    f"{worker_id} progress callback error: {e}"
                                )

                    # Always add result to result queue (even if None)
                    # This ensures the database writer knows about all files
                    if not self._put_result_with_timeout((file_path, media)):
                        logger.error(
                            f"{worker_id} failed to add result for {file_path}"
                        )

                    # Mark task as done
                    self.file_queue.task_done()

                except queue.Empty:
                    # Timeout is normal, just check stop event and continue
                    continue
                except Exception as e:
                    # Unexpected error - log it but try to continue
                    logger.error(f"{worker_id} unexpected error: {e}")
                    # Try to mark task done and add failed result if we have a file_path
                    if file_path is not None:
                        try:
                            self._put_result_with_timeout((file_path, None))
                            self.file_queue.task_done()
                        except Exception as cleanup_error:
                            logger.error(f"{worker_id} cleanup error: {cleanup_error}")

        except Exception as e:
            logger.error(f"{worker_id} fatal error: {e}")
            import traceback

            logger.error(f"{worker_id} traceback: {traceback.format_exc()}")
            self.stop_event.set()
        finally:
            logger.debug(
                f"{worker_id} exiting, processed {files_processed_by_worker} files successfully"
            )

    def _database_writer(self) -> None:
        """Database writer thread: batches results and writes to database"""
        batch = []
        last_write_time = time.time()
        batch_timeout = 2.0  # Write batch every 2 seconds even if not full
        results_processed = 0
        files_written = 0
        files_failed = 0

        logger.debug(
            f"Database writer starting, expecting up to {self.total_files} files"
        )

        try:
            while not self.stop_event.is_set():
                try:
                    # Get result from queue
                    result_tuple = self.result_queue.get(timeout=1.0)
                    file_path, media = result_tuple
                    results_processed += 1

                    # Add valid media to batch
                    if media is not None:
                        batch.append(media)
                        files_written += 1
                    else:
                        files_failed += 1
                        logger.debug(f"Skipped failed file: {file_path}")

                    current_time = time.time()

                    # Write batch if it's full or timeout reached
                    if len(batch) >= self.batch_size or (
                        batch and current_time - last_write_time >= batch_timeout
                    ):
                        if batch:
                            saved_count = self.db_manager.save_media_batch(batch)

                            # Generate thumbnails for successfully saved media files
                            if self.thumbnail_cache and saved_count > 0:
                                self._generate_thumbnails_for_batch(batch[:saved_count])

                            with self.stats_lock:
                                self.files_saved += saved_count

                            logger.debug(f"Saved batch of {saved_count} media files")
                            batch.clear()
                            last_write_time = current_time

                    # Mark result as processed
                    self.result_queue.task_done()

                    # Don't use this as exit condition - it's unreliable
                    # The exit condition should be based on workers finishing, not file count
                    if results_processed % 100 == 0:
                        logger.debug(
                            f"Database writer processed {results_processed}/{self.total_files} results"
                        )

                except queue.Empty:
                    # Check if we have a partial batch to write on timeout
                    current_time = time.time()
                    if batch and current_time - last_write_time >= batch_timeout:
                        saved_count = self.db_manager.save_media_batch(batch)

                        # Generate thumbnails for successfully saved media files
                        if self.thumbnail_cache and saved_count > 0:
                            self._generate_thumbnails_for_batch(batch[:saved_count])

                        with self.stats_lock:
                            self.files_saved += saved_count

                        logger.debug(
                            f"Saved timeout batch of {saved_count} media files"
                        )
                        batch.clear()
                        last_write_time = current_time

                    # Check if all workers are done and no more results expected
                    # Add a grace period to ensure no race conditions
                    if all(not worker.is_alive() for worker in self.workers):
                        # Workers are done, check if queue has been empty for a while
                        if self.result_queue.empty():
                            # Wait a bit to ensure no race condition
                            time.sleep(2.0)
                            if self.result_queue.empty():
                                logger.debug(
                                    f"All workers finished and queue empty, database writer exiting (processed {results_processed} files)"
                                )
                                break

                except Exception as e:
                    logger.error(f"Database writer error: {e}")
                    # Mark as processed even on error to avoid hanging
                    try:
                        self.result_queue.task_done()
                    except ValueError:
                        pass

            # Write any remaining items in batch
            if batch:
                saved_count = self.db_manager.save_media_batch(batch)

                # Generate thumbnails for successfully saved media files
                if self.thumbnail_cache and saved_count > 0:
                    self._generate_thumbnails_for_batch(batch[:saved_count])

                with self.stats_lock:
                    self.files_saved += saved_count
                logger.debug(f"Saved final batch of {saved_count} media files")

        except Exception as e:
            logger.error(f"Database writer fatal error: {e}")
            import traceback

            logger.error(f"Database writer traceback: {traceback.format_exc()}")
            self.stop_event.set()
        finally:
            logger.info(
                f"Database writer exiting: processed={results_processed}, written={files_written}, failed={files_failed}, saved={self.files_saved}"
            )

    def _wait_for_completion(self) -> None:
        """Wait for all threads to complete"""
        timeout = 300.0  # 5 minute timeout per thread for large directories

        # Wait for producer to finish
        if self.producer_thread and self.producer_thread.is_alive():
            self.producer_thread.join(timeout)
            if self.producer_thread.is_alive():
                logger.warning("Producer thread did not finish within timeout")

        # Wait for all workers to finish
        for i, worker in enumerate(self.workers):
            if worker.is_alive():
                worker.join(timeout)
                if worker.is_alive():
                    logger.warning(f"Worker thread {i+1} did not finish within timeout")

        # Wait for writer to finish
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout)
            if self.writer_thread.is_alive():
                logger.warning("Database writer thread did not finish within timeout")

    def _cleanup_threads(self) -> None:
        """Clean up thread resources"""
        self.workers.clear()
        self.producer_thread = None
        self.writer_thread = None

        # Clear any remaining items in queues
        while not self.file_queue.empty():
            try:
                self.file_queue.get_nowait()
            except queue.Empty:
                break

        while not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
            except queue.Empty:
                break

    def _generate_thumbnails_for_batch(self, media_batch: List[Media]) -> None:
        """Generate thumbnails for a batch of successfully saved media files"""
        if not self.thumbnail_cache or not media_batch:
            return

        logger.debug(
            f"Generating thumbnails for batch of {len(media_batch)} media files"
        )

        # Extract file paths from media objects
        file_paths = [media.file_path for media in media_batch]

        # Generate thumbnails synchronously to avoid overloading the system
        thumbnail_count = 0
        failed_count = 0

        for file_path in file_paths:
            try:
                thumbnail_path = self.thumbnail_cache.get_or_create_thumbnail(file_path)
                if thumbnail_path:
                    thumbnail_count += 1
                    logger.debug(f"Generated thumbnail for {file_path}")
                else:
                    failed_count += 1
                    logger.debug(f"Failed to generate thumbnail for {file_path}")

            except Exception as e:
                failed_count += 1
                logger.debug(f"Thumbnail generation error for {file_path}: {e}")

        if thumbnail_count > 0 or failed_count > 0:
            logger.debug(
                f"Batch thumbnail generation: {thumbnail_count} created, {failed_count} failed"
            )

    def _put_result_with_timeout(self, result: Any) -> bool:
        """Put result in result queue with timeout and retry logic to prevent deadlocks

        Returns:
            bool: True if successfully added, False if failed
        """
        retry_count = 0
        max_retries = 30  # Limit retries to prevent infinite loops
        while not self.stop_event.is_set() and retry_count < max_retries:
            try:
                self.result_queue.put(result, timeout=2.0)
                return True  # Successfully added
            except queue.Full:
                # Result queue is full, wait a bit and retry
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        f"Failed to add result to queue after {max_retries} retries: {result[0] if result else 'None'}"
                    )
                    return False
                if retry_count % 10 == 0:  # Only log every 10 retries to reduce spam
                    logger.debug(
                        f"Result queue full, retry {retry_count}/{max_retries}..."
                    )
                time.sleep(0.5)  # Wait for queue to drain
        return False  # Failed to add

    def _reset_scanner_state(self) -> None:
        """Reset scanner state for reuse across multiple scans"""
        # Clear stop event
        self.stop_event.clear()

        # Reset progress tracking
        self.total_files = 0
        self.files_processed = 0
        self.files_saved = 0
        self.progress_callback = None

        # Clear thread references (they should be None after cleanup, but ensure it)
        self.workers = []
        self.producer_thread = None
        self.writer_thread = None

        # Create fresh queues to avoid any leftover state
        self.file_queue = queue.Queue(maxsize=100)
        self.result_queue = queue.Queue(maxsize=50)

        logger.debug("Scanner state reset for new scan")
