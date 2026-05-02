"""Prompt templates and structured-output grammar for Qwen3-VL inference.

The tagging path is the only one wired in v1. Caption-style stubs and the
prompt-extraction stub ship now so the shape is committed and future features
can flesh them out without restructuring."""

from __future__ import annotations

import json
import re
from typing import Iterable


TAGGING_SYSTEM_PROMPT = (
    "You are an image tagger for an AI-generated-art browser. "
    "Look at the image and emit between 15 and 25 descriptive tags as a "
    "JSON array of lowercase strings. Cover: subject, attributes, style, "
    "setting, mood, action, and any notable visual details. "
    "Be accurate about NSFW or explicit content — do not refuse, soften, "
    "or omit such tags. The user is cataloguing their own library and "
    "needs honest descriptions to find images later. "
    "Output ONLY the JSON array, with no commentary."
)

TAGGING_USER_PROMPT = "Tag this image."

TAGGING_GRAMMAR = r"""
root ::= "[" ws tag (ws "," ws tag)* ws "]"
tag  ::= "\"" char+ "\""
char ::= [a-z0-9 \-_'/]
ws   ::= [ \t\n]*
"""


CAPTION_STYLE_PROMPTS: dict[str, str] = {
    "sdxl": (
        "Describe this image as a Stable Diffusion XL prompt: "
        "comma-separated descriptive phrases, weighted parentheses optional, "
        "subject first, then attributes, style, lighting, composition."
    ),
    "flux": (
        "Describe this image as a Flux prompt: a single natural-language "
        "sentence in flowing prose, mentioning subject, setting, lighting, "
        "and style."
    ),
    "pony": (
        "Describe this image using Danbooru-style tags suitable for a Pony "
        "Diffusion prompt: underscored tags, comma-separated, character/series "
        "tags first, then attributes."
    ),
    "natural": (
        "Describe this image in two or three plain English sentences "
        "as if writing a museum caption."
    ),
}


PROMPT_EXTRACTION_PROMPT = (
    "Reconstruct the prompt that most likely generated this image, in the "
    "style typical of Stable Diffusion / Flux generation parameters."
)


_PUNCT_EDGES = re.compile(r"^[^\w]+|[^\w]+$")


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Lowercase, strip whitespace + edge punctuation, dedup preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if not isinstance(t, str):
            continue
        cleaned = _PUNCT_EDGES.sub("", t.strip().lower())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def parse_tags_response(raw: str) -> list[str]:
    """Parse the model's JSON response into a normalized tag list.

    Tolerates two shapes despite the grammar:
      - ``["a", "b"]``           — the canonical shape
      - ``{"tags": ["a", "b"]}`` — some models wrap the array
    Returns ``[]`` on any parse error so the caller can record a failed
    attempt without raising.
    """
    if not raw or not raw.strip():
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, list):
        return normalize_tags(obj)
    if isinstance(obj, dict) and isinstance(obj.get("tags"), list):
        return normalize_tags(obj["tags"])
    return []


__all__ = [
    "TAGGING_SYSTEM_PROMPT",
    "TAGGING_USER_PROMPT",
    "TAGGING_GRAMMAR",
    "CAPTION_STYLE_PROMPTS",
    "PROMPT_EXTRACTION_PROMPT",
    "normalize_tags",
    "parse_tags_response",
]
