import json
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
import re

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class FooocusExtractor(MetadataExtractor):
    """Extract metadata from Fooocus generated images"""

    def can_extract(self, media_path: Path) -> bool:
        """Check if image contains Fooocus metadata"""
        # Skip video files - they're not supported by Fooocus image extractor
        if media_path.suffix.lower() == ".mp4":
            return False

        metadata = self._get_exif_metadata(media_path)

        # Fooocus stores metadata in different ways
        # 1. In a "parameters" field (newer versions)
        if "parameters" in metadata:
            try:
                params = metadata["parameters"]
                if isinstance(params, str):
                    # Try to parse as JSON and check for Fooocus-specific fields
                    data = json.loads(params)
                    if isinstance(data, dict) and (
                        "fooocus" in str(data).lower() or "metadata_scheme" in data
                    ):
                        return True
            except:
                pass

        # 2. Check for fooocus_scheme field
        if "fooocus_scheme" in metadata:
            return True

        # 3. In Comment or Description fields (older versions)
        for field in ["Comment", "Description", "comment", "description"]:
            if field in metadata:
                text = metadata[field]
                # Check for Fooocus-specific patterns
                if "Fooocus" in text or ("Steps:" in text and "Sampler:" in text):
                    return True

        return False

    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract Fooocus metadata"""
        try:
            metadata = self._get_exif_metadata(image_path)

            result: Dict[str, Any] = {"source": "Fooocus", "raw_metadata": {}}

            # Check for JSON format in parameters field (newer Fooocus versions)
            if "parameters" in metadata:
                try:
                    params = metadata["parameters"]
                    if isinstance(params, str):
                        data = json.loads(params)
                        if isinstance(data, dict):
                            result["raw_metadata"]["parameters"] = data

                            # Extract from JSON format
                            extracted = self._extract_from_json(data)
                            result.update(extracted)

                            # Also store fooocus_scheme if present
                            if "fooocus_scheme" in metadata:
                                result["raw_metadata"]["fooocus_scheme"] = metadata[
                                    "fooocus_scheme"
                                ]

                            return result
                except json.JSONDecodeError:
                    pass

            # Fallback to text format in Comment/Description fields
            metadata_text = None
            for field in ["Comment", "Description", "comment", "description"]:
                if field in metadata:
                    metadata_text = metadata[field]
                    result["raw_metadata"][field] = metadata_text
                    break

            if not metadata_text:
                return None

            # Extract parameters from text
            extracted = self._extract_from_text(metadata_text)
            result.update(extracted)

            return result

        except Exception as e:
            logger.error(f"Failed to extract Fooocus metadata from {image_path}: {e}")
            return None

    def _extract_from_text(self, text: str) -> Dict[str, Any]:
        """Extract parameters from Fooocus text format"""
        extracted: Dict[str, Any] = {}
        loras: List[Dict[str, Any]] = []

        # Fooocus format is usually like:
        # prompt text
        # Negative prompt: negative text
        # Steps: 20, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 12345, Size: 512x512, Model: model_name
        # LoRAs: lora1:0.8, lora2:1.0

        # Split into sections
        lines = text.strip().split("\n")

        # First part before "Negative prompt:" is the main prompt
        prompt_lines = []
        negative_section = False
        parameters_section = False

        for i, line in enumerate(lines):
            if line.startswith("Negative prompt:"):
                negative_section = True
                # Extract negative prompt
                neg_prompt = line.split("Negative prompt:", 1)[1].strip()
                if i + 1 < len(lines) and not self._is_parameter_line(lines[i + 1]):
                    # Multi-line negative prompt
                    j = i + 1
                    while j < len(lines) and not self._is_parameter_line(lines[j]):
                        neg_prompt += " " + lines[j].strip()
                        j += 1
                extracted["negative_prompt"] = neg_prompt
            elif line.startswith("LoRAs:") or line.startswith("Loras:"):
                # Extract LoRAs from text format
                loras_text = line.split(":", 1)[1].strip()
                self._parse_loras_from_text(loras_text, loras)
            elif self._is_parameter_line(line):
                parameters_section = True
                # Parse parameters (including potential LoRA info)
                self._parse_parameter_line(line, extracted, loras)
            elif not negative_section and not parameters_section:
                prompt_lines.append(line)

        # Set main prompt
        if prompt_lines:
            extracted["prompt"] = "\n".join(prompt_lines).strip()

        # Add LoRAs if found
        if loras:
            extracted["loras"] = loras

        return extracted

    def _is_parameter_line(self, line: str) -> bool:
        """Check if line contains parameters"""
        return bool(
            re.search(r"(Steps|Sampler|CFG scale|Seed|Size|Model|LoRAs?):", line)
        )

    def _parse_parameter_line(
        self,
        line: str,
        extracted: Dict[str, Any],
        loras: Optional[List[Dict[str, Any]]] = None,
    ):
        """Parse parameter line with format: Key: value, Key: value, ..."""
        if loras is None:
            loras = []

        # Split by commas but be careful with model names that might contain commas
        parts = re.split(
            r",(?=\s*(?:Steps|Sampler|CFG scale|Seed|Size|Model|Width|Height|LoRAs?):)",
            line,
        )

        for part in parts:
            part = part.strip()
            if ":" not in part:
                continue

            key, value = part.split(":", 1)
            key = key.strip()
            value = value.strip()

            if key == "Steps":
                extracted["steps"] = self._safe_int(value)
            elif key == "Sampler":
                extracted["sampler"] = value
            elif key == "CFG scale":
                extracted["cfg_scale"] = self._safe_float(value)
            elif key == "Seed":
                extracted["seed"] = self._safe_int(value)
            elif key == "Model":
                extracted["model"] = value
            elif key in ["LoRAs", "LoRA", "Loras"]:
                # Parse LoRA information from the value
                self._parse_loras_from_text(value, loras)
            elif key == "Size":
                # Parse size like "512x768"
                if "x" in value:
                    try:
                        width, height = value.split("x")
                        extracted["width"] = self._safe_int(width)
                        extracted["height"] = self._safe_int(height)
                    except ValueError:
                        pass

    def _parse_loras_from_text(self, loras_text: str, loras: List[Dict[str, Any]]):
        """Parse LoRA list from text format"""
        # Handle different text formats:
        # Format 1: "lora1:0.8, lora2:1.0"
        # Format 2: "lora1 (0.8), lora2 (1.0)"
        # Format 3: "lora1, lora2" (assume weight 1.0)

        # Split by commas first
        lora_entries = [entry.strip() for entry in loras_text.split(",")]

        for entry in lora_entries:
            if not entry:
                continue

            # Try format with parentheses: "lora_name (weight)"
            paren_match = re.match(r"^(.+?)\s*\(([0-9.]+)\)$", entry)
            if paren_match:
                lora_name = paren_match.group(1).strip()
                lora_weight = self._safe_float(paren_match.group(2)) or 1.0
            # Try format with colon: "lora_name:weight"
            elif ":" in entry:
                parts = entry.split(":", 1)
                lora_name = parts[0].strip()
                lora_weight = self._safe_float(parts[1].strip()) or 1.0
            # Just name, assume weight 1.0
            else:
                lora_name = entry.strip()
                lora_weight = 1.0

            if lora_name:
                # Clean the name
                lora_name_clean = lora_name.replace(".safetensors", "").replace(
                    ".ckpt", ""
                )
                # Remove path if present
                if "/" in lora_name_clean:
                    lora_name_clean = lora_name_clean.split("/")[-1]
                if "\\" in lora_name_clean:
                    lora_name_clean = lora_name_clean.split("\\")[-1]

                loras.append({"lora_name": lora_name_clean, "lora_weight": lora_weight})

    def _extract_from_json(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parameters from Fooocus JSON format"""
        extracted: Dict[str, Any] = {}

        # Direct field mappings
        if "prompt" in data:
            extracted["prompt"] = data["prompt"]

        if "negative_prompt" in data:
            extracted["negative_prompt"] = data["negative_prompt"]

        if "base_model" in data:
            extracted["model"] = data["base_model"]

        if "steps" in data:
            extracted["steps"] = self._safe_int(data["steps"])

        if "guidance_scale" in data:
            extracted["cfg_scale"] = self._safe_float(data["guidance_scale"])

        if "seed" in data:
            extracted["seed"] = self._safe_int(data["seed"])

        if "sampler" in data:
            extracted["sampler"] = data["sampler"]

        if "scheduler" in data:
            extracted["scheduler"] = data["scheduler"]

        # Parse resolution
        if "resolution" in data:
            res = data["resolution"]
            if isinstance(res, str):
                # Format like "(1152, 896)"
                res = res.strip("()")
                if "," in res:
                    try:
                        width, height = res.split(",")
                        extracted["width"] = self._safe_int(width.strip())
                        extracted["height"] = self._safe_int(height.strip())
                    except ValueError:
                        pass

        # Additional Fooocus-specific fields
        if "version" in data:
            extracted["version"] = data["version"]

        if "styles" in data:
            extracted["styles"] = data["styles"]

        # Extract LoRAs from Fooocus JSON format
        loras: List[Dict[str, Any]] = []

        # Fooocus can store LoRAs in various ways:
        # 1. As "loras" array with objects containing "name" and "weight"
        if "loras" in data:
            loras_data = data["loras"]
            if isinstance(loras_data, list):
                for lora_item in loras_data:
                    if isinstance(lora_item, dict):
                        lora_name = lora_item.get("name") or lora_item.get(
                            "lora_name", ""
                        )
                        lora_weight = (
                            lora_item.get("weight")
                            or lora_item.get("strength")
                            or lora_item.get("model_strength", 1.0)
                        )

                        if lora_name:
                            # Remove common extensions and paths
                            lora_name_clean = lora_name.replace(
                                ".safetensors", ""
                            ).replace(".ckpt", "")
                            # Remove path if present
                            if "/" in lora_name_clean:
                                lora_name_clean = lora_name_clean.split("/")[-1]
                            if "\\" in lora_name_clean:
                                lora_name_clean = lora_name_clean.split("\\")[-1]

                            loras.append(
                                {
                                    "lora_name": lora_name_clean,
                                    "lora_weight": self._safe_float(lora_weight) or 1.0,
                                }
                            )

        # 2. Check for individual lora fields
        lora_keys = [
            k
            for k in data.keys()
            if k.startswith("lora") and not k.endswith(("_weight", "_strength"))
        ]
        for lora_key in lora_keys:
            lora_name = data.get(lora_key)
            if lora_name and isinstance(lora_name, str):
                # Look for corresponding weight
                for weight_suffix in ["_weight", "_strength", "_model_strength"]:
                    weight_key = f"{lora_key}{weight_suffix}"
                    if weight_key in data:
                        lora_weight = data[weight_key]
                        break
                else:
                    lora_weight = 1.0

                # Clean the name
                lora_name_clean = lora_name.replace(".safetensors", "").replace(
                    ".ckpt", ""
                )
                if "/" in lora_name_clean:
                    lora_name_clean = lora_name_clean.split("/")[-1]
                if "\\" in lora_name_clean:
                    lora_name_clean = lora_name_clean.split("\\")[-1]

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
