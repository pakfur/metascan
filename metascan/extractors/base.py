from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image
import json
import logging

logger = logging.getLogger(__name__)


class MetadataExtractor(ABC):
    """Base class for metadata extractors"""
    
    @abstractmethod
    def can_extract(self, image_path: Path) -> bool:
        """Check if this extractor can handle the given image"""
        pass
    
    @abstractmethod
    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata from the image"""
        pass
    
    def _get_png_metadata(self, image_path: Path) -> Dict[str, Any]:
        """Extract metadata from PNG info"""
        try:
            with Image.open(image_path) as img:
                return img.info
        except Exception as e:
            logger.error(f"Failed to read PNG metadata from {image_path}: {e}")
            return {}
    
    def _get_exif_metadata(self, image_path: Path) -> Dict[str, Any]:
        """Extract EXIF metadata"""
        try:
            with Image.open(image_path) as img:
                exif = img.getexif()
                if exif:
                    return {k: v for k, v in exif.items()}
                return {}
        except Exception as e:
            logger.error(f"Failed to read EXIF from {image_path}: {e}")
            return {}
    
    def _safe_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        """Safely convert value to int"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    def _safe_float(self, value: Any, default: Optional[float] = None) -> Optional[float]:
        """Safely convert value to float"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return default