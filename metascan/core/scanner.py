from pathlib import Path
from typing import List, Optional, Callable
import logging
from PIL import Image
from datetime import datetime
from metascan.core.media import Media
from metascan.core.database_sqlite import DatabaseManager
from metascan.extractors import MetadataExtractorManager

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
                # Process the media file
                media = self._process_media_file(file_path)
                if media:
                    # Store in database
                    self.db_manager.save_media(media)
                    processed_count += 1
                    
                if progress_callback:
                    progress_callback(i + 1, total_files, file_path)
                    
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
            
            # Get image info
            width, height, format_name = self._get_image_info(file_path)
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
                media.prompt = metadata.get("prompt")
                media.negative_prompt = metadata.get("negative_prompt")
                media.model = metadata.get("model")
                media.sampler = metadata.get("sampler")
                media.steps = metadata.get("steps")
                media.cfg_scale = metadata.get("cfg_scale")
                media.seed = metadata.get("seed")
                media.loras = metadata.get("loras", [])
                media.base_model = metadata.get("base_model")
                
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