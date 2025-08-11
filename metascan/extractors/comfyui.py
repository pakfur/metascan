import json
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class ComfyUIExtractor(MetadataExtractor):
    """Extract metadata from ComfyUI generated images"""

    def can_extract(self, media_path: Path) -> bool:
        """Check if image contains ComfyUI metadata"""
        # Skip video files - they should be handled by ComfyUIVideoExtractor
        if media_path.suffix.lower() == ".mp4":
            return False

        metadata = self._get_exif_metadata(media_path)
        return "prompt" in metadata or "workflow" in metadata

    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """Extract ComfyUI metadata"""
        try:
            metadata = self._get_exif_metadata(media_path)

            result: Dict[str, Any] = {"source": "ComfyUI", "raw_metadata": {}}

            # Extract prompt data
            if "prompt" in metadata:
                try:
                    prompt_data = json.loads(metadata["prompt"])
                    result["raw_metadata"]["prompt"] = prompt_data

                    # Extract common parameters
                    extracted = self._extract_parameters(prompt_data)
                    result.update(extracted)
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to parse ComfyUI prompt JSON from {media_path}"
                    )

            # Extract workflow if present
            if "workflow" in metadata:
                try:
                    workflow_data = json.loads(metadata["workflow"])
                    result["raw_metadata"]["workflow"] = workflow_data
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to parse ComfyUI workflow JSON from {media_path}"
                    )

            return result if result["raw_metadata"] else None

        except Exception as e:
            logger.error(f"Failed to extract ComfyUI metadata from {media_path}: {e}")
            return None

    def _extract_parameters(self, prompt_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract common parameters from ComfyUI prompt data"""
        extracted: Dict[str, Any] = {}
        loras: List[Dict[str, Any]] = []

        # Look for KSampler node
        for node_id, node_data in prompt_data.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            if class_type == "KSampler":
                extracted["sampler"] = inputs.get("sampler_name")
                extracted["steps"] = self._safe_int(inputs.get("steps"))
                extracted["cfg_scale"] = self._safe_float(inputs.get("cfg"))
                extracted["seed"] = self._safe_int(inputs.get("seed"))
                extracted["scheduler"] = inputs.get("scheduler")

            elif class_type == "CheckpointLoaderSimple":
                extracted["model"] = inputs.get("ckpt_name")

            elif class_type == "CLIPTextEncode":
                # Check if this is positive or negative prompt
                text = inputs.get("text", "")
                if text and "prompt" not in extracted:
                    # Try to determine if positive or negative
                    # This is a heuristic - ComfyUI doesn't explicitly mark which is which
                    if any(
                        neg in text.lower()
                        for neg in ["negative", "bad", "ugly", "worst"]
                    ):
                        extracted["negative_prompt"] = text
                    else:
                        extracted["prompt"] = text
                elif text and "negative_prompt" not in extracted:
                    extracted["negative_prompt"] = text

            elif class_type == "LoraLoader":
                # Extract LoRA information
                lora_name = inputs.get("lora_name", "")
                lora_weight = inputs.get(
                    "strength_model", 1.0
                )  # Default weight if not specified

                if lora_name:
                    # Remove .safetensors extension if present
                    lora_name_clean = lora_name.replace(".safetensors", "")
                    loras.append(
                        {
                            "lora_name": lora_name_clean,
                            "lora_weight": self._safe_float(lora_weight) or 1.0,
                        }
                    )

        # Add LoRAs to extracted data
        if loras:
            extracted["loras"] = loras

        return extracted
