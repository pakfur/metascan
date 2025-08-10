import json
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class SwarmUIExtractor(MetadataExtractor):
    """Extract metadata from SwarmUI generated images"""
    
    def can_extract(self, media_path: Path) -> bool:
        """Check if image contains SwarmUI metadata"""
        # Skip video files - they're not supported by SwarmUI image extractor
        if media_path.suffix.lower() == '.mp4':
            return False
            
        metadata = self._get_exif_metadata(media_path)
        
        # Check for SwarmUI metadata in various fields
        if "sui_image_params" in metadata or "parameters" in metadata:
            return True
            
        # Check UserComment field for SwarmUI data
        if "UserComment" in metadata:
            try:
                comment = metadata["UserComment"]
                if isinstance(comment, str) and "sui_image_params" in comment:
                    return True
            except:
                pass
                
        return False
    
    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract SwarmUI metadata"""
        try:
            metadata = self._get_exif_metadata(image_path)
            
            result = {
                "source": "SwarmUI",
                "raw_metadata": {}
            }
            
            # Try to extract from sui_image_params first
            if "sui_image_params" in metadata:
                try:
                    params = json.loads(metadata["sui_image_params"])
                    result["raw_metadata"]["sui_image_params"] = params
                    
                    # Extract parameters
                    extracted = self._extract_from_sui_params(params)
                    result.update(extracted)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SwarmUI params JSON from {image_path}")
            
            # Check UserComment field for SwarmUI data
            elif "UserComment" in metadata:
                try:
                    comment = metadata["UserComment"]
                    if isinstance(comment, str) and "sui_image_params" in comment:
                        # Try to parse JSON from UserComment
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
                                result["raw_metadata"]["sui_image_params"] = repaired_data
                                extracted = self._extract_from_sui_params(repaired_data)
                                result.update(extracted)
                            else:
                                logger.warning(f"Failed to parse SwarmUI data from UserComment in {image_path}: {e}")
                except (KeyError, TypeError) as e:
                    logger.warning(f"Unexpected error parsing SwarmUI data from {image_path}: {e}")
            
            # Fallback to parameters field
            elif "parameters" in metadata:
                params_text = metadata["parameters"]
                result["raw_metadata"]["parameters"] = params_text
                
                # Parse text format
                extracted = self._extract_from_text_params(params_text)
                result.update(extracted)
            
            return result if result["raw_metadata"] else None
            
        except Exception as e:
            logger.error(f"Failed to extract SwarmUI metadata from {image_path}: {e}")
            return None
    
    def _extract_from_sui_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Extract from SwarmUI JSON format"""
        extracted = {}
        
        # Direct mappings
        extracted["prompt"] = params.get("prompt")
        extracted["negative_prompt"] = params.get("negativeprompt") or params.get("negative_prompt")
        extracted["model"] = params.get("model")
        extracted["steps"] = self._safe_int(params.get("steps"))
        extracted["cfg_scale"] = self._safe_float(params.get("cfgscale") or params.get("cfg_scale"))
        extracted["seed"] = self._safe_int(params.get("seed"))
        extracted["sampler"] = params.get("sampler")
        
        # Extract LoRAs from SwarmUI format
        loras = []
        
        # SwarmUI can store LoRAs in different ways:
        # 1. As a "loras" list with objects containing "name" and "weight"
        if "loras" in params:
            loras_data = params["loras"]
            if isinstance(loras_data, list):
                for lora_item in loras_data:
                    if isinstance(lora_item, dict):
                        lora_name = lora_item.get("name") or lora_item.get("lora_name", "")
                        lora_weight = lora_item.get("weight") or lora_item.get("strength", 1.0)
                        
                        if lora_name:
                            # Remove common extensions
                            lora_name_clean = lora_name.replace(".safetensors", "").replace(".ckpt", "")
                            loras.append({
                                "lora_name": lora_name_clean,
                                "lora_weight": self._safe_float(lora_weight) or 1.0
                            })
        
        # 2. As individual parameters like "lora1", "lora2", etc.
        lora_keys = [k for k in params.keys() if k.startswith("lora") and not k.endswith("_weight")]
        for lora_key in lora_keys:
            lora_name = params.get(lora_key)
            if lora_name and isinstance(lora_name, str):
                # Look for corresponding weight
                weight_key = f"{lora_key}_weight"
                lora_weight = params.get(weight_key, 1.0)
                
                # Remove common extensions
                lora_name_clean = lora_name.replace(".safetensors", "").replace(".ckpt", "")
                loras.append({
                    "lora_name": lora_name_clean,
                    "lora_weight": self._safe_float(lora_weight) or 1.0
                })
        
        # Add LoRAs to extracted data
        if loras:
            extracted["loras"] = loras
        
        # Additional SwarmUI specific parameters
        if "width" in params:
            extracted["width"] = self._safe_int(params["width"])
        if "height" in params:
            extracted["height"] = self._safe_int(params["height"])
        
        return {k: v for k, v in extracted.items() if v is not None}
    
    def _extract_from_text_params(self, params_text: str) -> Dict[str, Any]:
        """Extract from text-based parameter format"""
        extracted = {}
        loras = []
        
        # Split by newlines and parse key-value pairs
        lines = params_text.strip().split('\n')
        
        current_key = None
        current_value = []
        
        for line in lines:
            if ':' in line and not line.startswith(' '):
                # Save previous key-value if exists
                if current_key and current_value:
                    value = '\n'.join(current_value).strip()
                    self._parse_parameter(current_key, value, extracted, loras)
                
                # Start new key-value
                parts = line.split(':', 1)
                current_key = parts[0].strip().lower()
                current_value = [parts[1].strip()] if len(parts) > 1 else []
            else:
                # Continuation of previous value
                current_value.append(line.strip())
        
        # Don't forget last parameter
        if current_key and current_value:
            value = '\n'.join(current_value).strip()
            self._parse_parameter(current_key, value, extracted, loras)
        
        # Add LoRAs if found
        if loras:
            extracted["loras"] = loras
        
        return extracted
    
    def _parse_parameter(self, key: str, value: str, extracted: Dict[str, Any], loras: list = None):
        """Parse individual parameter from text format"""
        if loras is None:
            loras = []
            
        # Normalize key
        key = key.replace(' ', '_').replace('-', '_')
        
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
                lora_name_clean = lora_name.replace(".safetensors", "").replace(".ckpt", "")
                loras.append({
                    "lora_name": lora_name_clean,
                    "lora_weight": lora_weight
                })
    
    def _parse_loras_from_text(self, loras_text: str, loras: list):
        """Parse LoRA list from text format"""
        import re
        
        # Handle different text formats:
        # Format 1: "lora1:0.8, lora2:1.0"
        # Format 2: "lora1 (0.8), lora2 (1.0)"
        # Format 3: "lora1, lora2" (assume weight 1.0)
        
        # Split by commas first
        lora_entries = [entry.strip() for entry in loras_text.split(',')]
        
        for entry in lora_entries:
            if not entry:
                continue
                
            # Try format with parentheses: "lora_name (weight)"
            paren_match = re.match(r'^(.+?)\s*\(([0-9.]+)\)$', entry)
            if paren_match:
                lora_name = paren_match.group(1).strip()
                lora_weight = self._safe_float(paren_match.group(2)) or 1.0
            # Try format with colon: "lora_name:weight"
            elif ':' in entry:
                parts = entry.split(':', 1)
                lora_name = parts[0].strip()
                lora_weight = self._safe_float(parts[1].strip()) or 1.0
            # Just name, assume weight 1.0
            else:
                lora_name = entry.strip()
                lora_weight = 1.0
            
            if lora_name:
                lora_name_clean = lora_name.replace(".safetensors", "").replace(".ckpt", "")
                loras.append({
                    "lora_name": lora_name_clean,
                    "lora_weight": lora_weight
                })
    
    def _repair_incomplete_json(self, json_str: str) -> Optional[Dict[str, Any]]:
        """Attempt to repair incomplete/truncated JSON by extracting available data"""
        try:
            # Try to extract just the prompt if the JSON is incomplete
            import re
            
            # Look for prompt field in the JSON
            prompt_match = re.search(r'"prompt"\s*:\s*"([^"]*)"', json_str)
            negative_match = re.search(r'"(?:negative_?prompt|negativeprompt)"\s*:\s*"([^"]*)"', json_str)
            model_match = re.search(r'"model"\s*:\s*"([^"]*)"', json_str)
            steps_match = re.search(r'"steps"\s*:\s*(\d+)', json_str)
            cfg_match = re.search(r'"(?:cfg_?scale|cfgscale)"\s*:\s*([\d.]+)', json_str)
            seed_match = re.search(r'"seed"\s*:\s*(\d+)', json_str)
            sampler_match = re.search(r'"sampler"\s*:\s*"([^"]*)"', json_str)
            width_match = re.search(r'"width"\s*:\s*(\d+)', json_str)
            height_match = re.search(r'"height"\s*:\s*(\d+)', json_str)
            
            # Look for LoRA data
            loras_match = re.search(r'"loras"\s*:\s*\[([^\]]*)\]', json_str, re.DOTALL)
            
            # Build a repaired params dict with what we found
            repaired = {}
            if prompt_match:
                repaired["prompt"] = prompt_match.group(1)
            if negative_match:
                repaired["negativeprompt"] = negative_match.group(1)
            if model_match:
                repaired["model"] = model_match.group(1)
            if steps_match:
                repaired["steps"] = int(steps_match.group(1))
            if cfg_match:
                repaired["cfgscale"] = float(cfg_match.group(1))
            if seed_match:
                repaired["seed"] = int(seed_match.group(1))
            if sampler_match:
                repaired["sampler"] = sampler_match.group(1)
            if width_match:
                repaired["width"] = int(width_match.group(1))
            if height_match:
                repaired["height"] = int(height_match.group(1))
            
            # Try to extract LoRA data
            if loras_match:
                loras_content = loras_match.group(1)
                # Try to extract individual LoRA objects
                lora_objects = re.findall(r'\{[^}]*"name"\s*:\s*"([^"]*)"[^}]*"weight"\s*:\s*([0-9.]+)[^}]*\}', loras_content)
                if lora_objects:
                    loras = []
                    for lora_name, lora_weight in lora_objects:
                        loras.append({
                            "name": lora_name,
                            "weight": float(lora_weight)
                        })
                    repaired["loras"] = loras
            
            return repaired if repaired else None
            
        except Exception as e:
            logger.debug(f"Failed to repair incomplete JSON: {e}")
            return None