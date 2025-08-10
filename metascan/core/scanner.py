from pathlib import Path
from typing import List, Optional, Callable
import logging
from PIL import Image
from datetime import datetime
from metascan.core.media import Media
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