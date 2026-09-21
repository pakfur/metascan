import json
import re
from pathlib import Path
from typing import Dict, Any, Iterable, Optional, List, Set
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

        models, loras = self._models_and_loras(prompt_data)
        if models:
            extracted["models"] = models
        if loras:
            extracted["loras"] = loras

        return extracted

    # ---- models and loras ---------------------------------------------

    _MODEL_NAME_KEYS = ("unet_name", "ckpt_name")
    # ImpactSwitch spells its branches input1..N, the Dream switches
    # input_1..N; both are 1-based and chosen by a `select` input.
    _SWITCH_INPUT = re.compile(r"^input_?(\d+)$")
    _WEIGHT_SUFFIXES = (".safetensors", ".ckpt", ".pt")

    def _models_and_loras(
        self, prompt_data: Dict[str, Any]
    ) -> "tuple[List[str], List[Dict[str, Any]]]":
        """The model(s) and LoRAs that actually fed the sampler.

        Walks each KSampler's ``model`` wire upstream instead of listing
        every loader in the graph: a multi-mode workflow keeps several
        loaders behind a switch, and the ones on unselected branches never
        ran. LoRAs come back in application order (loader -> sampler).

        When no wire reaches a loader (a sampler class this extractor does
        not know, or a broken graph) it falls back to listing everything,
        which is what the flat scan this replaced always did.
        """
        models: List[str] = []
        lora_groups: List[List[Dict[str, Any]]] = []
        seen: Set[str] = set()
        for node in prompt_data.values():
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                link = (node.get("inputs") or {}).get("model")
                self._trace_model(prompt_data, link, models, lora_groups, seen)

        if not models:
            lora_groups = []
            for node in prompt_data.values():
                if not isinstance(node, dict):
                    continue
                inputs = node.get("inputs") or {}
                name = self._model_name(inputs)
                if name and name not in models:
                    models.append(name)
                lora_groups.insert(0, self._node_loras(inputs))

        loras: List[Dict[str, Any]] = []
        for group in reversed(lora_groups):  # walked sampler -> loader
            for lora in group:
                if lora["lora_name"] not in [seen_l["lora_name"] for seen_l in loras]:
                    loras.append(lora)
        return models, loras

    def _trace_model(
        self,
        prompt_data: Dict[str, Any],
        link: Any,
        models: List[str],
        lora_groups: List[List[Dict[str, Any]]],
        seen: Set[str],
    ) -> None:
        if not self._is_link(link):
            return
        node_id = str(link[0])
        if node_id in seen:
            return  # shared by several samplers, or a cycle
        seen.add(node_id)
        node = prompt_data.get(node_id)
        if not isinstance(node, dict):
            return
        inputs = node.get("inputs") or {}

        name = self._model_name(inputs)
        if name:
            if name not in models:
                models.append(name)
            return

        group = self._node_loras(inputs)
        if group:
            lora_groups.append(group)
        for upstream in self._upstream_model_links(prompt_data, inputs):
            self._trace_model(prompt_data, upstream, models, lora_groups, seen)

    def _model_name(self, inputs: Dict[str, Any]) -> Optional[str]:
        """The weights file a loader node names (UNETLoader,
        CheckpointLoaderSimple, ...), kept verbatim -- extension and
        subfolder included -- as this extractor always reported
        checkpoints, so existing model filter values stay valid."""
        for key in self._MODEL_NAME_KEYS:
            value = inputs.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _upstream_model_links(
        self, prompt_data: Dict[str, Any], inputs: Dict[str, Any]
    ) -> Iterable[Any]:
        branches = {
            int(match.group(1)): value
            for key, value in inputs.items()
            if (match := self._SWITCH_INPUT.match(key)) and self._is_link(value)
        }
        if branches and "select" in inputs:
            selected = self._safe_int(
                self._resolve_input(prompt_data, inputs["select"])
            )
            if selected in branches:
                return [branches[selected]]
            # Can't tell which branch ran: report them all rather than none.
            return [branches[index] for index in sorted(branches)]
        return [inputs.get("model")]

    def _node_loras(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """LoRAs one node applies: a LoraLoader-style ``lora_name`` widget,
        or the enabled ``lora_N`` rows of a Power Lora Loader (rgthree)."""
        found: List[Dict[str, Any]] = []

        def add(name: Any, weight: Any) -> None:
            if not isinstance(name, str) or not name or name == "None":
                return
            for suffix in self._WEIGHT_SUFFIXES:
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
            parsed = self._safe_float(weight)
            found.append(
                {
                    "lora_name": name,
                    "lora_weight": round(parsed, 2) if parsed is not None else 1.0,
                }
            )

        lora_name = inputs.get("lora_name")
        if isinstance(lora_name, str):
            weight = inputs.get("strength_model")
            add(lora_name, inputs.get("strength") if weight is None else weight)

        for key, row in inputs.items():
            if key.startswith("lora_") and isinstance(row, dict) and row.get("on"):
                add(row.get("lora"), row.get("strength"))
        return found

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
