"""Prompt composition for the t2i dialog (spec section 5).

Pure apart from reading strings out of the hot-reloading prompt store:

* :func:`compose_t2i_prompts` builds the ``(system, user)`` pair that asks the
  VLM to rewrite a resolved caption in a model's style. The system prompt is
  ``T2I_CAPTION_PREAMBLE`` (no image is attached; the description is ground
  truth) + the model's guideline (``profile.meta_key``) + the usual content
  directive; the user turn is the description and a fixed closing line.
* :func:`parse_t2i_output` turns the VLM's raw reply into ``(prompt,
  negative)`` for a model profile.
* :func:`fallback_prompt` is what an unattended run uses when there is no VLM
  or it fails: the resolved caption, with a stock quality prefix for SDXL and
  a stock negative for the models that take one.

Parentheses are ComfyUI weighting syntax, so generated prompt text must never
contain them: :func:`strip_parentheses` enforces that on everything produced
here (VLM output and fallbacks). Manual prompts are the user's own text and
are never touched.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from metascan.core.meta_prompt_templates import split_negative_block
from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_models import T2iModelProfile

CONTENT_MODES: Tuple[str, ...] = ("uncensored", "sfw", "default")

# content_mode -> the existing directive appended to the system prompt
# ("default" appends nothing).
_DIRECTIVE_KEYS: Dict[str, str] = {
    "uncensored": "UNCENSORED_DIRECTIVE",
    "sfw": "SAFETY_DIRECTIVE",
}

# model id -> prompt-store key of its stock fallback pieces
_FALLBACK_PREFIX_KEYS: Dict[str, str] = {"sd": "T2I_FALLBACK_PREFIX_SD"}
_FALLBACK_NEGATIVE_KEYS: Dict[str, str] = {
    "sd": "T2I_FALLBACK_NEGATIVE_SD",
    "qwen": "T2I_FALLBACK_NEGATIVE_QWEN",
}

_USER_TEMPLATE = "DESCRIPTION:\n{description}\n\nWrite the prompt now."


def compose_t2i_prompts(
    profile: T2iModelProfile, resolved_caption: str, content_mode: str
) -> Tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for ``VlmClient.generate_text``.

    ``content_mode`` is one of :data:`CONTENT_MODES`: ``uncensored`` and
    ``sfw`` append the matching existing directive to the system prompt,
    ``default`` adds nothing. The caption goes into the user turn verbatim.
    """
    if content_mode not in CONTENT_MODES:
        raise ValueError(
            f"unknown content mode {content_mode!r}; expected one of: "
            + ", ".join(CONTENT_MODES)
        )
    store = get_prompt_store()
    system = store.get("T2I_CAPTION_PREAMBLE").rstrip() + "\n\n"
    system += store.get(profile.meta_key)
    directive_key = _DIRECTIVE_KEYS.get(content_mode)
    if directive_key is not None:
        system += store.get(directive_key)
    return system, _USER_TEMPLATE.format(description=resolved_caption)


def strip_parentheses(text: str) -> str:
    """Remove ``(`` and ``)`` and close the gaps they leave.

    Only runs of spaces and tabs are collapsed, and only when something was
    removed, so newlines and untouched text pass through unchanged.
    """
    if "(" not in text and ")" not in text:
        return text
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"[()]", "", text))


def parse_t2i_output(profile: T2iModelProfile, raw: str) -> Tuple[str, Optional[str]]:
    """Turn the VLM's raw reply into ``(prompt, negative_or_none)``.

    The reply is cleaned and split at a ``Negative:`` line with
    :func:`split_negative_block`. A model without a negative prompt drops any
    such block (a stray one must not reach its positive prompt). Parentheses
    are removed from both parts.
    """
    positive, negative = split_negative_block(raw)
    if not profile.has_negative or negative is None:
        return strip_parentheses(positive), None
    return strip_parentheses(positive), strip_parentheses(negative)


def fallback_prompt(
    profile: T2iModelProfile, resolved_caption: str
) -> Tuple[str, Optional[str]]:
    """``(prompt, negative)`` to use when no VLM prompt is available.

    The prompt is the resolved caption (whitespace-trimmed, parentheses
    removed), with the stock quality prefix in front for SDXL. Models that
    take a negative prompt get their stock negative; the others get ``None``.
    """
    store = get_prompt_store()
    prompt = strip_parentheses(resolved_caption.strip())
    prefix_key = _FALLBACK_PREFIX_KEYS.get(profile.id)
    if prefix_key is not None:
        prefix = store.get(prefix_key).strip()
        prompt = f"{prefix}, {prompt}" if prompt else prefix
    negative_key = _FALLBACK_NEGATIVE_KEYS.get(profile.id)
    if profile.has_negative and negative_key is not None:
        return prompt, store.get(negative_key).strip()
    return prompt, None
