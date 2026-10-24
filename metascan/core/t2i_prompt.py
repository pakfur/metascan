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

import logging
import re
from typing import Dict, NamedTuple, Optional, Sequence, Tuple

from metascan.core.meta_prompt_templates import split_negative_block
from metascan.core.prompt_store import PromptStore, get_prompt_store
from metascan.core.t2i_models import T2iModelProfile

logger = logging.getLogger(__name__)

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

_CLOSING = "Write the prompt now."
_USER_TEMPLATE = "DESCRIPTION:\n{description}\n\n" + _CLOSING
_USER_TEMPLATE_DIRECTED = (
    "DESCRIPTION:\n{description}\n\nDIRECTION:\n{direction}\n\n" + _CLOSING
)


class ComposedPrompts(NamedTuple):
    """What :func:`compose_guided_t2i_prompts` built, and whether the
    EXAMPLES block is in it (examples were given AND the preamble exists)."""

    system: str
    user: str
    examples_used: bool


def compose_t2i_prompts(
    profile: T2iModelProfile,
    resolved_caption: str,
    content_mode: str,
    direction: Optional[str] = None,
    angle: Optional[str] = None,
    examples: Sequence[str] = (),
    store: Optional[PromptStore] = None,
) -> Tuple[str, str]:
    """``(system_prompt, user_prompt)``; see :func:`compose_guided_t2i_prompts`."""
    composed = compose_guided_t2i_prompts(
        profile, resolved_caption, content_mode, direction, angle, examples, store
    )
    return composed.system, composed.user


def compose_guided_t2i_prompts(
    profile: T2iModelProfile,
    resolved_caption: str,
    content_mode: str,
    direction: Optional[str] = None,
    angle: Optional[str] = None,
    examples: Sequence[str] = (),
    store: Optional[PromptStore] = None,
) -> ComposedPrompts:
    """Return ``(system_prompt, user_prompt)`` for ``VlmClient.generate_text``.

    ``content_mode`` is one of :data:`CONTENT_MODES`: ``uncensored`` and
    ``sfw`` append the matching existing directive to the system prompt,
    ``default`` adds nothing. The caption goes into the user turn verbatim.
    A non-blank ``direction`` (caption directions, ``t2i_directions``) adds a
    DIRECTION block after it; without one the user turn is unchanged.

    A non-blank ``angle`` (a variant's ``T2I_VARIANT_ANGLES`` entry) adds an
    ``ANGLE: lead with <angle>`` line before the closing line. Non-blank
    ``examples`` (taste guidance) add an ``EXAMPLES:`` block, the
    ``T2I_EXAMPLES_PREAMBLE`` and the numbered examples, after the style
    guide and before the directive. With neither, both prompts are exactly
    what they were before these arguments existed. ``examples_used`` says
    whether the block went in: a hot reload that removed the preamble drops
    it, and a writer must then not be recorded as taste-guided.

    ``store`` defaults to the process-wide prompt store; the writer
    training dataset passes its own so it composes exactly these prompts.
    """
    if content_mode not in CONTENT_MODES:
        raise ValueError(
            f"unknown content mode {content_mode!r}; expected one of: "
            + ", ".join(CONTENT_MODES)
        )
    if store is None:
        store = get_prompt_store()
    system = store.get("T2I_CAPTION_PREAMBLE").rstrip() + "\n\n"
    system += store.get(profile.meta_key)
    shown = [" ".join(e.split()) for e in examples if e and e.strip()]
    preamble: Optional[str] = None
    if shown:
        try:
            preamble = store.get("T2I_EXAMPLES_PREAMBLE").strip()
        except KeyError:
            # Removed by a hot reload: write unguided rather than fail.
            logger.warning(
                "T2I_EXAMPLES_PREAMBLE missing from the prompt store; "
                "writing without taste examples"
            )
    examples_used = False
    if shown and preamble is not None:
        examples_used = True
        numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(shown, 1))
        system = system.rstrip() + "\n\nEXAMPLES:\n" + preamble + "\n\n" + numbered
    directive_key = _DIRECTIVE_KEYS.get(content_mode)
    if directive_key is not None:
        system += store.get(directive_key)
    if direction is not None and direction.strip():
        user = _USER_TEMPLATE_DIRECTED.format(
            description=resolved_caption, direction=direction.strip()
        )
    else:
        user = _USER_TEMPLATE.format(description=resolved_caption)
    if angle is not None and angle.strip():
        # The closing line ends every template; splice in front of it (a
        # caption may itself contain the closing words).
        user = (
            user[: -len(_CLOSING)] + f"ANGLE: lead with {angle.strip()}\n\n" + _CLOSING
        )
    return ComposedPrompts(system, user, examples_used)


def add_categories_line(user: str, names: Sequence[str]) -> str:
    """Splice a ``CATEGORIES: a, b`` line in front of the closing line of a
    composed user turn (an ``all``-scope writer adapter is trained with the
    image's generation categories there, and writes with the dialog's).
    Names are deduplicated and sorted case-insensitively, so training and
    inference build the same line whatever order they come in. No
    non-blank names: unchanged."""
    unique: Dict[str, str] = {}
    for n in names:
        if n and n.strip():
            clean = " ".join(n.split())
            unique.setdefault(clean.lower(), clean)
    shown = [unique[k] for k in sorted(unique)]
    if not shown or not user.endswith(_CLOSING):
        return user
    return user[: -len(_CLOSING)] + f"CATEGORIES: {', '.join(shown)}\n\n" + _CLOSING


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
    profile: T2iModelProfile,
    resolved_caption: str,
    direction: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """``(prompt, negative)`` to use when no VLM prompt is available.

    The prompt is the resolved caption (whitespace-trimmed, parentheses
    removed), with the stock quality prefix in front for SDXL. Models that
    take a negative prompt get their stock negative; the others get ``None``.
    A non-blank ``direction`` is appended to the caption.
    """
    store = get_prompt_store()
    text = resolved_caption.strip()
    if direction is not None and direction.strip():
        text = f"{text} {direction.strip()}" if text else direction.strip()
    prompt = strip_parentheses(text)
    prefix_key = _FALLBACK_PREFIX_KEYS.get(profile.id)
    if prefix_key is not None:
        prefix = store.get(prefix_key).strip()
        prompt = f"{prefix}, {prompt}" if prompt else prefix
    negative_key = _FALLBACK_NEGATIVE_KEYS.get(profile.id)
    if profile.has_negative and negative_key is not None:
        return prompt, store.get(negative_key).strip()
    return prompt, None
