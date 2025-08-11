import json
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class SwarmUIExtractor(MetadataExtractor):
    """Extract metadata from SwarmUI generated images"""

    def can_extract(self, media_path: Path) -> bool:
        if media_path.suffix.lower() == ".mp4":
            return False

        metadata = self._get_exif_metadata(media_path)

        if "sui_image_params" in metadata or "parameters" in metadata:
            return True

        if "UserComment" in metadata:
            try:
                comment = metadata["UserComment"]
                if isinstance(comment, str) and "sui_image_params" in comment:
                    return True
            except:
                pass

        return False

    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        try:
            metadata = self._get_exif_metadata(image_path)

            result: Dict[str, Any] = {"source": "SwarmUI", "raw_metadata": {}}

            if "sui_image_params" in metadata:
                try:
                    params = json.loads(metadata["sui_image_params"])
                    result["raw_metadata"]["sui_image_params"] = params

                    extracted = self._extract_from_sui_params(params)
                    result.update(extracted)
                except json.JSONDecodeError as e:
                    logger.debug(
                        f"JSON parse error in sui_image_params for {image_path}: {e}"
                    )
                    repaired_data = self._repair_incomplete_json(
                        metadata["sui_image_params"]
                    )
                    if repaired_data:
                        result["raw_metadata"]["sui_image_params"] = repaired_data
                        extracted = self._extract_from_sui_params(repaired_data)
                        result.update(extracted)
                        logger.debug(
                            f"Successfully recovered data from truncated sui_image_params in {image_path}"
                        )
                    else:
                        logger.warning(
                            f"Failed to parse or repair SwarmUI params JSON from {image_path}: {e}"
                        )

            elif "UserComment" in metadata:
                try:
                    comment = metadata["UserComment"]
                    if isinstance(comment, str) and "sui_image_params" in comment:
                        try:
                            json_data = json.loads(comment)
                            if "sui_image_params" in json_data:
                                params = json_data["sui_image_params"]
                                result["raw_metadata"]["sui_image_params"] = params
                                extracted = self._extract_from_sui_params(params)
                                result.update(extracted)
                        except json.JSONDecodeError as e:
                            # Try to repair incomplete JSON by extracting what we can
                            logger.debug(f"JSON parse error in {image_path}: {e}")
                            repaired_data = self._repair_incomplete_json(comment)
                            if repaired_data:
                                result["raw_metadata"][
                                    "sui_image_params"
                                ] = repaired_data
                                extracted = self._extract_from_sui_params(repaired_data)
                                result.update(extracted)
                            else:
                                logger.warning(
                                    f"Failed to parse SwarmUI data from UserComment in {image_path}: {e}"
                                )
                                json_parsing_errors: List[Dict[str, Any]] = result.get(
                                    "parsing_errors", []
                                )
                                json_parsing_errors.append(
                                    {
                                        "error_type": "JSONDecodeError",
                                        "error_message": str(e),
                                        "raw_data": comment[:500]
                                        if isinstance(comment, str)
                                        else str(comment)[:500],
                                    }
                                )
                                result["parsing_errors"] = json_parsing_errors
                except (KeyError, TypeError) as e:
                    logger.warning(
                        f"Unexpected error parsing SwarmUI data from {image_path}: {e}"
                    )
                    general_parsing_errors: List[Dict[str, Any]] = result.get("parsing_errors", [])
                    general_parsing_errors.append(
                        {
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "raw_data": str(metadata)[:500] if metadata else "",
                        }
                    )
                    result["parsing_errors"] = general_parsing_errors

            elif "parameters" in metadata:
                params_text = metadata["parameters"]
                result["raw_metadata"]["parameters"] = params_text

                # Parse text format
                extracted = self._extract_from_text_params(params_text)
                result.update(extracted)

            if result["raw_metadata"] or result.get("parsing_errors"):
                return result
            return None

        except Exception as e:
            logger.error(f"Failed to extract SwarmUI metadata from {image_path}: {e}")
            return None

    def _extract_from_sui_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}

        extracted["prompt"] = params.get("prompt")
        extracted["negative_prompt"] = params.get("negativeprompt") or params.get(
            "negative_prompt"
        )
        extracted["model"] = params.get("model")
        extracted["steps"] = self._safe_int(params.get("steps"))
        extracted["cfg_scale"] = self._safe_float(
            params.get("cfgscale") or params.get("cfg_scale")
        )
        extracted["seed"] = self._safe_int(params.get("seed"))
        extracted["sampler"] = params.get("sampler")

        loras: List[Dict[str, Any]] = []

        if "loras" in params:
            loras_data = params["loras"]
            if isinstance(loras_data, list):
                # Check if it's a list of objects (old format)
                if loras_data and isinstance(loras_data[0], dict):
                    for lora_item in loras_data:
                        lora_name = lora_item.get("name") or lora_item.get(
                            "lora_name", ""
                        )
                        lora_weight = lora_item.get("weight") or lora_item.get(
                            "strength", 1.0
                        )

                        if lora_name:
                            # Remove common extensions
                            lora_name_clean = lora_name.replace(
                                ".safetensors", ""
                            ).replace(".ckpt", "")
                            loras.append(
                                {
                                    "lora_name": lora_name_clean,
                                    "lora_weight": self._safe_float(lora_weight) or 1.0,
                                }
                            )
                # Check if it's a list of strings (SwarmUI format with separate weights)
                elif loras_data and isinstance(loras_data[0], str):
                    lora_weights = params.get("loraweights", [])
                    for i, lora_name in enumerate(loras_data):
                        if lora_name and isinstance(lora_name, str):
                            # Get corresponding weight, default to 1.0
                            lora_weight = 1.0
                            if i < len(lora_weights):
                                lora_weight = self._safe_float(lora_weights[i]) or 1.0

                            # Remove common extensions
                            lora_name_clean = lora_name.replace(
                                ".safetensors", ""
                            ).replace(".ckpt", "")
                            loras.append(
                                {
                                    "lora_name": lora_name_clean,
                                    "lora_weight": lora_weight,
                                }
                            )

        lora_keys = [
            k
            for k in params.keys()
            if k.startswith("lora") and not k.endswith("_weight")
        ]
        for lora_key in lora_keys:
            lora_name = params.get(lora_key)
            if lora_name and isinstance(lora_name, str):
                # Look for corresponding weight
                weight_key = f"{lora_key}_weight"
                lora_weight = params.get(weight_key, 1.0)

                # Remove common extensions
                lora_name_clean = lora_name.replace(".safetensors", "").replace(
                    ".ckpt", ""
                )
                loras.append(
                    {
                        "lora_name": lora_name_clean,
                        "lora_weight": self._safe_float(lora_weight) or 1.0,
                    }
                )

        if loras:
            extracted["loras"] = loras

        if "width" in params:
            extracted["width"] = self._safe_int(params["width"])
        if "height" in params:
            extracted["height"] = self._safe_int(params["height"])

        return {k: v for k, v in extracted.items() if v is not None}

    def _extract_from_text_params(self, params_text: str) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        loras: List[Dict[str, Any]] = []

        lines = params_text.strip().split("\n")

        current_key = None
        current_value = []

        for line in lines:
            if ":" in line and not line.startswith(" "):
                if current_key and current_value:
                    value = "\n".join(current_value).strip()
                    self._parse_parameter(current_key, value, extracted, loras)

                parts = line.split(":", 1)
                current_key = parts[0].strip().lower()
                current_value = [parts[1].strip()] if len(parts) > 1 else []
            else:
                # Continuation of previous value
                current_value.append(line.strip())

        if current_key and current_value:
            value = "\n".join(current_value).strip()
            self._parse_parameter(current_key, value, extracted, loras)

        if loras:
            extracted["loras"] = loras

        return extracted

    def _parse_parameter(
        self, key: str, value: str, extracted: Dict[str, Any], loras: Optional[List[Dict[str, Any]]] = None
    ):
        if loras is None:
            loras = []

        key = key.replace(" ", "_").replace("-", "_")

        if key in ["prompt", "positive_prompt"]:
            extracted["prompt"] = value
        elif key in ["negative_prompt", "negative"]:
            extracted["negative_prompt"] = value
        elif key == "model":
            extracted["model"] = value
        elif key == "steps":
            extracted["steps"] = self._safe_int(value)
        elif key in ["cfg_scale", "cfg", "guidance_scale"]:
            extracted["cfg_scale"] = self._safe_float(value)
        elif key == "seed":
            extracted["seed"] = self._safe_int(value)
        elif key == "sampler":
            extracted["sampler"] = value
        elif key in ["loras", "lora", "lora_list"]:
            # Parse LoRA list from text format
            # Format could be: "lora1:0.8, lora2:1.0" or "lora1 (0.8), lora2 (1.0)"
            self._parse_loras_from_text(value, loras)
        elif key.startswith("lora") and key[4:].isdigit():
            # Individual LoRA entries like "lora1", "lora2"
            # Look for weight in value or assume 1.0
            if ":" in value:
                parts = value.split(":", 1)
                lora_name = parts[0].strip()
                lora_weight = self._safe_float(parts[1].strip()) or 1.0
            else:
                lora_name = value.strip()
                lora_weight = 1.0

            if lora_name:
                lora_name_clean = lora_name.replace(".safetensors", "").replace(
                    ".ckpt", ""
                )
                loras.append({"lora_name": lora_name_clean, "lora_weight": lora_weight})

    def _parse_loras_from_text(self, loras_text: str, loras: List[Dict[str, Any]]):
        import re

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
                lora_name_clean = lora_name.replace(".safetensors", "").replace(
                    ".ckpt", ""
                )
                loras.append({"lora_name": lora_name_clean, "lora_weight": lora_weight})

    def _repair_incomplete_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        try:
            import re

            cleaned_json = re.sub(r'(["\]}])\s*\.\s*', r"\1,", json_str)
            cleaned_json = re.sub(r'(["\]}])\s*\.$', r"\1", cleaned_json)

            try:
                import json
                from typing import cast

                return cast(Dict[str, Any], json.loads(cleaned_json))
            except json.JSONDecodeError:
                pass

            prompt_match = None

            prompt_match = re.search(r'"prompt"\s*:\s*"([^"]*)"', json_str)

            if not prompt_match:
                truncated_prompt_match = re.search(
                    r'"prompt"\s*:\s*"([^"]*?)(?:$|[^",]*$)', json_str, re.DOTALL
                )
                if truncated_prompt_match:
                    prompt_text = truncated_prompt_match.group(1)
                    prompt_text = re.sub(r"\s+\w*$", "", prompt_text).strip()
                    if (
                        len(prompt_text) > 10
                    ):  # Only use if we have meaningful content (more than 10 chars)
                        prompt_match = truncated_prompt_match

            negative_match = re.search(
                r'"(?:negative_?prompt|negativeprompt)"\s*:\s*"([^"]*)"', json_str
            )
            if not negative_match:
                negative_match = re.search(
                    r'"(?:negative_?prompt|negativeprompt)"\s*:\s*"([^"]*?)(?:[^"]*)?(?:",|$)',
                    json_str,
                    re.DOTALL,
                )

            model_match = re.search(r'"model"\s*:\s*"([^"]*)"', json_str)
            if not model_match:
                model_match = re.search(
                    r'"model"\s*:\s*"([^"]*?)(?:[^"]*)?(?:",|$)', json_str, re.DOTALL
                )

            steps_match = re.search(r'"steps"\s*:\s*(\d+)', json_str)
            cfg_match = re.search(r'"(?:cfg_?scale|cfgscale)"\s*:\s*([\d.]+)', json_str)
            seed_match = re.search(r'"seed"\s*:\s*(\d+)', json_str)

            sampler_match = re.search(r'"sampler"\s*:\s*"([^"]*)"', json_str)
            if not sampler_match:
                sampler_match = re.search(
                    r'"sampler"\s*:\s*"([^"]*?)(?:[^"]*)?(?:",|$)', json_str, re.DOTALL
                )

            width_match = re.search(r'"width"\s*:\s*(\d+)', json_str)
            height_match = re.search(r'"height"\s*:\s*(\d+)', json_str)

            loras_match = re.search(r'"loras"\s*:\s*\[([^\]]*)\]', json_str, re.DOTALL)
            if not loras_match:
                # Try to find partial LoRA array
                loras_match = re.search(
                    r'"loras"\s*:\s*\[([^"]*?)(?:\]|$)', json_str, re.DOTALL
                )

            repaired: Dict[str, Any] = {}

            if prompt_match:
                prompt_text = prompt_match.group(1)
                prompt_text = re.sub(r"\s+\w*$", "", prompt_text).strip()
                prompt_text = re.sub(
                    r"\w*$",
                    lambda m: ""
                    if len(m.group()) < 4
                    and not m.group().endswith((".", ",", "!", "?"))
                    else m.group(),
                    prompt_text,
                ).strip()
                if len(prompt_text) > 5:  # Only use if we have meaningful content
                    repaired["prompt"] = prompt_text

            if negative_match:
                negative_text = negative_match.group(1)
                negative_text = re.sub(r"\s+\S*$", "", negative_text).strip()
                if negative_text:
                    repaired["negativeprompt"] = negative_text

            if model_match:
                model_text = model_match.group(1)
                model_text = re.sub(r"\s+\S*$", "", model_text).strip()
                if model_text:
                    repaired["model"] = model_text

            if steps_match:
                try:
                    repaired["steps"] = int(steps_match.group(1))
                except ValueError:
                    pass

            if cfg_match:
                try:
                    repaired["cfgscale"] = float(cfg_match.group(1))
                except ValueError:
                    pass

            if seed_match:
                try:
                    repaired["seed"] = int(seed_match.group(1))
                except ValueError:
                    pass

            if sampler_match:
                sampler_text = sampler_match.group(1)
                sampler_text = re.sub(r"\s+\S*$", "", sampler_text).strip()
                if sampler_text:
                    repaired["sampler"] = sampler_text

            if width_match:
                try:
                    repaired["width"] = int(width_match.group(1))
                except ValueError:
                    pass

            if height_match:
                try:
                    repaired["height"] = int(height_match.group(1))
                except ValueError:
                    pass

            if loras_match:
                loras_content = loras_match.group(1)
                lora_objects = re.findall(
                    r'\{[^}]*"name"\s*:\s*"([^"]*)"[^}]*"weight"\s*:\s*([0-9.]+)[^}]*\}',
                    loras_content,
                )
                if lora_objects:
                    loras_list = []
                    for lora_name, lora_weight in lora_objects:
                        try:
                            loras_list.append(
                                {"name": lora_name, "weight": float(lora_weight)}
                            )
                        except ValueError:
                            pass
                    if loras_list:
                        repaired["loras"] = loras_list
                else:
                    # Try to extract SwarmUI format: array of strings with separate weights
                    lora_strings = re.findall(r'"([^"]+)"', loras_content)
                    if lora_strings:
                        repaired["loras"] = lora_strings

            weights_match = re.search(
                r'"loraweights"\s*:\s*\[([^\]]*)\]', json_str, re.DOTALL
            )
            if weights_match:
                weights_content = weights_match.group(1)
                # Extract weight strings/numbers, filtering out formatting characters
                weight_values = re.findall(r'"([^"]+)"', weights_content)
                if not weight_values:
                    # Fallback to numeric values without quotes
                    weight_values = re.findall(r"(-?[\d.]+)", weights_content)
                if weight_values:
                    # Filter out empty/whitespace values and format characters
                    weight_values = [
                        w.strip()
                        for w in weight_values
                        if w.strip() and w.strip() not in [".", ",", "]", "["]
                    ]
                    if weight_values:
                        repaired["loraweights"] = weight_values

            if repaired:
                logger.debug(
                    f"Successfully repaired truncated JSON, extracted {len(repaired)} fields"
                )

            return repaired if repaired else None

        except Exception as e:
            logger.debug(f"Failed to repair incomplete JSON: {e}")
            return None
