from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from PIL import Image
import json
import logging

logger = logging.getLogger(__name__)


class MetadataExtractor(ABC):
    """
    Base class for metadata extractors that extract AI generation metadata from media files.

    This abstract base class defines the interface for pluggable metadata extractors that can
    parse metadata embedded in images and videos created by various AI generation tools
    (ComfyUI, SwarmUI, Fooocus, etc.).

    ## Plugin Architecture

    Extractors are automatically discovered and managed by MetadataExtractorManager, which
    tries each extractor in order until one successfully extracts metadata. To create a new
    extractor plugin:

    1. Inherit from this base class
    2. Implement the `can_extract()` and `extract()` methods
    3. Add your extractor to the MetadataExtractorManager in extractors/__init__.py
    4. Follow the standardized return schema documented in `extract()`

    ## Indexing and Filtering

    The extracted metadata is automatically indexed in the database for fast filtering:
    - `source` -> Indexed as "source" type for tool filtering
    - `model` -> Indexed as "model" type for model filtering
    - `prompt` -> Tokenized and indexed as "prompt" type for text search
    - `loras[].lora_name` -> Indexed as "lora" type for LoRA filtering
    - `tags[]` -> Indexed as "tag" type for custom tag filtering

    ## Performance Considerations

    - `can_extract()` should be fast as it's called for every file
    - Use lazy loading and avoid expensive operations in `can_extract()`
    - Cache expensive computations if the same extractor is used multiple times
    - Handle malformed data gracefully to prevent crashes during batch processing

    ## Error Handling

    - Always wrap operations in try-catch blocks
    - Log errors with appropriate detail levels (error vs debug)
    - Return None from `extract()` if metadata cannot be parsed
    - Use `parsing_errors` field to report specific parsing issues for debugging

    ## Testing

    When implementing new extractors:
    - Test with various file formats your tool supports
    - Test with malformed/incomplete metadata
    - Test with files that don't contain your tool's metadata
    - Verify proper type conversion of numeric fields
    """

    @abstractmethod
    def can_extract(self, media_path: Path) -> bool:
        """
        Check if this extractor can extract metadata from the given media file.

        This method should perform a quick check to determine if the file contains
        metadata that this extractor can parse. It should be fast and not perform
        heavy operations since it's called for every file.

        Args:
            media_path: Path to the media file to check

        Returns:
            True if this extractor can handle the file, False otherwise

        Example:
            def can_extract(self, media_path: Path) -> bool:
                if media_path.suffix.lower() not in ['.png', '.jpg', '.jpeg']:
                    return False
                metadata = self._get_exif_metadata(media_path)
                return 'my_tool_signature' in metadata
        """
        pass

    @abstractmethod
    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """
        Extract metadata from the given media file.

        This method should parse the metadata embedded in the file and return a standardized
        dictionary with the extracted information. If extraction fails or no valid metadata
        is found, return None.

        Args:
            media_path: Path to the media file to extract metadata from

        Returns:
            Dictionary with extracted metadata following the standard schema, or None if
            extraction failed or no metadata was found.

        Expected Return Schema:
        {
            # REQUIRED FIELDS
            "source": str,                    # Tool that generated the image ("ComfyUI", "SwarmUI", "Fooocus", etc.)
            "raw_metadata": Dict[str, Any],   # Original raw metadata for debugging/logging

            # STANDARD AI GENERATION FIELDS (Optional)
            "prompt": Optional[str],              # Positive text prompt
            "negative_prompt": Optional[str],     # Negative text prompt
            "model": Optional[str],               # AI model name/checkpoint used (single model)
            "models": Optional[List[str]],        # AI model names (multiple models - preferred for videos)
            "sampler": Optional[str],             # Sampling method ("euler", "dpm++_2m", etc.)
            "scheduler": Optional[str],           # Scheduler type ("normal", "karras", etc.)
            "steps": Optional[int],               # Number of sampling steps
            "cfg_scale": Optional[float],         # CFG guidance scale
            "seed": Optional[int],                # Random seed used

            # VIDEO-SPECIFIC FIELDS (Optional - for video generation)
            "frame_rate": Optional[float],        # Video frame rate
            "duration": Optional[float],          # Video duration in seconds
            "video_length": Optional[int],        # Number of frames

            # LORA INFORMATION (Optional)
            "loras": List[Dict[str, Union[str, float]]],  # List of LoRA models used
            # Each LoRA dict should have:
            # {
            #     "lora_name": str,    # Name of the LoRA model
            #     "lora_weight": float # Weight/strength of the LoRA (0.0-1.0+)
            # }

            # CUSTOM TAGS (Optional)
            "tags": List[str],                    # Custom tags for categorization

            # ADDITIONAL METADATA (Optional)
            "width": Optional[int],               # Generation width (if different from image size)
            "height": Optional[int],              # Generation height (if different from image size)
            "batch_size": Optional[int],          # Batch size used
            "clip_skip": Optional[int],           # CLIP skip value

            # ERROR HANDLING (Internal use)
            "parsing_errors": List[Dict[str, Any]]  # List of parsing errors encountered
            # Each error dict should have:
            # {
            #     "error_type": str,      # Type of error ("JSONDecodeError", "ValueError", etc.)
            #     "error_message": str,   # Error description
            #     "raw_data": str         # Raw data that failed to parse
            # }
        }

        Notes:
        - Only "source" and "raw_metadata" are required; all other fields are optional
        - Values should be properly typed (int, float, str, etc.) not strings of numbers
        - Use the helper methods _safe_int() and _safe_float() for safe type conversion
        - The "parsing_errors" field is automatically processed by MetadataExtractorManager
        - All string fields are case-sensitive and should preserve original casing
        - LoRA weights can be > 1.0 for some tools
        - Tags should be lowercase for consistent filtering

        Example:
            def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
                try:
                    metadata = self._get_exif_metadata(media_path)
                    if 'my_tool_params' not in metadata:
                        return None

                    result = {
                        "source": "MyTool",
                        "raw_metadata": {"params": metadata['my_tool_params']},
                        "prompt": metadata.get('positive_prompt'),
                        "model": metadata.get('model_name'),
                        "steps": self._safe_int(metadata.get('steps')),
                        "cfg_scale": self._safe_float(metadata.get('cfg')),
                        "loras": [
                            {"lora_name": "style_lora", "lora_weight": 0.8}
                        ]
                    }
                    return result
                except Exception as e:
                    logger.error(f"Extraction failed for {media_path}: {e}")
                    return None
        """
        pass

    def _get_png_metadata(self, image_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from PNG text chunks.

        Args:
            image_path: Path to the PNG image file

        Returns:
            Dictionary of metadata keys and values from PNG text chunks
        """
        try:
            with Image.open(image_path) as img:
                return dict(img.info)  # Explicitly convert to dict to satisfy mypy
        except Exception as e:
            logger.error(f"Failed to read PNG metadata from {image_path}: {e}")
            return {}

    def _get_exif_metadata(self, image_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from image EXIF data and PNG text chunks.

        This method handles both PNG text chunks and EXIF data from JPEG/other formats.
        It also handles special encoding cases like SwarmUI's Unicode UserComment.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary of metadata keys and values extracted from the image
        """
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
                        pass

                return metadata
        except Exception as e:
            logger.error(f"Failed to read metadata from {image_path}: {e}")
            return {}

    def _safe_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        """
        Safely convert a value to integer with error handling.

        Args:
            value: Value to convert to int (can be str, int, float, etc.)
            default: Default value to return if conversion fails

        Returns:
            Converted integer value or default if conversion failed

        Example:
            steps = self._safe_int(metadata.get('steps'))  # Returns int or None
            steps = self._safe_int(metadata.get('steps'), 20)  # Returns int or 20
        """
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def _safe_float(
        self, value: Any, default: Optional[float] = None
    ) -> Optional[float]:
        """
        Safely convert a value to float with error handling.

        Args:
            value: Value to convert to float (can be str, int, float, etc.)
            default: Default value to return if conversion fails

        Returns:
            Converted float value or default if conversion failed

        Example:
            cfg = self._safe_float(metadata.get('cfg_scale'))  # Returns float or None
            cfg = self._safe_float(metadata.get('cfg_scale'), 7.5)  # Returns float or 7.5
        """
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _create_lora_dict(
        self, name: str, weight: Union[int, float, str]
    ) -> Dict[str, Union[str, float]]:
        """
        Create a standardized LoRA dictionary for the metadata schema.

        Args:
            name: Name of the LoRA model
            weight: Weight/strength value (will be converted to float)

        Returns:
            Dictionary with 'lora_name' and 'lora_weight' keys

        Example:
            lora = self._create_lora_dict("style_lora", "0.8")
            # Returns: {"lora_name": "style_lora", "lora_weight": 0.8}
        """
        return {
            "lora_name": str(name),
            "lora_weight": self._safe_float(weight, 1.0) or 1.0,
        }
