import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class ComfyUIVideoExtractor(MetadataExtractor):
    """Extract metadata from ComfyUI generated MP4 videos"""
    
    def can_extract(self, media_path: Path) -> bool:
        """Check if MP4 contains ComfyUI metadata"""
        if media_path.suffix.lower() != '.mp4':
            return False
            
        metadata = self._get_video_metadata(media_path)
        return "prompt" in metadata or "workflow" in metadata
    
    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        """Extract ComfyUI metadata from MP4"""
        try:
            metadata = self._get_video_metadata(media_path)
            
            result = {
                "source": "ComfyUI",
                "raw_metadata": {}
            }
            
            # Extract prompt data
            if "prompt" in metadata:
                try:
                    prompt_data = metadata["prompt"]
                    # If it's already a dict, use it directly; if string, parse it
                    if isinstance(prompt_data, str):
                        prompt_data = json.loads(prompt_data)
                    result["raw_metadata"]["prompt"] = prompt_data
                    
                    # Extract common parameters
                    extracted = self._extract_parameters(prompt_data)
                    result.update(extracted)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse ComfyUI prompt data from {media_path}: {e}")
            
            # Extract workflow if present
            if "workflow" in metadata:
                try:
                    workflow_data = metadata["workflow"]
                    # If it's already a dict, use it directly; if string, parse it
                    if isinstance(workflow_data, str):
                        workflow_data = json.loads(workflow_data)
                    result["raw_metadata"]["workflow"] = workflow_data
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse ComfyUI workflow data from {media_path}: {e}")
            
            return result if result["raw_metadata"] else None
            
        except Exception as e:
            logger.error(f"Failed to extract ComfyUI metadata from {media_path}: {e}")
            return None
    
    def _get_video_metadata(self, media_path: Path) -> Dict[str, Any]:
        """Extract metadata from MP4 video file"""
        try:
            # First try exiftool (faster and more reliable for MP4 metadata)
            result = subprocess.run(
                ['exiftool', '-Comment', '-json', str(media_path)],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data and len(data) > 0:
                    comment = data[0].get('Comment', '')
                    if comment:
                        try:
                            # ComfyUI stores metadata as JSON in the comment field
                            comment_data = json.loads(comment)
                            return comment_data
                        except json.JSONDecodeError:
                            # If it's not JSON, return as-is
                            return {"comment": comment}
            
            # Fallback to ffprobe if exiftool fails
            return self._get_video_metadata_ffprobe(media_path)
            
        except Exception as e:
            logger.error(f"Failed to extract video metadata from {media_path}: {e}")
            return {}
    
    def _get_video_metadata_ffprobe(self, media_path: Path) -> Dict[str, Any]:
        """Fallback method using ffprobe"""
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_format', str(media_path)
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                format_data = data.get('format', {})
                tags = format_data.get('tags', {})
                
                # Look for ComfyUI metadata in various tag fields
                for key, value in tags.items():
                    if key.lower() in ['comment', 'description', 'title']:
                        try:
                            metadata = json.loads(value)
                            if isinstance(metadata, dict) and ('prompt' in metadata or 'workflow' in metadata):
                                return metadata
                        except json.JSONDecodeError:
                            continue
                
                return tags
            
            return {}
            
        except Exception as e:
            logger.error(f"ffprobe fallback failed for {media_path}: {e}")
            return {}
    
    def _extract_parameters(self, prompt_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract common parameters from ComfyUI prompt data"""
        extracted = {}
        
        # Look for various node types
        for node_id, node_data in prompt_data.items():
            if not isinstance(node_data, dict):
                continue
                
            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})
            
            # KSampler for basic generation parameters
            if class_type == "KSampler":
                extracted["sampler"] = inputs.get("sampler_name")
                extracted["steps"] = self._safe_int(inputs.get("steps"))
                extracted["cfg_scale"] = self._safe_float(inputs.get("cfg"))
                extracted["seed"] = self._safe_int(inputs.get("seed"))
                extracted["scheduler"] = inputs.get("scheduler")
            
            # Model loader
            elif class_type == "CheckpointLoaderSimple":
                extracted["model"] = inputs.get("ckpt_name")
            
            # Video-specific loaders
            elif class_type == "DiffusionModelLoaderKJ":
                extracted["model"] = inputs.get("model_name")
            
            # Text encoders for prompts
            elif class_type == "CLIPTextEncode":
                text = inputs.get("text", "")
                if text:
                    # Try to determine if positive or negative
                    if any(neg in text.lower() for neg in ["negative", "bad", "ugly", "worst", "overexposure"]):
                        if "negative_prompt" not in extracted:
                            extracted["negative_prompt"] = text
                    else:
                        if "prompt" not in extracted:
                            extracted["prompt"] = text
            
            # Video generation nodes
            elif class_type in ["WanImageToVideo", "VHS_VideoCombine"]:
                if "width" not in extracted:
                    extracted["width"] = self._safe_int(inputs.get("width"))
                if "height" not in extracted:
                    extracted["height"] = self._safe_int(inputs.get("height"))
                if "frame_rate" not in extracted:
                    extracted["frame_rate"] = self._safe_float(inputs.get("frame_rate"))
                if "length" not in extracted:
                    extracted["length"] = self._safe_int(inputs.get("length"))
        
        return extracted