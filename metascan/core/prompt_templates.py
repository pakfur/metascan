"""Prompt-template composer for the /api/prompt endpoints (TA-10).

Pure functions — no I/O, no model calls. Each composer returns a
``(system_prompt, user_prompt)`` tuple ready to feed into
``VlmClient.generate_text``.

The Literal types are the single source of truth for the playground
target-model / style enums; the API layer (`backend/api/prompt.py`) and
the frontend (`frontend/src/api/prompt.ts`) mirror them.
"""

from __future__ import annotations

from typing import Literal


TargetModel = Literal["sdxl", "flux-chroma", "qwen-t2i", "pony"]
Architecture = Literal["t2i"]  # t2v / i2v / i2i deferred to v2
StyleEnhancement = Literal[
    "anime",
    "photorealistic",
    "cinematic",
    "cartoon",
    "watercolor",
    "oil-painting",
    "comic",
    "hyperdetailed",
    "minimalist",
    "moody-lighting",
]


TARGET_MODEL_GUIDANCE: dict[TargetModel, str] = {
    "sdxl": (
        "comma-separated descriptive phrases, subject first then "
        "attributes/style/lighting/composition; weighted parens optional."
    ),
    "flux-chroma": (
        "a single natural-language paragraph describing subject, "
        "setting, lighting, mood, in flowing prose."
    ),
    "qwen-t2i": (
        "a natural-language sentence describing the subject and key "
        "attributes; no syntax conventions."
    ),
    "pony": (
        "Danbooru-style underscored tags, comma-separated, leading with "
        "score_9, score_8_up, score_7_up, then character/series/attributes."
    ),
}


STYLE_PHRASES: dict[StyleEnhancement, str] = {
    "anime": "anime aesthetic with cel-shaded shapes",
    "photorealistic": "photorealistic style with realistic lighting and textures",
    "cinematic": "cinematic composition with dramatic lighting",
    "cartoon": "cartoon / illustrated style with bold outlines",
    "watercolor": "watercolor painting with soft washes",
    "oil-painting": "oil painting with visible brushwork",
    "comic": "comic-book style with cel shading and ink lines",
    "hyperdetailed": "hyperdetailed rendering, intricate fine detail",
    "minimalist": "minimalist composition with limited color palette",
    "moody-lighting": "moody, low-key lighting with deep shadows",
}


_OUTPUT_RULE = "Output ONLY the prompt text — no preamble, no commentary, no quotes."


def _style_clause(styles: list[StyleEnhancement]) -> str:
    if not styles:
        return ""
    if len(styles) > 3:
        raise ValueError("at most 3 style enhancements allowed")
    phrases = [STYLE_PHRASES[s] for s in styles]
    return " Apply these stylistic directions: " + "; ".join(phrases) + "."


def compose_generate_prompts(
    target_model: TargetModel,
    architecture: Architecture,
    styles: list[StyleEnhancement],
) -> tuple[str, str]:
    """System + user prompts for a fresh generate-from-image request."""
    style_clause = _style_clause(styles)
    system = (
        f"You are an expert prompt engineer for AI image generation. "
        f"Look at the supplied image and produce a prompt suitable for "
        f"a {target_model} {architecture} model.{style_clause} "
        f"Format: {TARGET_MODEL_GUIDANCE[target_model]} "
        f"{_OUTPUT_RULE}"
    )
    user = "Write a prompt that would generate this image."
    return system, user


def compose_transform_prompts(
    source_prompt: str,
    target_model: TargetModel,
    architecture: Architecture,
) -> tuple[str, str]:
    """System + user prompts for rewriting an existing prompt for a new target."""
    system = (
        f"You are an expert prompt engineer. Rewrite the supplied prompt "
        f"to suit a {target_model} {architecture} model. Preserve the "
        f"subject and key attributes; adapt syntax and conventions to the "
        f"target. Format: {TARGET_MODEL_GUIDANCE[target_model]} "
        f"{_OUTPUT_RULE}"
    )
    user = (
        f"Original prompt:\n{source_prompt}\n\n"
        f"Rewrite for {target_model} {architecture}."
    )
    return system, user


def compose_clean_prompts(source_prompt: str) -> tuple[str, str]:
    """System + user prompts for a cleanup pass — no target-model semantics."""
    system = (
        "Clean up the supplied AI image-generation prompt: remove "
        "redundancies, fix typos, normalize separators, but preserve "
        f"all meaningful content and style. {_OUTPUT_RULE}"
    )
    user = f"Prompt to clean:\n{source_prompt}"
    return system, user


__all__ = [
    "TargetModel",
    "Architecture",
    "StyleEnhancement",
    "TARGET_MODEL_GUIDANCE",
    "STYLE_PHRASES",
    "compose_generate_prompts",
    "compose_transform_prompts",
    "compose_clean_prompts",
]
