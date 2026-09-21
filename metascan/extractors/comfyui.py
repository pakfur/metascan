import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import logging

from metascan.extractors.base import MetadataExtractor

logger = logging.getLogger(__name__)


class ComfyUIExtractor(MetadataExtractor):
    """Extract metadata from ComfyUI generated images"""

    def can_extract(self, media_path: Path) -> bool:
        """Check if image contains ComfyUI metadata"""
        if media_path.suffix.lower() in {".mp4", ".webm", ".mov"}:
            return False

        metadata = self._get_exif_metadata(media_path)
        return "prompt" in metadata or "workflow" in metadata

    def extract(self, media_path: Path) -> Optional[Dict[str, Any]]:
        try:
            metadata = self._get_exif_metadata(media_path)

            result: Dict[str, Any] = {"source": "ComfyUI", "raw_metadata": {}}

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

    def _extract_parameters(
        self, prompt_data: Dict[str, Any]
    ) -> Dict[str, Any]:  # noqa: C901
        extracted: Dict[str, Any] = {}
        loras: List[Dict[str, Any]] = []

        for node_id, node_data in prompt_data.items():
            if not isinstance(node_data, dict):
                continue

            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            # Any widget can be wired instead of typed in, in which case its
            # value is a [node_id, output_index] link rather than a literal.
            def widget(key: str) -> Any:
                return self._resolve_input(prompt_data, inputs.get(key))

            if class_type == "KSampler":
                sampler = widget("sampler_name")
                scheduler = widget("scheduler")
                extracted["sampler"] = sampler if isinstance(sampler, str) else None
                extracted["steps"] = self._safe_int(widget("steps"))
                extracted["cfg_scale"] = self._safe_float(widget("cfg"))
                extracted["seed"] = self._safe_int(widget("seed"))
                extracted["scheduler"] = (
                    scheduler if isinstance(scheduler, str) else None
                )

            elif class_type == "CheckpointLoaderSimple":
                extracted["model"] = inputs.get("ckpt_name")

            elif class_type == "CLIPTextEncode":
                text = widget("text")
                if not isinstance(text, str):
                    text = ""

                # The node title is the reliable signal when the author set
                # one; without it a negative prompt that happens to avoid
                # the heuristic's keywords gets stored as the positive.
                title = str((node_data.get("_meta") or {}).get("title") or "").lower()
                if text and "negative" in title:
                    extracted.setdefault("negative_prompt", text)
                elif text and "positive" in title:
                    extracted.setdefault("prompt", text)
                elif text and "prompt" not in extracted:
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

        if loras:
            extracted["loras"] = loras

        return extracted

    # Widget names that hold a node's own literal value, in preference order.
    # ``text_0`` is ShowText's rendered string -- what downstream nodes
    # actually received, as opposed to the template further upstream.
    _LITERAL_KEYS = ("value", "text_0", "text", "string", "prompt")

    @staticmethod
    def _is_link(value: Any) -> bool:
        """API-format graphs encode a wire as ``[source_node_id, output_index]``."""
        return (
            isinstance(value, list)
            and len(value) == 2
            and isinstance(value[0], (str, int))
            and isinstance(value[1], int)
        )

    def _resolve_input(
        self,
        prompt_data: Dict[str, Any],
        value: Any,
        _seen: Optional[Set[str]] = None,
    ) -> Any:
        """Follow a wired input back to the literal it carries.

        Literals are returned unchanged. For a link, a literal on the
        immediate source node wins over walking further upstream, so a
        ShowText node yields the expanded prompt rather than the wildcard
        template feeding it. Returns None for a dangling link, a cycle, or
        a source that exposes no recognisable literal.
        """
        if not self._is_link(value):
            return value

        seen = _seen if _seen is not None else set()
        node_id = str(value[0])
        if node_id in seen:
            return None
        seen.add(node_id)

        source = prompt_data.get(node_id)
        if not isinstance(source, dict):
            return None
        source_inputs = source.get("inputs") or {}

        for key in self._LITERAL_KEYS:
            candidate = source_inputs.get(key)
            if candidate in (None, "") or self._is_link(candidate):
                continue
            return candidate

        for key in self._LITERAL_KEYS:
            candidate = source_inputs.get(key)
            if self._is_link(candidate):
                resolved = self._resolve_input(prompt_data, candidate, seen)
                if resolved is not None:
                    return resolved

        return None
