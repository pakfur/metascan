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
    def can_extract(self, media_path: Path) -> bool:
        """Check if this extractor can handle the given media file"""
        pass

    @abstractmethod
    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata from the media file"""
        pass

    def _get_png_metadata(self, image_path: Path) -> Dict[str, Any]:
        """Extract metadata from PNG info"""
        try:
            with Image.open(image_path) as img:
                return dict(img.info)  # Explicitly convert to dict to satisfy mypy
        except Exception as e:
            logger.error(f"Failed to read PNG metadata from {image_path}: {e}")
            return {}

    def _get_exif_metadata(self, image_path: Path) -> Dict[str, Any]:
        """Extract EXIF metadata and embedded text data"""
        try:
            with Image.open(image_path) as img:
                metadata = {}

                # For PNG files, check text chunks
                if img.format == "PNG" and hasattr(img, "text"):
                    metadata.update(img.text)

                # For JPEG/other formats, check EXIF
                exif = img.getexif()
                if exif:
                    from PIL.ExifTags import TAGS, IFD

                    # Process standard EXIF tags
                    for tag_id, value in exif.items():
                        tag_name = TAGS.get(tag_id, str(tag_id))
                        metadata[tag_name] = value

                    # Check EXIF IFD for UserComment and other data
                    try:
                        exif_ifd = exif.get_ifd(IFD.Exif)
                        if exif_ifd:
                            for tag_id, value in exif_ifd.items():
                                tag_name = TAGS.get(tag_id, str(tag_id))

                                # Handle UserComment specially - it's often Unicode encoded
                                if tag_name == "UserComment" and isinstance(
                                    value, bytes
                                ):
                                    try:
                                        # SwarmUI uses Unicode encoding
                                        if value.startswith(b"UNICODE\x00"):
                                            decoded = (
                                                value[8:]
                                                .decode("utf-16-le", errors="ignore")
                                                .rstrip("\x00")
                                            )
                                            metadata[tag_name] = decoded
                                        else:
                                            # Try UTF-8 or ASCII
                                            decoded = value.decode(
                                                "utf-8", errors="ignore"
                                            )
                                            metadata[tag_name] = decoded
                                    except:
                                        metadata[tag_name] = value
                                else:
                                    metadata[tag_name] = value
                    except:
                        # If IFD access fails, continue with what we have
                        pass

                return metadata
        except Exception as e:
            logger.error(f"Failed to read metadata from {image_path}: {e}")
            return {}

    def _safe_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        """Safely convert value to int"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def _safe_float(
        self, value: Any, default: Optional[float] = None
    ) -> Optional[float]:
        """Safely convert value to float"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
