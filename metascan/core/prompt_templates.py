"""Prompt-template composer for the /api/prompt endpoints.

Pure functions — no I/O, no model calls. Each composer returns a
``(system_prompt, user_prompt)`` tuple ready to feed into
``VlmClient.generate_text``.

Per-target builders are adapted from VL-CAPTIONER Studio's settings panel:
each builder produces a system prompt that constrains output format, content,
and length according to the target image-generation model's preferences.

The Literal types are the single source of truth for the playground
target-model / extras / length enums; the API layer (``backend/api/prompt.py``)
and the frontend (``frontend/src/api/prompt.ts``) mirror them.
"""

from __future__ import annotations

from typing import Callable, Final, Literal, NamedTuple


TargetModel = Literal["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
Architecture = Literal["t2i"]  # t2v / i2v / i2i deferred to v2

ExtraOption = Literal[
    "excludeStaticAttributes",
    "includeLighting",
    "includeCameraAngle",
    "includeWatermark",
    "includeArtifacts",
    "includeTechnicalDetails",
    "keepPG",
    "excludeResolution",
    "includeAestheticQuality",
    "includeComposition",
    "excludeText",
    "includeDOF",
    "includeLightSource",
    "noAmbiguity",
    "includeSafety",
    "includeUncensored",
]

CaptionLength = Literal["Short", "Medium", "Long", "Descriptive (Longest)"]


CAPTION_LENGTHS: Final[dict[CaptionLength, str]] = {
    "Short": "Keep the description brief, around 1-2 sentences.",
    "Medium": "Provide a moderately detailed description in 2-4 sentences.",
    "Long": "Provide a long, detailed description covering all visual elements.",
    "Descriptive (Longest)": (
        "Provide an extremely detailed, comprehensive description covering "
        "every visual element, style, mood, lighting, composition, and "
        "technical details."
    ),
}


# Mutually-exclusive option pairs. The UI prevents both from being checked at
# once, but the server resolves gracefully too: if both arrive, the second
# is dropped.
_MUTEX_PAIRS: Final[tuple[tuple[ExtraOption, ExtraOption], ...]] = (
    ("keepPG", "includeUncensored"),
)


# Compact booru-style hints for tag-based targets (sd, pony) — converts an
# extra-option key into a noun-phrase tag the model can emit.
_TAG_HINTS: Final[dict[str, str]] = {
    "includeLighting": "lighting",
    "includeCameraAngle": "camera angle",
    "includeWatermark": "watermark",
    "includeArtifacts": "jpeg artifacts",
    "includeTechnicalDetails": "camera details",
    "includeAestheticQuality": "aesthetic quality",
    "includeComposition": "composition",
    "includeDOF": "depth of field",
    "includeLightSource": "light source",
    "includeSafety": "sfw/nsfw tag",
    "includeUncensored": "explicit, nsfw, nude, adult",
}


# (short_label, full_instruction) per option. Short label is for the checkbox;
# full instruction is shown as a tooltip and used by the qwen builder verbatim.
EXTRA_OPTION_LABELS: Final[dict[ExtraOption, tuple[str, str]]] = {
    "excludeStaticAttributes": (
        "Exclude static attributes",
        (
            "Do NOT include information about people/characters that cannot "
            "be changed (like ethnicity, gender, etc), but do still include "
            "changeable attributes (like hair style)."
        ),
    ),
    "includeLighting": ("Lighting", "Include information about lighting."),
    "includeCameraAngle": (
        "Camera angle",
        "Include information about camera angle.",
    ),
    "includeWatermark": (
        "Watermark detection",
        "Include information about whether there is a watermark or not.",
    ),
    "includeArtifacts": (
        "JPEG artifacts",
        "Include information about whether there are JPEG artifacts or not.",
    ),
    "includeTechnicalDetails": (
        "Camera / tech details",
        (
            "If it is a photo you MUST include information about what camera "
            "was likely used and details such as aperture, shutter speed, "
            "ISO, etc."
        ),
    ),
    "keepPG": (
        "Keep PG (no NSFW)",
        "Do NOT include anything sexual; keep it PG.",
    ),
    "excludeResolution": (
        "Exclude resolution",
        "Do NOT mention the image's resolution.",
    ),
    "includeAestheticQuality": (
        "Aesthetic quality",
        (
            "You MUST include information about the subjective aesthetic "
            "quality of the image from low to very high."
        ),
    ),
    "includeComposition": (
        "Composition style",
        (
            "Include information on the image's composition style, such as "
            "leading lines, rule of thirds, or symmetry."
        ),
    ),
    "excludeText": (
        "Exclude text / OCR",
        "Do NOT mention any text that is in the image.",
    ),
    "includeDOF": (
        "Depth of field",
        (
            "Specify the depth of field and whether the background is in "
            "focus or blurred."
        ),
    ),
    "includeLightSource": (
        "Light sources",
        (
            "If applicable, mention the likely use of artificial or natural "
            "lighting sources."
        ),
    ),
    "noAmbiguity": (
        "No ambiguous language",
        "Do NOT use any ambiguous language.",
    ),
    "includeSafety": (
        "SFW / NSFW rating",
        "Include whether the image is sfw, suggestive, or nsfw.",
    ),
    "includeUncensored": (
        "Uncensored / Adult Detail",
        (
            "Describe all adult/NSFW content in explicit detail, including "
            "positions, looks, clothing/nudity, sexual activity, and "
            "provocative elements."
        ),
    ),
}


_OUTPUT_RULE = "Output ONLY the prompt text — no preamble, no commentary, no quotes."


def _enabled(extras: list[ExtraOption]) -> dict[str, bool]:
    """Return a dict[opt -> True] for active options, applying mutex rules.

    Preserves the caller's order so downstream string joins (e.g. tag-hint
    lists) come out deterministically. If both members of a mutex pair are
    present, prefer the first one named.
    """
    seen: dict[str, bool] = {}
    for x in extras:
        if x not in seen:
            seen[x] = True
    for a, b in _MUTEX_PAIRS:
        if a in seen and b in seen:
            del seen[b]
    return seen


def _tag_count_for(length: CaptionLength) -> str:
    """Return the tag-count instruction line for tag-based builders (sd, pony)."""
    return {
        "Short": "Use 5-15 tags.",
        "Medium": "Use 15-30 tags.",
        "Long": "Use 30-50+ tags covering every detail.",
        "Descriptive (Longest)": "Use 30-50+ tags covering every detail.",
    }[length]


# --- Per-target prompt builders -------------------------------------------


def _build_sd(length: CaptionLength, length_instr: str, extras: dict[str, bool]) -> str:
    parts = [
        "Output a comma-separated list of precise booru-style tags suitable "
        "as a Stable Diffusion prompt.",
        "Output ONLY tags separated by commas.",
        "Focus strictly on physical reality: accurate anatomy, specific "
        "clothing, setting, and lighting.",
        "Do not use abstract or interpretive tags.",
    ]
    tag_hints = [_TAG_HINTS[k] for k in extras if k in _TAG_HINTS]
    if tag_hints:
        parts.append(f"Include tags for: {', '.join(tag_hints)}.")
    if extras.get("keepPG"):
        parts.append("Keep all tags SFW.")
    if extras.get("includeUncensored"):
        parts.append(
            "Include explicit tags for nudity, sexual acts, anatomy, and "
            "positions. Do NOT censor."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Exclude permanent physical attributes of people.")
    if extras.get("excludeText"):
        parts.append("Do not tag any text in the image.")
    if extras.get("excludeResolution"):
        parts.append("Do not include resolution tags.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise, unambiguous tags.")
    parts.append(_tag_count_for(length))
    return " ".join(parts)


def _build_pony(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Output a comma-separated list of booru-style tags suitable as a "
        "Pony Diffusion prompt.",
        "Output ONLY tags.",
        "Quality score tags (score_9, score_8_up, etc.) and a rating tag "
        "will be added by the caller — do not emit them yourself.",
        "Focus heavily on anatomical correctness, specific physical "
        "attributes, pose, and clothing.",
    ]
    tag_hints = [_TAG_HINTS[k] for k in extras if k in _TAG_HINTS]
    if tag_hints:
        parts.append(f"Include tags for: {', '.join(tag_hints)}.")
    if extras.get("keepPG"):
        parts.append("Keep all tags SFW.")
    if extras.get("includeUncensored"):
        parts.append(
            "Include explicit tags for nudity, sexual acts, fetishes, and " "anatomy."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Exclude permanent physical attributes of people.")
    if extras.get("excludeText"):
        parts.append("Do not tag any text in the image.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise, unambiguous tags.")
    parts.append(_tag_count_for(length))
    return " ".join(parts)


def _build_flux1(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Write a highly accurate, objectively detailed description of this "
        "image suitable as a Flux.1 prompt.",
        "Use clear, descriptive prose — NOT tags.",
        "Focus on physical reality: anatomy, precise shapes, textures, and "
        "spatial relations.",
        "Avoid poetic language or storytelling. Be direct and descriptive.",
    ]
    nl_extras: list[str] = []
    if extras.get("includeLighting"):
        nl_extras.append("precise lighting details")
    if extras.get("includeCameraAngle"):
        nl_extras.append("exact camera angle")
    if extras.get("includeTechnicalDetails"):
        nl_extras.append("technical camera parameters")
    if extras.get("includeComposition"):
        nl_extras.append("composition style")
    if extras.get("includeDOF"):
        nl_extras.append("focal depth")
    if extras.get("includeLightSource"):
        nl_extras.append("light sources")
    if extras.get("includeAestheticQuality"):
        nl_extras.append("image quality")
    if nl_extras:
        parts.append(f"Include objective details about: {', '.join(nl_extras)}.")
    if extras.get("keepPG"):
        parts.append("Keep the description SFW.")
    if extras.get("includeUncensored"):
        parts.append(
            "Describe all anatomical details, including genitalia, sexual "
            "acts, and nudity, with explicit and anatomically correct "
            "terminology. Do not use euphemisms."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Do not describe unchangeable physical attributes.")
    if extras.get("excludeText"):
        parts.append("Ignore text in the image.")
    if extras.get("excludeResolution"):
        parts.append("Ignore resolution.")
    if extras.get("includeWatermark"):
        parts.append("Note if a watermark is present.")
    if extras.get("includeArtifacts"):
        parts.append("Note any image artifacts.")
    if extras.get("includeSafety"):
        parts.append("State the NSFW/SFW rating.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise, clinical language.")
    if length_instr:
        parts.append(length_instr)
    return " ".join(parts)


def _build_flux2(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Write a highly accurate, objectively detailed description of this "
        "image suitable as a Flux.2 prompt.",
        "Focus on physical reality: anatomy, skin texture, precise shapes, "
        "and spatial relations.",
        "Avoid poetic language, storytelling, or ambiguous metaphors. Be "
        "direct and descriptive.",
        "Describe human anatomy, body proportions, and physical features in "
        "extreme detail.",
    ]
    nl_extras: list[str] = []
    if extras.get("includeLighting"):
        nl_extras.append("precise lighting conditions")
    if extras.get("includeCameraAngle"):
        nl_extras.append("exact camera angle")
    if extras.get("includeComposition"):
        nl_extras.append("composition and framing")
    if extras.get("includeDOF"):
        nl_extras.append("focal depth")
    if extras.get("includeLightSource"):
        nl_extras.append("light sources")
    if extras.get("includeAestheticQuality"):
        nl_extras.append("image quality")
    if extras.get("includeTechnicalDetails"):
        nl_extras.append("technical camera parameters")
    if nl_extras:
        parts.append(f"Include objective details about: {', '.join(nl_extras)}.")
    if extras.get("keepPG"):
        parts.append("Keep the description SFW.")
    if extras.get("includeUncensored"):
        parts.append(
            "Describe all anatomical details, including genitalia, sexual "
            "acts, and nudity, with explicit and anatomically correct "
            "terminology. Do not use euphemisms."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Do not describe unchangeable physical attributes.")
    if extras.get("excludeText"):
        parts.append("Ignore text in the image.")
    if extras.get("excludeResolution"):
        parts.append("Ignore resolution.")
    if extras.get("includeWatermark"):
        parts.append("Note if a watermark is present.")
    if extras.get("includeArtifacts"):
        parts.append("Note any image artifacts.")
    if extras.get("includeSafety"):
        parts.append("State the NSFW/SFW rating.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise, clinical language.")
    if length_instr:
        parts.append(length_instr)
    return " ".join(parts)


def _build_zimage(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Write a detailed, objective natural language description of this "
        "image suitable as a Z-Image prompt.",
        "Focus on the physical subject, anatomy, environment, and specific "
        "lighting details.",
        "Be precise and descriptive. Avoid ambiguous or emotive language.",
    ]
    nl_extras: list[str] = []
    if extras.get("includeLighting"):
        nl_extras.append("lighting details")
    if extras.get("includeCameraAngle"):
        nl_extras.append("exact camera angle")
    if extras.get("includeTechnicalDetails"):
        nl_extras.append("technical details")
    if extras.get("includeComposition"):
        nl_extras.append("composition")
    if extras.get("includeDOF"):
        nl_extras.append("depth of field")
    if extras.get("includeLightSource"):
        nl_extras.append("light sources")
    if extras.get("includeAestheticQuality"):
        nl_extras.append("image quality")
    if nl_extras:
        parts.append(f"Include objective details about: {', '.join(nl_extras)}.")
    if extras.get("keepPG"):
        parts.append("Keep the description SFW.")
    if extras.get("includeUncensored"):
        parts.append(
            "Provide a fully explicit, anatomically correct description of "
            "nudity, genitalia, and sexual acts. Do not use euphemisms."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Do not describe unchangeable physical attributes.")
    if extras.get("excludeText"):
        parts.append("Ignore text in the image.")
    if extras.get("excludeResolution"):
        parts.append("Ignore resolution.")
    if extras.get("includeWatermark"):
        parts.append("Note if a watermark is present.")
    if extras.get("includeArtifacts"):
        parts.append("Note any image artifacts.")
    if extras.get("includeSafety"):
        parts.append("State the NSFW/SFW rating.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise, clinical language.")
    if length_instr:
        parts.append(length_instr)
    return " ".join(parts)


def _build_chroma(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Write a detailed, objective description of this image suitable as a "
        "Chroma prompt.",
        "Focus on precise colors, lighting, physical forms, and anatomy.",
        "Use clear, descriptive prose. Avoid subjective artistic " "interpretation.",
    ]
    nl_extras: list[str] = []
    if extras.get("includeLighting"):
        nl_extras.append("precise lighting and color temperature")
    if extras.get("includeCameraAngle"):
        nl_extras.append("camera perspective")
    if extras.get("includeComposition"):
        nl_extras.append("composition")
    if extras.get("includeDOF"):
        nl_extras.append("focal depth")
    if extras.get("includeLightSource"):
        nl_extras.append("light sources and their color")
    if extras.get("includeAestheticQuality"):
        nl_extras.append("image quality")
    if extras.get("includeTechnicalDetails"):
        nl_extras.append("technical details")
    if nl_extras:
        parts.append(f"Include objective details about: {', '.join(nl_extras)}.")
    if extras.get("keepPG"):
        parts.append("Keep it PG.")
    if extras.get("includeUncensored"):
        parts.append(
            "Describe all anatomical details, including genitalia and "
            "sexual acts, with explicit and anatomically correct "
            "terminology."
        )
    if extras.get("excludeStaticAttributes"):
        parts.append("Do not describe unchangeable physical attributes.")
    if extras.get("excludeText"):
        parts.append("Ignore text in the image.")
    if extras.get("excludeResolution"):
        parts.append("Ignore resolution.")
    if extras.get("includeWatermark"):
        parts.append("Note if a watermark is present.")
    if extras.get("includeArtifacts"):
        parts.append("Note any image artifacts.")
    if extras.get("includeSafety"):
        parts.append("State the NSFW/SFW rating.")
    if extras.get("noAmbiguity"):
        parts.append("Use precise language.")
    if length_instr:
        parts.append(length_instr)
    return " ".join(parts)


def _build_qwen(
    length: CaptionLength, length_instr: str, extras: dict[str, bool]
) -> str:
    parts = [
        "Provide a highly accurate, objective, and comprehensive description "
        "of this image suitable as a Qwen-Image prompt.",
        "Use complete sentences. Focus on physical reality, anatomy, and "
        "precise visual details.",
        "Avoid storytelling, mood, or interpretation.",
    ]
    # Append every applicable instruction verbatim from the option label table —
    # qwen handles long, dense prompts well.
    for key, (_short, full_text) in EXTRA_OPTION_LABELS.items():
        if extras.get(key):
            if key == "includeUncensored":
                parts.append(
                    "Describe all anatomy, nudity, and sexual acts in clear, "
                    "explicit, and anatomically correct terms."
                )
            else:
                parts.append(full_text)
    if length_instr:
        parts.append(length_instr)
    return " ".join(parts)


_BUILDERS: Final[
    dict[TargetModel, Callable[[CaptionLength, str, dict[str, bool]], str]]
] = {
    "sd": _build_sd,
    "pony": _build_pony,
    "flux1": _build_flux1,
    "flux2": _build_flux2,
    "zimage": _build_zimage,
    "chroma": _build_chroma,
    "qwen": _build_qwen,
}


# --- Per-target presets ---------------------------------------------------


class TargetPreset(NamedTuple):
    label: str
    prefix: str
    suffix: str
    supported_options: tuple[ExtraOption, ...]
    allowed_lengths: tuple[CaptionLength, ...]


_ALL_EXTRAS: Final[tuple[ExtraOption, ...]] = tuple(EXTRA_OPTION_LABELS.keys())
_ALL_LENGTHS: Final[tuple[CaptionLength, ...]] = (
    "Short",
    "Medium",
    "Long",
    "Descriptive (Longest)",
)
# Tag-based targets cap at "Long" — their builders only differentiate three
# tag-count buckets and "Descriptive (Longest)" would just duplicate "Long".
_TAG_LENGTHS: Final[tuple[CaptionLength, ...]] = ("Short", "Medium", "Long")


TARGET_PRESETS: Final[dict[TargetModel, TargetPreset]] = {
    "sd": TargetPreset(
        label="Stable Diffusion",
        prefix="",
        suffix=", high quality, masterwork",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_TAG_LENGTHS,
    ),
    "pony": TargetPreset(
        label="Pony (SDXL)",
        prefix="score_9, score_8_up, score_7_up, ",
        suffix=", rating_safe",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_TAG_LENGTHS,
    ),
    "flux1": TargetPreset(
        label="Flux 1",
        prefix="",
        suffix="",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_ALL_LENGTHS,
    ),
    "flux2": TargetPreset(
        label="Flux 2",
        prefix="",
        suffix="",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_ALL_LENGTHS,
    ),
    "zimage": TargetPreset(
        label="Z-Image",
        prefix="",
        suffix="",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_ALL_LENGTHS,
    ),
    "chroma": TargetPreset(
        label="Chroma",
        prefix="",
        suffix="",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_ALL_LENGTHS,
    ),
    "qwen": TargetPreset(
        label="Qwen Image",
        prefix="",
        suffix="",
        supported_options=_ALL_EXTRAS,
        allowed_lengths=_ALL_LENGTHS,
    ),
}


def _validate_length_for(target: TargetModel, length: CaptionLength) -> CaptionLength:
    """Return ``length`` if allowed for the target, else fall back to "Medium".

    Tag-based targets clamp "Descriptive (Longest)" to "Medium" (silent).
    """
    allowed = TARGET_PRESETS[target].allowed_lengths
    if length in allowed:
        return length
    return "Medium" if "Medium" in allowed else allowed[0]


# --- Composers ------------------------------------------------------------


def compose_generate_prompts(
    target_model: TargetModel,
    architecture: Architecture,
    extras: list[ExtraOption],
    caption_length: CaptionLength = "Medium",
) -> tuple[str, str]:
    """System + user prompts for a fresh generate-from-image request."""
    enabled = _enabled(extras)
    length = _validate_length_for(target_model, caption_length)
    length_instr = CAPTION_LENGTHS[length]
    builder = _BUILDERS[target_model]
    system = f"{builder(length, length_instr, enabled)} {_OUTPUT_RULE}"
    user = "Write a prompt that would generate this image."
    return system, user


def compose_transform_prompts(
    source_prompt: str,
    target_model: TargetModel,
    architecture: Architecture,
    extras: list[ExtraOption],
    caption_length: CaptionLength = "Medium",
) -> tuple[str, str]:
    """System + user prompts for rewriting an existing prompt for a new target."""
    enabled = _enabled(extras)
    length = _validate_length_for(target_model, caption_length)
    length_instr = CAPTION_LENGTHS[length]
    builder = _BUILDERS[target_model]
    system = (
        f"{builder(length, length_instr, enabled)} "
        f"Rewrite the supplied prompt to match these guidelines, preserving "
        f"the subject and key attributes. {_OUTPUT_RULE}"
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
    "ExtraOption",
    "CaptionLength",
    "CAPTION_LENGTHS",
    "EXTRA_OPTION_LABELS",
    "TargetPreset",
    "TARGET_PRESETS",
    "compose_generate_prompts",
    "compose_transform_prompts",
    "compose_clean_prompts",
]
