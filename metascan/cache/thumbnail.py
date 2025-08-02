from pathlib import Path
from PIL import Image
from typing import Optional, Tuple
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Manages thumbnail generation and caching"""
    
    DEFAULT_SIZE = (256, 256)
    SUPPORTED_FORMATS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
    
    def __init__(self, cache_dir: Path, thumbnail_size: Tuple[int, int] = DEFAULT_SIZE):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_size = thumbnail_size
        self._executor = ThreadPoolExecutor(max_workers=4)
    
    def get_thumbnail_path(self, image_path: Path) -> Path:
        """Get the cache path for a thumbnail"""
        # Create a unique filename based on original path and modification time
        stat = image_path.stat()
        unique_string = f"{image_path}_{stat.st_mtime}_{stat.st_size}"
        hash_name = hashlib.md5(unique_string.encode()).hexdigest()
        
        return self.cache_dir / f"{hash_name}.jpg"
    
    def get_or_create_thumbnail(self, image_path: Path) -> Optional[Path]:
        """Get thumbnail from cache or create if it doesn't exist"""
        if not self._is_supported_format(image_path):
            logger.warning(f"Unsupported image format: {image_path}")
            return None
        
        thumbnail_path = self.get_thumbnail_path(image_path)
        
        # Check if thumbnail exists and is newer than source
        if thumbnail_path.exists():
            if thumbnail_path.stat().st_mtime >= image_path.stat().st_mtime:
                return thumbnail_path
        
        # Create thumbnail
        return self._create_thumbnail(image_path, thumbnail_path)
    
    def _create_thumbnail(self, image_path: Path, thumbnail_path: Path) -> Optional[Path]:
        """Create a thumbnail for the given image"""
        try:
            with Image.open(image_path) as img:
                # Convert RGBA to RGB if necessary
                if img.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'RGBA':
                        background.paste(img, mask=img.split()[3])
                    else:
                        background.paste(img, mask=img.split()[1])
                    img = background
                elif img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Create thumbnail
                img.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)
                
                # Save as JPEG for smaller size
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)
                
                logger.debug(f"Created thumbnail for {image_path}")
                return thumbnail_path
                
        except Exception as e:
            logger.error(f"Failed to create thumbnail for {image_path}: {e}")
            return None
    
    def create_thumbnails_batch(self, image_paths: list[Path], 
                              callback=None) -> dict[Path, Optional[Path]]:
        """Create thumbnails for multiple images in parallel"""
        results = {}
        futures = {}
        
        for image_path in image_paths:
            if self._is_supported_format(image_path):
                future = self._executor.submit(self.get_or_create_thumbnail, image_path)
                futures[future] = image_path
        
        for future in as_completed(futures):
            image_path = futures[future]
            try:
                thumbnail_path = future.result()
                results[image_path] = thumbnail_path
                if callback:
                    callback(image_path, thumbnail_path)
            except Exception as e:
                logger.error(f"Failed to process thumbnail for {image_path}: {e}")
                results[image_path] = None
        
        return results
    
    def _is_supported_format(self, image_path: Path) -> bool:
        """Check if the image format is supported"""
        return image_path.suffix.lower() in self.SUPPORTED_FORMATS
    
    def clear_cache(self):
        """Clear all cached thumbnails"""
        count = 0
        for thumbnail in self.cache_dir.glob("*.jpg"):
            try:
                thumbnail.unlink()
                count += 1
            except Exception as e:
                logger.error(f"Failed to delete thumbnail {thumbnail}: {e}")
        
        logger.info(f"Cleared {count} thumbnails from cache")
        return count
    
    def get_cache_size(self) -> int:
        """Get total size of cache in bytes"""
        total = 0
        for thumbnail in self.cache_dir.glob("*.jpg"):
            try:
                total += thumbnail.stat().st_size
            except:
                pass
        return total
    
    def cleanup_orphaned(self, valid_paths: set[Path]) -> int:
        """Remove thumbnails for images that no longer exist"""
        # Create a set of valid thumbnail names
        valid_thumbnails = {
            self.get_thumbnail_path(path).name 
            for path in valid_paths
        }
        
        removed = 0
        for thumbnail in self.cache_dir.glob("*.jpg"):
            if thumbnail.name not in valid_thumbnails:
                try:
                    thumbnail.unlink()
                    removed += 1
                except Exception as e:
                    logger.error(f"Failed to remove orphaned thumbnail {thumbnail}: {e}")
        
        if removed > 0:
            logger.info(f"Removed {removed} orphaned thumbnails")
        
        return removed
    
    def __del__(self):
        """Cleanup executor on deletion"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)