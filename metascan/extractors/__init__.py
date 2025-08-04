from typing import List, Optional, Dict, Any
from pathlib import Path
import logging

from metascan.extractors.base import MetadataExtractor
from metascan.extractors.comfyui import ComfyUIExtractor
from metascan.extractors.comfyui_video import ComfyUIVideoExtractor
from metascan.extractors.swarmui import SwarmUIExtractor
from metascan.extractors.fooocus import FooocusExtractor

logger = logging.getLogger(__name__)


class MetadataExtractorManager:
    """Manages all metadata extractors"""
    
    def __init__(self):
        self.extractors: List[MetadataExtractor] = [
            ComfyUIVideoExtractor(),  # Check video first for MP4 files
            FooocusExtractor(),       # Check Fooocus first as it has more specific markers  
            ComfyUIExtractor(),
            SwarmUIExtractor()        # SwarmUI last as it has more generic checks
        ]
    
    def extract_metadata(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata using the appropriate extractor"""
        for extractor in self.extractors:
            try:
                if extractor.can_extract(media_path):
                    metadata = extractor.extract(media_path)
                    if metadata:
                        logger.info(f"Extracted {metadata.get('source', 'unknown')} metadata from {media_path}")
                        return metadata
            except Exception as e:
                logger.error(f"Extractor {extractor.__class__.__name__} failed for {media_path}: {e}")
        
        logger.debug(f"No metadata extractor found for {media_path}")
        return None


__all__ = [
    'MetadataExtractor',
    'MetadataExtractorManager',
    'ComfyUIExtractor',
    'ComfyUIVideoExtractor',
    'SwarmUIExtractor',
    'FooocusExtractor'
]