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
            # Include both English and Chinese negative indicators
            negative_indicators = [
                "negative",
                "bad",
                "ugly",
                "worst",
                "low quality",
                "poor",
                "最差质量",
                "低质量",
                "丑陋",
                "残缺",
                "畸形",
                "毁容",  # Chinese negative terms
                "overexposure",
                "过曝",
                "静态",
                "模糊",  # Common negative terms
            ]

            # Count how many negative indicators are present
            negative_count = sum(
                1 for indicator in negative_indicators if indicator in text.lower()
            )

            # If text has multiple negative indicators, it's likely a negative prompt
            # Also check if text is mostly Chinese (common pattern for negative prompts)
            chinese_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
            total_chars = len(text)

            is_negative = negative_count >= 2 or (  # Multiple negative keywords
                chinese_chars > total_chars * 0.5 and negative_count >= 1
            )  # Mostly Chinese with negative keywords

            if is_negative:
                if "negative_prompt" not in result:
                    result["negative_prompt"] = text
            else:
                if "prompt" not in result:
                    result["prompt"] = text


class PowerLoraLoaderHandler(NodeHandler):
    """Handler for Power Lora Loader (rgthree) nodes"""

    def can_handle(self, class_type: str) -> bool:
        return class_type == "Power Lora Loader (rgthree)"

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Initialize loras list if needed
        if "loras" not in result:
            result["loras"] = []

        # Iterate through all lora_N inputs
        for key, value in inputs.items():
            if key.startswith("lora_") and isinstance(value, dict):
                # Check if this LoRA is enabled
                if not value.get("on", False):
                    continue

                # Extract LoRA name
                lora_name = value.get("lora", "")
                if not lora_name:
                    continue

                # Remove file extension
                lora_name = (
                    lora_name.replace(".safetensors", "")
                    .replace(".ckpt", "")
                    .replace(".pt", "")
                )

                # Extract strength
                strength = value.get("strength", 1.0)

                # Check if this LoRA is already in the list (avoid duplicates)
                existing_lora = next(
                    (
                        lora
                        for lora in result["loras"]
                        if lora["lora_name"] == lora_name
                    ),
                    None,
                )

                if not existing_lora:
                    weight = round(float(strength), 2)
                    result["loras"].append(
                        {"lora_name": lora_name, "lora_weight": weight}
                    )


class LoraLoaderHandler(NodeHandler):
    """Handler for standard LoRA loader nodes"""

    def can_handle(self, class_type: str) -> bool:
        # Don't handle Power Lora Loader here
        if "power" in class_type.lower():
            return False
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
    """Handler for sampler nodes (KSampler and variants).

    Also covers the split-sampler topology (SamplerCustomAdvanced), where
    the same widgets live on separate nodes: ``RandomNoise`` carries the
    seed, ``BasicScheduler`` the scheduler/steps/denoise and
    ``KSamplerSelect`` the sampler name.
    """

    def can_handle(self, class_type: str) -> bool:
        lowered = class_type.lower()
        return (
            "sampler" in lowered
            or "scheduler" in lowered
            or class_type == "RandomNoise"
        )

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # Extract sampler name (handle various formats)
        sampler_name = inputs.get("sampler_name", "")
        if isinstance(sampler_name, str) and sampler_name:
            # Simplify RES4LYF sampler names like "multistep/res_2m" to
            # "RES4LYF". ComfyUI's own res_multistep family is a core
            # sampler, not a RES4LYF one, so it keeps its real name.
            lowered = sampler_name.lower()
            if "res" in lowered and not lowered.startswith("res_multistep"):
                result["sampler"] = "RES4LYF"
            else:
                result["sampler"] = sampler_name

        # Extract scheduler
        scheduler = inputs.get("scheduler")
        if isinstance(scheduler, str) and scheduler and "scheduler" not in result:
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

        # Extract seed (KSamplerAdvanced / RandomNoise spell it noise_seed).
        # A list here is a link to another node, not a value.
        seed = inputs.get("seed")
        if seed is None:
            seed = inputs.get("noise_seed")
        if isinstance(seed, (int, float)) and "seed" not in result:
            result["seed"] = int(seed)

        # Extract denoise strength
        denoise = inputs.get("denoise")
        if denoise is not None and "denoise" not in result:
            result["denoise"] = float(denoise)


class VideoGeneratorHandler(NodeHandler):
    """Handler for video generation nodes"""

    def can_handle(self, class_type: str) -> bool:
        return class_type in [
            "WanImageToVideo",
            "VHS_VideoCombine",
            "AnimateDiff",
            "CreateVideo",
        ] or class_type.startswith("MiniMaxH3")

    def extract(
        self, node_id: str, node_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        inputs = node_data.get("inputs", {})

        # A list value is a link to another node (e.g. H3's ``length`` comes
        # from a seconds-to-frames math node), not a number to read.
        def number(*keys: str) -> Optional[float]:
            for key in keys:
                value = inputs.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
            return None

        # Extract dimensions
        width = number("width")
        if "width" not in result and width is not None:
            result["width"] = int(width)

        height = number("height")
        if "height" not in result and height is not None:
            result["height"] = int(height)

        # Extract frame rate
        frame_rate = number("frame_rate", "fps")
        if "frame_rate" not in result and frame_rate:
            result["frame_rate"] = frame_rate

        # Extract video length
        length = number("length", "num_frames")
        if "video_length" not in result and length:
            result["video_length"] = int(length)


class ComfyUIVideoExtractor(MetadataExtractor):
    """Improved ComfyUI video metadata extractor with node-specific handlers"""

    def __init__(self):
        super().__init__()
        # Initialize node handlers
        # Order matters: PowerLoraLoaderHandler before LoraLoaderHandler
        self.handlers: List[NodeHandler] = [
            CLIPTextEncodeHandler(),
            PowerLoraLoaderHandler(),
            LoraLoaderHandler(),
            UNETLoaderHandler(),
            SamplerHandler(),
            VideoGeneratorHandler(),
        ]

    def can_extract(self, media_path: Path) -> bool:
        if media_path.suffix.lower() not in {".mp4", ".webm", ".mov"}:
            return False

        metadata = self._get_video_metadata(media_path)
        return "prompt" in metadata or "workflow" in metadata

    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:  # noqa: C901
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
                    self._apply_ms_titles(prompt_data, result)
                    self._resolve_linked_prompt(prompt_data, result)

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

            if result["raw_metadata"]:
                self._fill_from_container(media_path, result)

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

    def _apply_ms_titles(
        self, prompt_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        """Read back the values metascan itself wrote into MS_* titled nodes.

        These are authoritative for clips metascan generated (see
        ``metascan/core/comfy_bindings.py`` for the title contract), so they
        override whatever the class-name heuristics guessed.
        """
        for node_data in prompt_data.values():
            if not isinstance(node_data, dict):
                continue
            title = (node_data.get("_meta") or {}).get("title")
            inputs = node_data.get("inputs") or {}

            if title in ("MS_POSITIVE", "MS_NEGATIVE"):
                text = inputs.get("text")
                if isinstance(text, str) and text.strip():
                    key = "prompt" if title == "MS_POSITIVE" else "negative_prompt"
                    result[key] = text.strip()
            elif title == "MS_SEED":
                seed = inputs.get("seed")
                if seed is None:
                    seed = inputs.get("noise_seed")
                if isinstance(seed, (int, float)):
                    result["seed"] = int(seed)
            elif title == "MS_STEPS":
                steps = inputs.get("steps")
                if isinstance(steps, (int, float)):
                    result["steps"] = int(steps)

    def _resolve_linked_prompt(
        self, prompt_data: Dict[str, Any], result: Dict[str, Any]
    ) -> None:
        """Find the prompt on video nodes that take it as a plain string.

        Nodes like MiniMaxH3ImageToVideo have no CLIPTextEncode upstream:
        their ``prompt`` input is the text itself or a link to a string node.
        """
        if "prompt" in result:
            return

        generator = VideoGeneratorHandler()
        for node_data in prompt_data.values():
            if not isinstance(node_data, dict):
                continue
            if not generator.can_handle(node_data.get("class_type", "")):
                continue

            value = (node_data.get("inputs") or {}).get("prompt")
            if isinstance(value, list) and value:
                source = prompt_data.get(str(value[0]))
                source_inputs = (
                    source.get("inputs") or {} if isinstance(source, dict) else {}
                )
                value = next(
                    (
                        source_inputs[key]
                        for key in ("text", "string", "value")
                        if isinstance(source_inputs.get(key), str)
                    ),
                    None,
                )
            if isinstance(value, str) and value.strip():
                result["prompt"] = value.strip()
                return

    def _fill_from_container(self, media_path: Path, result: Dict[str, Any]) -> None:
        """Fill frame rate / frame count / duration the graph didn't state.

        The graph can only state these when they are literal widget values;
        a linked ``length`` or a missing fps is unreadable, and no node
        carries the duration. The container knows all three.
        """
        self._derive_duration(result)
        if all(result.get(k) for k in ("frame_rate", "video_length", "duration")):
            return

        for key, value in self._probe_container(media_path).items():
            if not result.get(key):
                result[key] = value
        self._derive_duration(result)

    @staticmethod
    def _derive_duration(result: Dict[str, Any]) -> None:
        if result.get("duration"):
            return
        frame_rate = result.get("frame_rate")
        length = result.get("video_length")
        if frame_rate and length:
            result["duration"] = round(length / frame_rate, 3)

    def _probe_container(self, media_path: Path) -> Dict[str, Any]:
        """Read frame_rate / video_length / duration from the video stream."""
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=avg_frame_rate,r_frame_rate,nb_frames,duration"
                    ":format=duration",
                    str(media_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return {}

            data = json.loads(proc.stdout)
            streams = data.get("streams") or [{}]
            stream = streams[0]
            out: Dict[str, Any] = {}

            for rate_key in ("avg_frame_rate", "r_frame_rate"):
                rate = self._parse_rate(stream.get(rate_key))
                if rate:
                    out["frame_rate"] = rate
                    break

            try:
                frames = int(stream.get("nb_frames") or 0)
            except (TypeError, ValueError):
                frames = 0
            if frames > 0:
                out["video_length"] = frames

            for raw in (stream.get("duration"), data.get("format", {}).get("duration")):
                try:
                    duration = float(raw)
                except (TypeError, ValueError):
                    continue
                if duration > 0:
                    out["duration"] = duration
                    break

            return out

        except Exception as e:
            logger.debug(f"Container probe failed for {media_path}: {e}")
            return {}

    @staticmethod
    def _parse_rate(raw: Any) -> Optional[float]:
        """Parse an ffprobe rational like ``"24/1"``."""
        if not isinstance(raw, str) or not raw:
            return None
        try:
            num, _, den = raw.partition("/")
            rate = float(num) / (float(den) if den else 1.0)
        except (ValueError, ZeroDivisionError):
            return None
        return round(rate, 3) if rate > 0 else None

    def _post_process_results(self, result: Dict[str, Any]) -> None:
        """Clean up and finalize extracted metadata"""

        # Keep models as list (always use "models" key for consistency)
        # The scanner will handle converting to the Media.model list field
        if "models" in result:
            if len(result["models"]) == 0:
                del result["models"]
            # Keep models as list regardless of count

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
