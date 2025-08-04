from typing import List, Optional, Dict, Any
from pathlib import Path
import logging

from metascan.extractors.base import MetadataExtractor
from metascan.extractors.comfyui import ComfyUIExtractor
from metascan.extractors.swarmui import SwarmUIExtractor
from metascan.extractors.fooocus import FooocusExtractor

logger = logging.getLogger(__name__)


class MetadataExtractorManager:
    """Manages all metadata extractors"""
    
    def __init__(self):
        self.extractors: List[MetadataExtractor] = [
            FooocusExtractor(),   # Check Fooocus first as it has more specific markers
            ComfyUIExtractor(),
            SwarmUIExtractor()    # SwarmUI last as it has more generic checks
        ]
    
    def extract_metadata(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata using the appropriate extractor"""
        for extractor in self.extractors:
            try:
                if extractor.can_extract(image_path):
                    metadata = extractor.extract(image_path)
                    if metadata:
                        logger.info(f"Extracted {metadata.get('source', 'unknown')} metadata from {image_path}")
                        return metadata
            except Exception as e:
                logger.error(f"Extractor {extractor.__class__.__name__} failed for {image_path}: {e}")
        
        logger.debug(f"No metadata extractor found for {image_path}")
        return None


__all__ = [
    'MetadataExtractor',
    'MetadataExtractorManager',
    'ComfyUIExtractor',
    'SwarmUIExtractor',
    'FooocusExtractor'
]