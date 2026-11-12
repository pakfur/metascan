"""What an image was generated with, as the Upscale dialog pre-fills it.

A refine workflow (Moss + Ultimate SD Upscale) redraws tiles at a low
denoise, so it needs the image's own prompt, negative, LoRAs, seed and model:
without them it drifts towards the plain base model. Every value is a
starting point the owner can edit or clear.

Pure: the caller reads the media record, the T2I LoRA stack and the scorecard
state; nothing here touches the database.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from metascan.core.t2i_form import SEED_MAX

# Extensions ComfyUI's LoRA loaders list. Metadata parsing drops the
# extension of a LoRA name; a stack entry must name the file.
LORA_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")


def lora_file_name(name: str) -> str:
    """``name`` as a loader file name: ``.safetensors`` added when the
    metadata dropped the extension."""
    if name.lower().endswith(LORA_EXTENSIONS):
        return name
    return name + ".safetensors"


def _metadata_loras(media: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for lora in getattr(media, "loras", None) or []:
        name = getattr(lora, "lora_name", None)
        if not isinstance(name, str) or not name.strip():
            continue
        weight = getattr(lora, "lora_weight", None)
        out.append(
            {
                "name": lora_file_name(name.strip()),
                "strength": float(weight) if isinstance(weight, (int, float)) else 1.0,
            }
        )
    return out


def model_family(name: Optional[str]) -> Optional[str]:
    """The folder a model file sits in below its models folder (``moss`` for
    ``moss\\dirtyMoss_bf16.safetensors``), lower-cased; None for a bare name.
    Two models of one family take the same text encoder, VAE and LoRAs."""
    if not name:
        return None
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    return parts[0].lower() if len(parts) > 1 else None


def same_family(a: Optional[str], b: Optional[str]) -> bool:
    """True when a refine graph built for ``b`` can take the model ``a``:
    the same file, or two files in the same model folder."""
    if not a or not b:
        return False
    if os.path.normcase(a.replace("\\", "/")) == os.path.normcase(b.replace("\\", "/")):
        return True
    fa, fb = model_family(a), model_family(b)
    return fa is not None and fa == fb


# Loaders whose file a graph bakes in: the model, and LoRAs outside the stack
# (an I2V turbo preset's step-distilled LoRA).
_MODEL_LOADERS = (("UNETLoader", "unet_name"), ("CheckpointLoaderSimple", "ckpt_name"))
_LORA_LOADERS = (
    ("LoraLoaderModelOnly", "strength_model"),
    ("LoraLoader", "strength_model"),
)


def _node_order(node_id: str) -> Tuple[int, ...]:
    """``"105:6"`` sorts as (105, 6): subgraph ids keep their parent's place."""
    out = []
    for part in str(node_id).split(":"):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def _graph_model(graph: Dict[str, Any]) -> Optional[str]:
    """The model file an API graph loads: its MS_MODEL node's, else its
    first model loader's."""
    nodes = sorted(
        ((k, v) for k, v in graph.items() if isinstance(v, dict)),
        key=lambda kv: _node_order(kv[0]),
    )
    first: Optional[str] = None
    for _, node in nodes:
        inputs = node.get("inputs") or {}
        for class_type, widget in _MODEL_LOADERS:
            if node.get("class_type") != class_type:
                continue
            value = inputs.get(widget)
            if not isinstance(value, str) or not value:
                continue
            if (node.get("_meta") or {}).get("title") == "MS_MODEL":
                return value
            first = first or value
    return first


def _graph_loras(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The LoRAs an API graph bakes in outside its MS_LORA_STACK, in node
    order. A linked strength or name is no fixed value and is left out."""
    out: List[Dict[str, Any]] = []
    for _, node in sorted(
        ((k, v) for k, v in graph.items() if isinstance(v, dict)),
        key=lambda kv: _node_order(kv[0]),
    ):
        inputs = node.get("inputs") or {}
        for class_type, strength_key in _LORA_LOADERS:
            if node.get("class_type") != class_type:
                continue
            name = inputs.get("lora_name")
            strength = inputs.get(strength_key, 1.0)
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(strength, (int, float)) or isinstance(strength, bool):
                continue
            out.append({"name": name.strip(), "strength": float(strength)})
    return out


def i2v_generation(i2v: Dict[str, Any]) -> Dict[str, Any]:
    """``{prompt, seed, model, loras}`` an I2V clip was rendered with
    (``db.i2v_generation_for_path``). A MiniMax refine redraws the clip with
    its own prompt, diffusion model and LoRAs, so the model is the one its
    preset loads and the LoRAs are the preset's baked ones (its turbo LoRA)
    followed by the stack the dialog sent."""
    graph = i2v.get("workflow") if isinstance(i2v.get("workflow"), dict) else None
    seed = i2v.get("seed")
    prompt = i2v.get("prompt")
    return {
        "prompt": prompt if isinstance(prompt, str) and prompt.strip() else None,
        "seed": (
            seed
            if isinstance(seed, int)
            and not isinstance(seed, bool)
            and 0 <= seed <= SEED_MAX
            else None
        ),
        "model": _graph_model(graph) if graph else None,
        "loras": (_graph_loras(graph) if graph else [])
        + [dict(entry) for entry in i2v.get("loras") or []],
    }


def generation_defaults(
    media: Any,
    t2i_loras: Optional[List[Dict[str, Any]]],
    blind: bool,
    i2v: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``{prompt, negative, seed, model, loras, blind}`` for the dialog.

    ``t2i_loras`` (the stack a T2I run sent, file names intact) wins over the
    parsed metadata. ``i2v`` (an I2V clip's lineage) wins over both for the
    prompt, seed, model and LoRAs: the clip's metadata names neither its
    baked model nor its LoRAs reliably. A blind scorecard image (no grade
    yet) gives no prompt or negative, as its detail record does not.
    """
    if i2v is not None:
        made = i2v_generation(i2v)
        return {
            "prompt": None if blind else made["prompt"],
            "negative": None,
            "seed": made["seed"],
            "model": made["model"],
            "loras": made["loras"],
            "blind": blind,
        }
    if media is None:
        return {
            "prompt": None,
            "negative": None,
            "seed": None,
            "model": None,
            "loras": [],
            "blind": blind,
        }
    models = [m for m in (getattr(media, "model", None) or []) if isinstance(m, str)]
    seed = getattr(media, "seed", None)
    return {
        "prompt": None if blind else (getattr(media, "prompt", None) or None),
        "negative": (
            None if blind else (getattr(media, "negative_prompt", None) or None)
        ),
        # The dialog's seed range: a seed it cannot take is left out.
        "seed": seed if isinstance(seed, int) and 0 <= seed <= SEED_MAX else None,
        "model": models[0] if models else None,
        "loras": t2i_loras if t2i_loras is not None else _metadata_loras(media),
        "blind": blind,
    }


__all__ = [
    "generation_defaults",
    "i2v_generation",
    "lora_file_name",
    "model_family",
    "same_family",
]
