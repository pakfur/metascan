import json
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class SwarmUIExtractor(MetadataExtractor):
    """Extract metadata from SwarmUI generated images"""
    
    def can_extract(self, image_path: Path) -> bool:
        """Check if image contains SwarmUI metadata"""
        metadata = self._get_png_metadata(image_path)
        return "sui_image_params" in metadata or "parameters" in metadata
    
    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract SwarmUI metadata"""
        try:
            metadata = self._get_png_metadata(image_path)
            
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