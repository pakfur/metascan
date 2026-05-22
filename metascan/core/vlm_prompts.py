"""Prompt templates and structured-output grammar for Qwen3-VL inference.

The tagging path is the only one wired in v1. The system / user prompts
and the GBNF grammar live in ``data/meta_prompt.yml`` and are exposed
here as module-level attributes via :func:`__getattr__` so callers can
do ``from metascan.core.vlm_prompts import TAGGING_SYSTEM_PROMPT`` and
still pick up hot-reloads (each ``from ... import`` re-resolves the
attribute against the live store).
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from metascan.core.prompt_store import get_prompt_store


# Names that resolve to YAML-backed prompt strings via __getattr__ below.
_DYNAMIC_PROMPTS = frozenset(
    {
        "TAGGING_SYSTEM_PROMPT",
        "TAGGING_USER_PROMPT",
        "TAGGING_GRAMMAR",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve ``TAGGING_*`` module attributes against the live store.

    Plain ``from metascan.core.vlm_prompts import TAGGING_SYSTEM_PROMPT``
    re-runs this getter every call site evaluation, so an edit to the
    YAML reaches the next request without a process restart. Unknown
    names raise :class:`AttributeError` as the import machinery expects.
    """
    if name in _DYNAMIC_PROMPTS:
        return get_prompt_store().get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


# TAGGING_SYSTEM_PROMPT / TAGGING_USER_PROMPT / TAGGING_GRAMMAR are
# intentionally NOT in __all__ — they're resolved dynamically by
# :func:`__getattr__` against the live prompt store, so a wildcard import
# would copy the value at import time and miss subsequent hot reloads.
# Use direct attribute access or a fresh ``from ... import`` per call.
__all__ = [
    "normalize_tags",
    "parse_tags_response",
]
