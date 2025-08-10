from pathlib import Path
from PIL import Image
from typing import Optional, Tuple
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import os

try:
    import ffmpeg
    HAS_FFMPEG_PYTHON = True
except ImportError:
    HAS_FFMPEG_PYTHON = False

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Manages thumbnail generation and caching"""
    
    DEFAULT_SIZE = (256, 256)
    SUPPORTED_FORMATS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.mp4'}
    
    def __init__(self, cache_dir: Path, thumbnail_size: Tuple[int, int] = DEFAULT_SIZE):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_size = thumbnail_size
        self._executor = ThreadPoolExecutor(max_workers=4)
    
    def get_thumbnail_path(self, media_path: Path) -> Path:
        """Get the cache path for a thumbnail"""
        # Create a unique filename based on original path and modification time
        stat = media_path.stat()
        unique_string = f"{media_path}_{stat.st_mtime}_{stat.st_size}"
        hash_name = hashlib.md5(unique_string.encode()).hexdigest()
        
        return self.cache_dir / f"{hash_name}.jpg"
    
    def get_or_create_thumbnail(self, media_path: Path) -> Optional[Path]:
        """Get thumbnail from cache or create if it doesn't exist"""
        if not self._is_supported_format(media_path):
            logger.warning(f"Unsupported media format: {media_path}")
            return None
        
        thumbnail_path = self.get_thumbnail_path(media_path)
        
        # Check if thumbnail exists and is newer than source
        if thumbnail_path.exists():
            if thumbnail_path.stat().st_mtime >= media_path.stat().st_mtime:
                return thumbnail_path
        
        # Create thumbnail
        return self._create_thumbnail(media_path, thumbnail_path)
    
    def _create_thumbnail(self, media_path: Path, thumbnail_path: Path) -> Optional[Path]:
        """Create a thumbnail for the given media file"""
        try:
            if media_path.suffix.lower() == '.mp4':
                return self._create_video_thumbnail(media_path, thumbnail_path)
            else:
                return self._create_image_thumbnail(media_path, thumbnail_path)
        except Exception as e:
            logger.error(f"Failed to create thumbnail for {media_path}: {e}")
            return None
    
    def _create_image_thumbnail(self, image_path: Path, thumbnail_path: Path) -> Optional[Path]:
        """Create a thumbnail for an image file"""
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
            logger.error(f"Failed to create image thumbnail for {image_path}: {e}")
            return None
    
    def _create_video_thumbnail(self, video_path: Path, thumbnail_path: Path) -> Optional[Path]:
        """Create a thumbnail for a video file"""
        try:
            # Try python-ffmpeg bindings first
            if HAS_FFMPEG_PYTHON and self._create_video_thumbnail_python(video_path, thumbnail_path):
                return thumbnail_path
            
            # Fallback to subprocess calls
            if self._create_video_thumbnail_ffmpeg(video_path, thumbnail_path):
                return thumbnail_path
            
            # Final fallback 
            return self._create_video_thumbnail_fallback(video_path, thumbnail_path)
            
        except Exception as e:
            logger.error(f"Failed to create video thumbnail for {video_path}: {e}")
            return None
    
    def _create_video_thumbnail_python(self, video_path: Path, thumbnail_path: Path) -> bool:
        """Create video thumbnail using python-ffmpeg bindings"""
        try:
            # Use ffmpeg-python to extract a frame
            stream = ffmpeg.input(str(video_path))
            
            # Seek to 1 second or 10% into the video, whichever is smaller
            # Use thumbnail filter for best frame selection
            stream = ffmpeg.filter(stream, 'thumbnail')
            
            # Scale to desired size while maintaining aspect ratio
            stream = ffmpeg.filter(
                stream, 'scale', 
                self.thumbnail_size[0], self.thumbnail_size[1],
                force_original_aspect_ratio='decrease'
            )
            
            # Output to thumbnail file
            stream = ffmpeg.output(stream, str(thumbnail_path), vframes=1, format='image2')
            
            # Run with overwrite and capture stderr
            ffmpeg.run(stream, overwrite_output=True, capture_stderr=True, quiet=True)
            
            if thumbnail_path.exists():
                logger.debug(f"Created video thumbnail using python-ffmpeg for {video_path}")
                return True
            
            return False
            
        except ffmpeg.Error as e:
            logger.debug(f"python-ffmpeg failed for {video_path}: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        except Exception as e:
            logger.debug(f"python-ffmpeg unexpected error for {video_path}: {e}")
            return False
    
    def _create_video_thumbnail_ffmpeg(self, video_path: Path, thumbnail_path: Path) -> bool:
        """Create video thumbnail using ffmpeg"""
        try:
            # Extract frame at 1 second (or 10% into video if shorter)
            cmd = [
                'ffmpeg', '-i', str(video_path), '-vf', 
                f'thumbnail,scale={self.thumbnail_size[0]}:{self.thumbnail_size[1]}:force_original_aspect_ratio=decrease',
                '-frames:v', '1', '-f', 'image2', '-y', str(thumbnail_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and thumbnail_path.exists():
                logger.debug(f"Created video thumbnail for {video_path}")
                return True
            
            return False
            
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _create_video_thumbnail_fallback(self, video_path: Path, thumbnail_path: Path) -> Optional[Path]:
        """Fallback method for video thumbnail creation"""
        try:
            # Try simpler ffmpeg command
            cmd = [
                'ffmpeg', '-i', str(video_path), '-ss', '00:00:01', '-vframes', '1',
                '-s', f'{self.thumbnail_size[0]}x{self.thumbnail_size[1]}',
                '-y', str(thumbnail_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and thumbnail_path.exists():
                logger.debug(f"Created fallback video thumbnail for {video_path}")
                return thumbnail_path
            
            # Last resort: create a placeholder thumbnail
            return self._create_video_placeholder(thumbnail_path)
            
        except Exception as e:
            logger.error(f"Video thumbnail fallback failed for {video_path}: {e}")
            return self._create_video_placeholder(thumbnail_path)
    
    def _create_video_placeholder(self, thumbnail_path: Path) -> Optional[Path]:
        """Create a placeholder thumbnail for videos when extraction fails"""
        try:
            # Create a simple placeholder image
            img = Image.new('RGB', self.thumbnail_size, (64, 64, 64))
            
            # Add a play button symbol
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            
            # Calculate play button size
            center_x, center_y = self.thumbnail_size[0] // 2, self.thumbnail_size[1] // 2
            button_size = min(self.thumbnail_size) // 4
            
            # Draw play triangle
            points = [
                (center_x - button_size//2, center_y - button_size//2),
                (center_x - button_size//2, center_y + button_size//2),
                (center_x + button_size//2, center_y)
            ]
            draw.polygon(points, fill=(255, 255, 255))
            
            img.save(thumbnail_path, 'JPEG', quality=85)
            logger.debug(f"Created video placeholder thumbnail")
            return thumbnail_path
            
        except Exception as e:
            logger.error(f"Failed to create video placeholder: {e}")
            return None
    
    def create_thumbnails_batch(self, media_paths: list[Path], 
                              callback=None) -> dict[Path, Optional[Path]]:
        """Create thumbnails for multiple media files in parallel"""
        results = {}
        futures = {}
        
        for media_path in media_paths:
            if self._is_supported_format(media_path):
                future = self._executor.submit(self.get_or_create_thumbnail, media_path)
                futures[future] = media_path
        
        for future in as_completed(futures):
            media_path = futures[future]
            try:
                thumbnail_path = future.result()
                results[media_path] = thumbnail_path
                if callback:
                    callback(media_path, thumbnail_path)
            except Exception as e:
                logger.error(f"Failed to process thumbnail for {media_path}: {e}")
                results[media_path] = None
        
        return results
    
    def _is_supported_format(self, media_path: Path) -> bool:
        """Check if the media format is supported"""
        return media_path.suffix.lower() in self.SUPPORTED_FORMATS
    
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
    
    def move_cache_to_trash(self) -> bool:
        """Move entire thumbnail cache directory to platform-appropriate trash"""
        try:
            if not self.cache_dir.exists():
                logger.info("Cache directory doesn't exist, nothing to clean")
                return True
            
            # Count files before moving
            thumbnail_files = list(self.cache_dir.glob("*.jpg"))
            if not thumbnail_files:
                logger.info("No thumbnail files to move to trash")
                return True
            
            # Move cache directory to trash using platform-specific method
            self._move_cache_to_trash_platform()
            
            # Recreate cache directory
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Moved {len(thumbnail_files)} thumbnails to trash")
            return True
            
        except Exception as e:
            logger.error(f"Failed to move thumbnail cache to trash: {e}")
            return False
    
    def _move_cache_to_trash_platform(self):
        """Move cache directory to platform-specific trash location"""
        import platform
        import shutil
        
        system = platform.system()
        
        if system == "Darwin":  # macOS
            # Use macOS Trash
            trash_dir = Path.home() / ".Trash"
            trash_dir.mkdir(exist_ok=True)
            
            # Generate unique name if directory already exists in trash
            dest_path = trash_dir / self.cache_dir.name
            counter = 1
            while dest_path.exists():
                dest_path = trash_dir / f"{self.cache_dir.name}_{counter}"
                counter += 1
            
            shutil.move(str(self.cache_dir), str(dest_path))
        
        elif system == "Windows":
            # Use Windows Recycle Bin via shell
            import subprocess
            # Use PowerShell to move to recycle bin
            ps_command = f'Remove-Item -Path "{self.cache_dir}" -Recurse -Force'
            subprocess.run(["powershell", "-Command", ps_command], check=True)
        
        elif system == "Linux":
            # Use XDG trash
            trash_dir = Path.home() / ".local" / "share" / "Trash" / "files"
            trash_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate unique name if directory already exists in trash
            dest_path = trash_dir / self.cache_dir.name
            counter = 1
            while dest_path.exists():
                dest_path = trash_dir / f"{self.cache_dir.name}_{counter}"
                counter += 1
            
            shutil.move(str(self.cache_dir), str(dest_path))
        
        else:
            # Fallback: just delete the directory
            shutil.rmtree(self.cache_dir)
    
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