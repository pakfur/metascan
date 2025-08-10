from typing import List, Optional, Dict, Any
from pathlib import Path
import logging

from metascan.extractors.base import MetadataExtractor
from metascan.extractors.comfyui import ComfyUIExtractor
from metascan.extractors.comfyui_video import ComfyUIVideoExtractor
from metascan.extractors.swarmui import SwarmUIExtractor
from metascan.extractors.fooocus import FooocusExtractor
from metascan.utils.metadata_logger import MetadataParsingLogger

logger = logging.getLogger(__name__)


class MetadataExtractorManager:
    """Manages all metadata extractors"""
    
    def __init__(self, enable_logging: bool = True):
        self.extractors: List[MetadataExtractor] = [
            ComfyUIVideoExtractor(),  # Check video first for MP4 files
            FooocusExtractor(),       # Check Fooocus first as it has more specific markers  
            ComfyUIExtractor(),
            SwarmUIExtractor()        # SwarmUI last as it has more generic checks
        ]
        
        # Initialize metadata parsing logger
        self.parsing_logger = MetadataParsingLogger() if enable_logging else None
    
    def extract_metadata(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata using the appropriate extractor"""
        for extractor in self.extractors:
            extractor_name = extractor.__class__.__name__
            raw_data = None
            
            try:
                if extractor.can_extract(media_path):
                    # Try to get raw data for logging (if extraction fails)
                    try:
                        # Attempt to read raw metadata for debugging
                        if hasattr(extractor, '_read_raw_metadata'):
                            raw_data = extractor._read_raw_metadata(media_path)
                    except:
                        pass  # Ignore errors getting raw data
                    
                    metadata = extractor.extract(media_path)
                    if metadata:
                        logger.info(f"Extracted {metadata.get('source', 'unknown')} metadata from {media_path}")
                        
                        # Log successful extraction
                        if self.parsing_logger:
                            self.parsing_logger.log_extraction_attempt(
                                file_path=media_path,
                                extractor_name=extractor_name,
                                success=True,
                                metadata=metadata,
                                raw_data=raw_data
                            )
                        
                        return metadata
            except Exception as e:
                logger.error(f"Extractor {extractor_name} failed for {media_path}: {e}")
                
                # Log extraction failure
                if self.parsing_logger:
                    self.parsing_logger.log_extraction_attempt(
                        file_path=media_path,
                        extractor_name=extractor_name,
                        success=False,
                        error=e,
                        raw_data=raw_data
                    )
        
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