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
        
        # Additional SwarmUI specific parameters
        if "width" in params:
            extracted["width"] = self._safe_int(params["width"])
        if "height" in params:
            extracted["height"] = self._safe_int(params["height"])
        
        return {k: v for k, v in extracted.items() if v is not None}
    
    def _extract_from_text_params(self, params_text: str) -> Dict[str, Any]:
        """Extract from text-based parameter format"""
        extracted = {}
        
        # Split by newlines and parse key-value pairs
        lines = params_text.strip().split('\n')
        
        current_key = None
        current_value = []
        
        for line in lines:
            if ':' in line and not line.startswith(' '):
                # Save previous key-value if exists
                if current_key and current_value:
                    value = '\n'.join(current_value).strip()
                    self._parse_parameter(current_key, value, extracted)
                
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
            self._parse_parameter(current_key, value, extracted)
        
        return extracted
    
    def _parse_parameter(self, key: str, value: str, extracted: Dict[str, Any]):
        """Parse individual parameter from text format"""
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
            
            return repaired if repaired else None
            
        except Exception as e:
            logger.debug(f"Failed to repair incomplete JSON: {e}")
            return None