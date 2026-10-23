"""Variant angles and taste-example helpers for the T2I prompt writer. Pure.

* :func:`variant_angles` reads ``T2I_VARIANT_ANGLES`` out of the hot-reloading
  prompt store: variant ``k`` (k >= 1) of a caption leads with angle ``k-1``.
* :func:`matches_model_family` decides whether a library item's metadata
  model names belong to a ``MODEL_PROFILES`` entry, so the "base+taste" writer
  only shows the VLM favorite prompts written in the target model's dialect.
* :func:`truncate_example` bounds one example's length.

Model-family rule (used by ``DatabaseManager.favorite_prompt_candidates``):
an item rendered by the T2I dialog (a ``t2i_images`` row with a ``model``)
matches only when that column equals the profile id. Any other item matches
when one of its metadata model names (lower-cased) contains one of the
profile's family tokens below. An item whose family is unknown (no model
names, or none matching a token) is left out: prompt dialects differ too much
between families (SDXL tag lists against Krea 2 prose) to risk a wrong one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from metascan.core.prompt_store import PromptStore

TASTE_EXAMPLES_K = 3
TASTE_EXAMPLE_MAX_CHARS = 600

VARIANT_ANGLES_KEY = "T2I_VARIANT_ANGLES"
EXAMPLES_PREAMBLE_KEY = "T2I_EXAMPLES_PREAMBLE"

# MODEL_PROFILES id -> substrings of a checkpoint/model name in that family.
MODEL_FAMILY_TOKENS: Dict[str, Tuple[str, ...]] = {
    "krea2": ("krea",),
    "qwen": ("qwen",),
    "sd": ("sdxl", "sd_xl", "sd-xl", "xl"),
    "zimage": ("z_image", "z-image", "zimage"),
}


def variant_angles(store: PromptStore) -> List[str]:
    """The angle list, one per non-blank line (a leading ``- `` is dropped).

    Empty when the key is missing, so a store without it just means no
    angles rather than an error.
    """
    if VARIANT_ANGLES_KEY not in store:
        return []
    try:
        raw = store.get(VARIANT_ANGLES_KEY)
    except KeyError:  # removed by a reload between the two calls
        return []
    angles: List[str] = []
    for line in raw.splitlines():
        text = line.strip()
        if text.startswith("- "):
            text = text[2:].strip()
        if text and not text.startswith("#"):
            angles.append(text)
    return angles


def matches_model_family(model_names: Iterable[str], model_id: str) -> bool:
    """Whether any of ``model_names`` belongs to profile ``model_id``'s family."""
    tokens = MODEL_FAMILY_TOKENS.get(model_id)
    if not tokens:
        return False
    for name in model_names:
        lowered = (name or "").lower()
        if any(token in lowered for token in tokens):
            return True
    return False


def is_image_path(path: str) -> bool:
    """Videos are never taste examples."""
    from metascan.core.duplicate_detection import VIDEO_EXTENSIONS

    return Path(path).suffix.lower() not in VIDEO_EXTENSIONS


def truncate_example(text: str, limit: int = TASTE_EXAMPLE_MAX_CHARS) -> str:
    """Whitespace-collapsed ``text``, cut to at most ``limit`` characters
    (at a word boundary when one is reasonably close)."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    if space >= limit * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:")
