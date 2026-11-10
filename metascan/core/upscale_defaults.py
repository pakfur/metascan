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
from typing import Any, Dict, List, Optional

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


def generation_defaults(
    media: Any,
    t2i_loras: Optional[List[Dict[str, Any]]],
    blind: bool,
) -> Dict[str, Any]:
    """``{prompt, negative, seed, model, loras, blind}`` for the dialog.

    ``t2i_loras`` (the stack a T2I run sent, file names intact) wins over the
    parsed metadata. A blind scorecard image (no grade yet) gives no prompt or
    negative, as its detail record does not.
    """
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
    "lora_file_name",
    "model_family",
    "same_family",
]
