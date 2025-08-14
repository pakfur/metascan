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
            FooocusExtractor(),  # Check Fooocus first as it has more specific markers
            ComfyUIExtractor(),
            SwarmUIExtractor(),  # SwarmUI last as it has more generic checks
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
                        if hasattr(extractor, "_read_raw_metadata"):
                            raw_data = extractor._read_raw_metadata(media_path)
                    except:
                        pass  # Ignore errors getting raw data

                    metadata = extractor.extract(media_path)
                    if metadata:
                        logger.info(
                            f"Extracted {metadata.get('source', 'unknown')} metadata from {media_path}"
                        )

                        # Log successful extraction
                        if self.parsing_logger:
                            self.parsing_logger.log_extraction_attempt(
                                file_path=media_path,
                                extractor_name=extractor_name,
                                success=True,
                                metadata=metadata,
                                raw_data=raw_data,
                            )

                        # Check for and log parsing errors that occurred during extraction
                        if "parsing_errors" in metadata and self.parsing_logger:
                            for parse_error in metadata["parsing_errors"]:
                                # Create a custom exception for the parsing error
                                error_msg = f"{parse_error['error_type']}: {parse_error['error_message']}"

                                # Create a dynamic exception class with the right name
                                error_type = parse_error["error_type"]
                                parse_exception: Exception
                                if error_type == "JSONDecodeError":
                                    import json

                                    parse_exception = json.JSONDecodeError(
                                        parse_error["error_message"], "", 0
                                    )
                                else:
                                    # Create a generic exception with custom attributes
                                    parse_exception = Exception(error_msg)
                                    # Add error_type as a dynamic attribute for logging
                                    setattr(parse_exception, "error_type", error_type)

                                self.parsing_logger.log_extraction_attempt(
                                    file_path=media_path,
                                    extractor_name=f"{extractor_name}_parsing",
                                    success=False,
                                    error=parse_exception,
                                    raw_data=parse_error.get("raw_data", ""),
                                )

                        # Remove parsing errors from metadata before returning
                        if "parsing_errors" in metadata:
                            del metadata["parsing_errors"]

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
                        raw_data=raw_data,
                    )

        logger.debug(f"No metadata extractor found for {media_path}")
        return None


__all__ = [
    "MetadataExtractor",
    "MetadataExtractorManager",
    "ComfyUIExtractor",
    "ComfyUIVideoExtractor",
    "SwarmUIExtractor",
    "FooocusExtractor",
]
