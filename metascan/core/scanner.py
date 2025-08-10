from pathlib import Path
from typing import List, Optional, Callable
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

try:
    import ffmpeg
    HAS_FFMPEG_PYTHON = True
except ImportError:
    HAS_FFMPEG_PYTHON = False

logger = logging.getLogger(__name__)


class Scanner:
    """Main scanner that integrates MediaScanner with database operations"""
    
    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4'}
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.extractor_manager = MetadataExtractorManager()
        
    def scan_directory(self, directory: str, recursive: bool = True, 
                      progress_callback: Optional[Callable] = None) -> int:
        """Scan a directory and store results in database"""
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")
            
        # Find all media files
        media_files = self._find_media_files(dir_path, recursive)
        total_files = len(media_files)
        processed_count = 0
        
        logger.info(f"Found {total_files} media files in {directory}")
        
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
                    processed_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")
                
        logger.info(f"Successfully processed {processed_count} new/updated media files")
        return processed_count
        
    def _find_media_files(self, directory: Path, recursive: bool) -> List[Path]:
        """Find all supported media files in directory"""
        media_files = []
        
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
                format=format_name,
                created_at=datetime.fromtimestamp(stat.st_ctime),
                modified_at=datetime.fromtimestamp(stat.st_mtime)
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
                                lora_weight=lora_data.get("lora_weight", 1.0)
                            )
                            media.loras.append(lora)
                
            return media
            
        except Exception as e:
            logger.error(f"Failed to process media file {file_path}: {e}")
            return None
            
    def _get_media_info(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Get media dimensions and format"""
        try:
            if file_path.suffix.lower() == '.mp4':
                return self._get_video_info(file_path)
            else:
                with Image.open(file_path) as img:
                    return img.width, img.height, img.format
        except Exception as e:
            logger.error(f"Failed to get media info for {file_path}: {e}")
            return None, None, None
    
    def _get_video_info(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
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
    
    def _get_video_info_python(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Get video info using python-ffmpeg bindings"""
        try:
            probe = ffmpeg.probe(str(file_path))
            
            # Find video stream
            video_stream = None
            for stream in probe['streams']:
                if stream['codec_type'] == 'video':
                    video_stream = stream
                    break
            
            if video_stream:
                width = video_stream.get('width')
                height = video_stream.get('height')
                if width and height:
                    return width, height, 'MP4'
            
            return None, None, None
            
        except ffmpeg.Error as e:
            logger.debug(f"python-ffmpeg probe failed for {file_path}: {e}")
            return None, None, None
        except Exception as e:
            logger.debug(f"python-ffmpeg unexpected error for {file_path}: {e}")
            return None, None, None
    
    def _get_video_info_subprocess(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Get video dimensions using subprocess ffprobe"""
        try:
            import subprocess
            import json
            
            # Use ffprobe to get video dimensions
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_streams', '-select_streams', 'v:0', str(file_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                # Fallback: try to extract basic info without ffprobe 
                return self._get_video_info_fallback(file_path)
            
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                stream = data['streams'][0]
                width = stream.get('width')
                height = stream.get('height')
                return width, height, 'MP4'
            
            return None, None, None
            
        except Exception as e:
            logger.error(f"Subprocess video info failed for {file_path}: {e}")
            return self._get_video_info_fallback(file_path)
    
    def _get_video_info_fallback(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Fallback method to get basic video info without ffprobe"""
        try:
            # Try using exiftool if available
            import subprocess
            result = subprocess.run(
                ['exiftool', '-ImageWidth', '-ImageHeight', '-json', str(file_path)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                if data and len(data) > 0:
                    item = data[0]
                    width = item.get('ImageWidth')
                    height = item.get('ImageHeight')
                    if width and height:
                        return width, height, 'MP4'
            
            # If all else fails, return reasonable defaults
            logger.warning(f"Could not determine video dimensions for {file_path}, using defaults")
            return 1920, 1080, 'MP4'
            
        except Exception as e:
            logger.error(f"Fallback video info extraction failed for {file_path}: {e}")
            return 1920, 1080, 'MP4'


class ThreadedScanner:
    """Multi-threaded scanner using producer-consumer pattern for improved throughput"""
    
    def __init__(self, db_manager: DatabaseManager, num_workers: int = 4, batch_size: int = 10):
        self.db_manager = db_manager
        self.extractor_manager = MetadataExtractorManager()
        self.num_workers = num_workers
        self.batch_size = batch_size
        
        # Thread-safe queues
        self.file_queue = queue.Queue(maxsize=100)  # Bounded to prevent memory issues
        self.result_queue = queue.Queue(maxsize=50)
        
        # Threading control
        self.workers = []
        self.producer_thread = None
        self.writer_thread = None
        self.stop_event = threading.Event()
        self.stats_lock = threading.Lock()
        
        # Progress tracking
        self.total_files = 0
        self.files_processed = 0
        self.files_saved = 0
        self.progress_callback = None
        
    def scan_directory(self, directory: str, recursive: bool = True, 
                      progress_callback: Optional[Callable] = None) -> int:
        """Scan a directory using multi-threaded approach"""
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        self.progress_callback = progress_callback
        self.stop_event.clear()
        
        try:
            # Find all media files first to get total count
            media_files = self._find_media_files(dir_path, recursive)
            self.total_files = len(media_files)
            self.files_processed = 0
            self.files_saved = 0
            
            if self.total_files == 0:
                logger.info(f"No media files found in {directory}")
                return 0
            
            logger.info(f"Found {self.total_files} media files in {directory}")
            
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
    
    def stop_scanning(self):
        """Signal all threads to stop"""
        logger.info("Stopping threaded scanner...")
        self.stop_event.set()
    
    def _find_media_files(self, directory: Path, recursive: bool) -> List[Path]:
        """Find all supported media files in directory"""
        media_files = []
        supported_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4'}
        
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
    
    def _start_threads(self, media_files: List[Path]):
        """Start producer, worker, and writer threads"""
        # Start producer thread
        self.producer_thread = threading.Thread(
            target=self._producer_worker, 
            args=(media_files,),
            name="FileProducer"
        )
        self.producer_thread.start()
        
        # Start worker threads
        self.workers = []
        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self._file_worker,
                name=f"FileWorker-{i+1}"
            )
            worker.start()
            self.workers.append(worker)
        
        # Start database writer thread
        self.writer_thread = threading.Thread(
            target=self._database_writer,
            name="DatabaseWriter"
        )
        self.writer_thread.start()
    
    def _producer_worker(self, media_files: List[Path]):
        """Producer thread: adds files to the work queue"""
        try:
            for file_path in media_files:
                if self.stop_event.is_set():
                    break
                
                # Add file to queue (will block if queue is full)
                self.file_queue.put(file_path, timeout=1.0)
            
            # Signal end of files by adding sentinel values
            for _ in range(self.num_workers):
                self.file_queue.put(None)
                
        except Exception as e:
            logger.error(f"Producer thread error: {e}")
            self.stop_event.set()
    
    def _file_worker(self):
        """Worker thread: processes files and adds results to result queue"""
        # Create scanner instance per thread (thread-local extractors)
        scanner = Scanner(self.db_manager)
        
        try:
            while not self.stop_event.is_set():
                file_path = None
                try:
                    # Get file from queue
                    file_path = self.file_queue.get(timeout=1.0)
                    
                    # Check for sentinel value (end of work)
                    if file_path is None:
                        self.file_queue.task_done()
                        break
                    
                    # Process the file
                    media = scanner._process_media_file(file_path)
                    
                    # Update progress
                    with self.stats_lock:
                        self.files_processed += 1
                        if self.progress_callback:
                            try:
                                should_continue = self.progress_callback(
                                    self.files_processed, 
                                    self.total_files, 
                                    file_path
                                )
                                if should_continue is False:
                                    self.stop_event.set()
                                    # Still add result to queue even if cancelled
                                    self.result_queue.put((file_path, media))
                                    self.file_queue.task_done()
                                    break
                            except Exception as e:
                                logger.error(f"Progress callback error: {e}")
                    
                    # Always add result to result queue (even if None)
                    self.result_queue.put((file_path, media))
                    
                    # Mark task as done
                    self.file_queue.task_done()
                    
                except queue.Empty:
                    continue  # Timeout, check stop event and try again
                except Exception as e:
                    logger.error(f"Worker thread error processing file {file_path}: {e}")
                    # Always add a result (even if failed) and mark task done
                    if file_path is not None:
                        self.result_queue.put((file_path, None))
                        try:
                            self.file_queue.task_done()
                        except ValueError:
                            pass  # Queue might already be empty
                    
        except Exception as e:
            logger.error(f"Worker thread fatal error: {e}")
            self.stop_event.set()
    
    def _database_writer(self):
        """Database writer thread: batches results and writes to database"""
        batch = []
        last_write_time = time.time()
        batch_timeout = 2.0  # Write batch every 2 seconds even if not full
        results_processed = 0
        
        try:
            while not self.stop_event.is_set():
                try:
                    # Get result from queue
                    file_path, media = self.result_queue.get(timeout=1.0)
                    results_processed += 1
                    
                    # Add valid media to batch
                    if media is not None:
                        batch.append(media)
                    else:
                        logger.debug(f"Skipped failed file: {file_path}")
                    
                    current_time = time.time()
                    
                    # Write batch if it's full or timeout reached
                    if (len(batch) >= self.batch_size or 
                        (batch and current_time - last_write_time >= batch_timeout)):
                        
                        if batch:
                            saved_count = self.db_manager.save_media_batch(batch)
                            
                            with self.stats_lock:
                                self.files_saved += saved_count
                            
                            logger.debug(f"Saved batch of {saved_count} media files")
                            batch.clear()
                            last_write_time = current_time
                    
                    # Mark result as processed
                    self.result_queue.task_done()
                    
                    # Check if we've processed all expected results
                    if results_processed >= self.total_files:
                        logger.debug(f"Database writer processed all {self.total_files} results")
                        break
                    
                except queue.Empty:
                    # Check if we have a partial batch to write on timeout
                    current_time = time.time()
                    if batch and current_time - last_write_time >= batch_timeout:
                        saved_count = self.db_manager.save_media_batch(batch)
                        
                        with self.stats_lock:
                            self.files_saved += saved_count
                        
                        logger.debug(f"Saved timeout batch of {saved_count} media files")
                        batch.clear()
                        last_write_time = current_time
                    
                    # Check if all workers are done and no more results expected
                    if (all(not worker.is_alive() for worker in self.workers) and 
                        self.result_queue.empty() and 
                        (self.producer_thread is None or not self.producer_thread.is_alive())):
                        logger.debug("All workers and producer finished, database writer exiting")
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
                with self.stats_lock:
                    self.files_saved += saved_count
                logger.debug(f"Saved final batch of {saved_count} media files")
                
        except Exception as e:
            logger.error(f"Database writer fatal error: {e}")
            self.stop_event.set()
    
    def _wait_for_completion(self):
        """Wait for all threads to complete"""
        # Wait for producer to finish
        if self.producer_thread:
            self.producer_thread.join()
        
        # Wait for all workers to finish
        for worker in self.workers:
            worker.join()
        
        # Wait for writer to finish
        if self.writer_thread:
            self.writer_thread.join()
    
    def _cleanup_threads(self):
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