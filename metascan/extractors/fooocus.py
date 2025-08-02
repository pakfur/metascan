import json
from pathlib import Path
from typing import Dict, Any, Optional
import logging
import re

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class FooocusExtractor(MetadataExtractor):
    """Extract metadata from Fooocus generated images"""
    
    def can_extract(self, image_path: Path) -> bool:
        """Check if image contains Fooocus metadata"""
        metadata = self._get_png_metadata(image_path)
        
        # Fooocus uses "Comment" or "Description" fields
        for field in ["Comment", "Description", "comment", "description"]:
            if field in metadata:
                text = metadata[field]
                # Check for Fooocus-specific patterns
                if "Fooocus" in text or "Steps:" in text and "Sampler:" in text:
                    return True
        
        return False
    
    def extract(self, image_path: Path) -> Optional[Dict[str, Any]]:
        """Extract Fooocus metadata"""
        try:
            metadata = self._get_png_metadata(image_path)
            
            result = {
                "source": "Fooocus",
                "raw_metadata": {}
            }
            
            # Find the metadata field
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
        extracted = {}
        
        # Fooocus format is usually like:
        # prompt text
        # Negative prompt: negative text
        # Steps: 20, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 12345, Size: 512x512, Model: model_name
        
        # Split into sections
        lines = text.strip().split('\n')
        
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
            elif self._is_parameter_line(line):
                parameters_section = True
                # Parse parameters
                self._parse_parameter_line(line, extracted)
            elif not negative_section and not parameters_section:
                prompt_lines.append(line)
        
        # Set main prompt
        if prompt_lines:
            extracted["prompt"] = '\n'.join(prompt_lines).strip()
        
        return extracted
    
    def _is_parameter_line(self, line: str) -> bool:
        """Check if line contains parameters"""
        return bool(re.search(r'(Steps|Sampler|CFG scale|Seed|Size|Model):', line))
    
    def _parse_parameter_line(self, line: str, extracted: Dict[str, Any]):
        """Parse parameter line with format: Key: value, Key: value, ..."""
        # Split by commas but be careful with model names that might contain commas
        parts = re.split(r',(?=\s*(?:Steps|Sampler|CFG scale|Seed|Size|Model|Width|Height):)', line)
        
        for part in parts:
            part = part.strip()
            if ':' not in part:
                continue
                
            key, value = part.split(':', 1)
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
            elif key == "Size":
                # Parse size like "512x768"
                if 'x' in value:
                    try:
                        width, height = value.split('x')
                        extracted["width"] = self._safe_int(width)
                        extracted["height"] = self._safe_int(height)
                    except ValueError:
                        pass