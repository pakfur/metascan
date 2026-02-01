"""
Improved ComfyUI video metadata extractor with node-specific handlers.
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class NodeHandler:
    """Base class for ComfyUI node-specific metadata extraction handlers"""

    def can_handle(self, class_type: str) -> bool:
        """Check if this handler can process the given node type"""
        return False

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        """Extract metadata from the node and update the result dictionary"""
        pass


class CLIPTextEncodeHandler(NodeHandler):
    """Handler for CLIPTextEncode nodes (prompts)"""

    def can_handle(self, class_type: str) -> bool:
        return class_type == "CLIPTextEncode"

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})
        text = inputs.get("text", "").strip()

        if not text:
            return

        # Try to determine if positive or negative based on title or content
        meta = node_data.get("_meta", {})
        title = meta.get("title", "").lower()

        # Check title first for clear indicators
        if "negative" in title or "neg" in title:
            if "negative_prompt" not in result:
                result["negative_prompt"] = text
        elif "positive" in title or "pos" in title:
            if "prompt" not in result:
                result["prompt"] = text
        else:
            # Heuristic: check content for negative keywords
            negative_indicators = [
                "negative",
                "bad",
                "ugly",
                "worst",
                "low quality",
                "poor",
            ]
            if any(indicator in text.lower() for indicator in negative_indicators):
                if "negative_prompt" not in result:
                    result["negative_prompt"] = text
            else:
                if "prompt" not in result:
                    result["prompt"] = text


class LoraLoaderHandler(NodeHandler):
    """Handler for LoRA loader nodes"""

    def can_handle(self, class_type: str) -> bool:
        return "lora" in class_type.lower() and "loader" in class_type.lower()

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Extract LoRA name
        lora_name = inputs.get("lora_name", "")
        if not lora_name:
            return

        # Remove file extension if present
        lora_name = (
            lora_name.replace(".safetensors", "")
            .replace(".ckpt", "")
            .replace(".pt", "")
        )

        # Extract strength/weight
        strength = inputs.get("strength_model")
        if strength is None:
            strength = inputs.get("strength", 1.0)

        # Add to loras list
        if "loras" not in result:
            result["loras"] = []

        # Check if this LoRA is already in the list (avoid duplicates)
        existing_lora = next(
            (lora for lora in result["loras"] if lora["lora_name"] == lora_name), None
        )

        if not existing_lora:
            weight = float(strength) if strength is not None else 1.0
            # Round weight to avoid floating point precision issues
            weight = round(weight, 2)
            result["loras"].append({"lora_name": lora_name, "lora_weight": weight})


class UNETLoaderHandler(NodeHandler):
    """Handler for UNET/Model loader nodes"""

    def can_handle(self, class_type: str) -> bool:
        return class_type in [
            "UNETLoader",
            "CheckpointLoaderSimple",
            "DiffusionModelLoaderKJ",
        ]

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Extract model name
        model_name = (
            inputs.get("unet_name")
            or inputs.get("ckpt_name")
            or inputs.get("model_name")
            or ""
        )

        if not model_name:
            return

        # Remove file extension
        model_name = (
            model_name.replace(".safetensors", "")
            .replace(".ckpt", "")
            .replace(".pt", "")
        )

        # Store as list to support multiple models (e.g., high/low noise variants)
        if "models" not in result:
            result["models"] = []

        if model_name not in result["models"]:
            result["models"].append(model_name)


class SamplerHandler(NodeHandler):
    """Handler for sampler nodes (KSampler and variants)"""

    def can_handle(self, class_type: str) -> bool:
        return "sampler" in class_type.lower()

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Extract sampler name (handle various formats)
        sampler_name = inputs.get("sampler_name", "")
        if sampler_name:
            # Simplify sampler names like "multistep/res_2m" to "RES4LYF" if needed
            # This is a custom mapping based on the specific workflow
            if "res" in sampler_name.lower():
                result["sampler"] = "RES4LYF"
            else:
                result["sampler"] = sampler_name

        # Extract scheduler
        scheduler = inputs.get("scheduler")
        if scheduler and "scheduler" not in result:
            result["scheduler"] = scheduler

        # Extract steps
        steps = inputs.get("steps")
        if steps is not None and "steps" not in result:
            # Handle both direct values and references
            if isinstance(steps, (int, float)):
                result["steps"] = int(steps)
            elif isinstance(steps, list) and len(steps) > 0:
                # It's a reference to another node, skip for now
                pass

        # Extract CFG scale
        cfg = inputs.get("cfg")
        if cfg is not None and "cfg_scale" not in result:
            # Round to avoid floating point precision issues
            result["cfg_scale"] = round(float(cfg), 2)

        # Extract seed
        seed = inputs.get("seed")
        if seed is not None and "seed" not in result:
            result["seed"] = int(seed)

        # Extract denoise strength
        denoise = inputs.get("denoise")
        if denoise is not None and "denoise" not in result:
            result["denoise"] = float(denoise)


class VideoGeneratorHandler(NodeHandler):
    """Handler for video generation nodes"""

    def can_handle(self, class_type: str) -> bool:
        return class_type in ["WanImageToVideo", "VHS_VideoCombine", "AnimateDiff"]

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Extract dimensions
        if "width" not in result:
            width = inputs.get("width")
            if width is not None:
                result["width"] = int(width)

        if "height" not in result:
            height = inputs.get("height")
            if height is not None:
                result["height"] = int(height)

        # Extract frame rate
        if "frame_rate" not in result:
            frame_rate = inputs.get("frame_rate") or inputs.get("fps")
            if frame_rate is not None:
                result["frame_rate"] = float(frame_rate)

        # Extract video length
        if "video_length" not in result:
            length = inputs.get("length") or inputs.get("num_frames")
            if length is not None:
                result["video_length"] = int(length)


class ComfyUIVideoExtractorImproved(MetadataExtractor):
    """Improved ComfyUI video metadata extractor with node-specific handlers"""

    def __init__(self):
        super().__init__()
        # Initialize node handlers
        self.handlers: List[NodeHandler] = [
            CLIPTextEncodeHandler(),
            LoraLoaderHandler(),
            UNETLoaderHandler(),
            SamplerHandler(),
            VideoGeneratorHandler(),
        ]

    def can_extract(self, media_path: Path) -> bool:
        if media_path.suffix.lower() not in {".mp4", ".webm"}:
            return False

        metadata = self._get_video_metadata(media_path)
        return "prompt" in metadata or "workflow" in metadata

    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        try:
            metadata = self._get_video_metadata(media_path)

            result: Dict[str, Any] = {"source": "ComfyUI", "raw_metadata": {}}

            # Extract prompt data
            if "prompt" in metadata:
                try:
                    prompt_data = metadata["prompt"]
                    # If it's a string, parse it
                    if isinstance(prompt_data, str):
                        prompt_data = json.loads(prompt_data)

                    result["raw_metadata"]["prompt"] = prompt_data

                    # Process each node with appropriate handler
                    self._process_nodes(prompt_data, result)

                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(
                        f"Failed to parse ComfyUI prompt data from {media_path}: {e}"
                    )

            # Store workflow if present
            if "workflow" in metadata:
                try:
                    workflow_data = metadata["workflow"]
                    if isinstance(workflow_data, str):
                        workflow_data = json.loads(workflow_data)
                    result["raw_metadata"]["workflow"] = workflow_data
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(
                        f"Failed to parse ComfyUI workflow data from {media_path}: {e}"
                    )

            # Post-process results
            self._post_process_results(result)

            return result if result["raw_metadata"] else None

        except Exception as e:
            logger.error(f"Failed to extract ComfyUI metadata from {media_path}: {e}")
            return None

    def _process_nodes(
        self, prompt_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        """Process all nodes using appropriate handlers"""
        for node_id, node_data in prompt_data.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")

            # Try each handler
            for handler in self.handlers:
                if handler.can_handle(class_type):
                    try:
                        handler.extract(node_id, node_data, result)
                    except Exception as e:
                        logger.debug(
                            f"Handler {handler.__class__.__name__} failed for node {node_id}: {e}"
                        )

    def _post_process_results(self, result: Dict[str, Any]) -> None:
        """Clean up and finalize extracted metadata"""

        # Convert models list to single model if only one
        if "models" in result:
            if len(result["models"]) == 1:
                result["model"] = result["models"][0]
                del result["models"]
            elif len(result["models"]) > 1:
                # Keep as models list for multiple models
                pass
            else:
                del result["models"]

        # Sort LoRAs by name for consistency
        if "loras" in result:
            result["loras"].sort(key=lambda x: x["lora_name"])

        # Remove None values
        keys_to_remove = [
            k
            for k, v in result.items()
            if v is None and k not in ["source", "raw_metadata"]
        ]
        for key in keys_to_remove:
            del result[key]

    def _get_video_metadata(self, media_path: Path) -> Dict[str, Any]:
        """Extract metadata from video file using exiftool with ffprobe fallback"""
        try:
            result = subprocess.run(
                ["exiftool", "-Comment", "-json", str(media_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data and len(data) > 0:
                    comment = data[0].get("Comment", "")
                    if comment:
                        try:
                            # ComfyUI stores metadata as JSON in the comment field
                            comment_data = json.loads(comment)
                            if isinstance(comment_data, dict):
                                return comment_data
                            else:
                                return {"parsed_comment": comment_data}
                        except json.JSONDecodeError:
                            # If it's not JSON, return as-is
                            return {"comment": comment}

            return self._get_video_metadata_ffprobe(media_path)

        except Exception as e:
            logger.error(f"Failed to extract video metadata from {media_path}: {e}")
            return {}

    def _get_video_metadata_ffprobe(self, media_path: Path) -> Dict[str, Any]:
        """Fallback metadata extraction using ffprobe"""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(media_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                format_data = data.get("format", {})
                tags = format_data.get("tags", {})

                # Look for ComfyUI metadata in various tag fields
                for key, value in tags.items():
                    if key.lower() in ["comment", "description", "title"]:
                        try:
                            metadata = json.loads(value)
                            if isinstance(metadata, dict) and (
                                "prompt" in metadata or "workflow" in metadata
                            ):
                                return metadata
                        except json.JSONDecodeError:
                            continue

                if isinstance(tags, dict):
                    return tags
                else:
                    return {}

            return {}

        except Exception as e:
            logger.error(f"ffprobe fallback failed for {media_path}: {e}")
            return {}
