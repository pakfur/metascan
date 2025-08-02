from pathlib import Path
from typing import List, Set, Callable, Optional
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from metascan.core.media import Media
from metascan.extractors import MetadataExtractorManager

logger = logging.getLogger(__name__)


class MediaScanner:
    """Scans directories for media files and extracts metadata"""
    
    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
    
    def __init__(self, extractor_manager: Optional[MetadataExtractorManager] = None):
        self.extractor_manager = extractor_manager or MetadataExtractorManager()
        self._executor = ThreadPoolExecutor(max_workers=4)
    
    def scan_directory(self, directory: Path, 
                      recursive: bool = True,
                      progress_callback: Optional[Callable] = None) -> List[Media]:
        """Scan a directory for media files"""
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        # Find all media files
        media_files = self._find_media_files(directory, recursive)
        total_files = len(media_files)
        
        logger.info(f"Found {total_files} media files in {directory}")
        
        # Process files in parallel
        media_list = []
        futures = {}
        
        for i, file_path in enumerate(media_files):
            future = self._executor.submit(self._process_media_file, file_path)
            futures[future] = (i, file_path)
        
        for future in as_completed(futures):
            i, file_path = futures[future]
            try:
                media = future.result()
                if media:
                    media_list.append(media)
                
                if progress_callback:
                    progress_callback(i + 1, total_files, file_path)
                    
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")
        
        logger.info(f"Successfully processed {len(media_list)} media files")
        return media_list
    
    def scan_files(self, file_paths: List[Path],
                  progress_callback: Optional[Callable] = None) -> List[Media]:
        """Scan specific files"""
        media_list = []
        total_files = len(file_paths)
        
        for i, file_path in enumerate(file_paths):
            try:
                media = self._process_media_file(file_path)
                if media:
                    media_list.append(media)
                
                if progress_callback:
                    progress_callback(i + 1, total_files, file_path)
                    
            except Exception as e:
                logger.error(f"Failed to process {file_path}: {e}")
        
        return media_list
    
    def _find_media_files(self, directory: Path, recursive: bool) -> List[Path]:
        """Find all supported media files in directory"""
        media_files = []
        
        if recursive:
            for ext in self.SUPPORTED_EXTENSIONS:
                media_files.extend(directory.rglob(f"*{ext}"))
        else:
            for ext in self.SUPPORTED_EXTENSIONS:
                media_files.extend(directory.glob(f"*{ext}"))
        
        # Sort for consistent ordering
        media_files.sort()
        
        return media_files
    
    def _process_media_file(self, file_path: Path) -> Optional[Media]:
        """Process a single media file"""
        try:
            # Get file stats
            stat = file_path.stat()
            
            # Get image dimensions
            width, height, format_name = self._get_image_info(file_path)
            if not width or not height:
                return None
            
            # Create base media object
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
                media.generation_data = metadata.get("raw_metadata", {})
                
                # Set extracted parameters
                media.prompt = metadata.get("prompt")
                media.negative_prompt = metadata.get("negative_prompt")
                media.model = metadata.get("model")
                media.sampler = metadata.get("sampler")
                media.steps = metadata.get("steps")
                media.cfg_scale = metadata.get("cfg_scale")
                media.seed = metadata.get("seed")
            
            return media
            
        except Exception as e:
            logger.error(f"Failed to process media file {file_path}: {e}")
            return None
    
    def _get_image_info(self, file_path: Path) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Get image dimensions and format"""
        try:
            with Image.open(file_path) as img:
                return img.width, img.height, img.format
        except Exception as e:
            logger.error(f"Failed to get image info for {file_path}: {e}")
            return None, None, None
    
    def __del__(self):
        """Cleanup executor"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)