"""Pure storyboard domain helpers: dimension bucketing, seeds, briefs.

No I/O, no model calls — the photo_exif precedent. Everything here is
exercised by tests that need no server.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("1:1", "4:3", "16:9", "2.39:1", "9:16")

# SDXL-family targets snap to the model's trained buckets; everything
# else (Flux and later) takes the nearest multiple of 16 at a ~1MP budget.
SDXL_TARGETS: frozenset[str] = frozenset({"sd", "pony"})

_SDXL_BUCKETS: tuple[tuple[int, int], ...] = (
    (1024, 1024),
    (1216, 832),
    (832, 1216),
    (1344, 768),
    (768, 1344),
)
_PIXEL_BUDGET = 1024 * 1024

SHOT_SIZES: Mapping[str, str] = {
    "ECU": "extreme close-up",
    "CU": "close-up",
    "MCU": "medium close-up",
    "MS": "medium shot",
    "MLS": "medium long shot",
    "WS": "wide shot",
    "EWS": "extreme wide shot",
}
ANGLES: Mapping[str, str] = {
    "eye": "eye level",
    "low": "low angle",
    "high": "high angle",
    "overhead": "overhead",
    "dutch": "dutch angle",
    "ots": "over-the-shoulder",
    "pov": "POV",
}
LENSES: Mapping[str, str] = {
    "wide": "wide-angle lens",
    "normal": "normal lens",
    "tele": "telephoto lens",
    "macro": "macro lens",
}
COMPOSITIONS: Mapping[str, str] = {
    "thirds_left": "subject on the left third",
    "thirds_right": "subject on the right third",
    "centered": "centered composition",
    "symmetrical": "symmetrical composition",
    "negative_space": "negative space composition",
    "frame_in_frame": "frame-within-frame composition",
    "leading_lines": "leading lines composition",
    "deep_staging": "deep staging, layered depth",
}
LIGHT_QUALITIES: Mapping[str, str] = {
    "hard": "hard light",
    "soft": "soft diffused light",
    "dappled": "dappled light",
    "practical": "practical light sources",
    "window": "motivated window light",
    "firelight": "firelight",
    "ambient": "ambient light",
}


def _aspect_value(aspect_ratio: str) -> float:
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(
            f"Unsupported aspect ratio {aspect_ratio!r}; "
            f"supported: {', '.join(SUPPORTED_ASPECT_RATIOS)}"
        )
    w_s, h_s = aspect_ratio.split(":")
    return float(w_s) / float(h_s)


def bucket_dims(aspect_ratio: str, target_model: str) -> tuple[int, int]:
    """Snap an aspect ratio to generation dimensions for a target model."""
    ar = _aspect_value(aspect_ratio)
    if target_model in SDXL_TARGETS:
        return min(_SDXL_BUCKETS, key=lambda wh: abs(math.log(wh[0] / wh[1] / ar)))
    w = round(math.sqrt(_PIXEL_BUDGET * ar) / 16) * 16
    h = round(math.sqrt(_PIXEL_BUDGET / ar) / 16) * 16
    return (int(w), int(h))


def beat_seed(
    base_seed: int, panel_sort_order: int, beat_sort_order: int, variant_index: int
) -> int:
    """Deterministic per-beat seed; a reroll advances variant_index.
    Collision-free for < 100 beats/shot and < 1000 variants/beat."""
    return base_seed + (panel_sort_order * 100 + beat_sort_order) * 1000 + variant_index


def storyboard_slug(storyboard_id: int, name: str) -> str:
    """Filesystem-safe directory name: '<id>-<kebab-name>'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return f"{storyboard_id}-{slug or 'storyboard'}"


def compose_brief(
    storyboard: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    beat: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> str:
    """Deterministic beat brief. The style block is deliberately absent:
    it is concatenated onto the final prompt outside the LLM call so the
    global look cannot drift through paraphrase (spec §7.2)."""
    shot_bits = [
        SHOT_SIZES.get(beat.get("shot_size") or ""),
        ANGLES.get(beat.get("angle") or ""),
        LENSES.get(beat.get("lens") or ""),
        COMPOSITIONS.get(beat.get("composition") or ""),
        storyboard.get("aspect_ratio"),
    ]
    lines = [f"SHOT: {', '.join(b for b in shot_bits if b)}"]
    for subj in subjects:
        lines.append(f"SUBJECT {subj['name']}: {subj['description']}")
    if beat.get("action"):
        lines.append(f"ACTION: {beat['action']}")
    if panel.get("action"):
        lines.append(f"SHOT CONTEXT: {panel['action']}")
    if beat.get("emotional_intent"):
        lines.append(f"PERFORMANCE: {beat['emotional_intent']}")
    if scene.get("setting"):
        lines.append(f"SETTING: {scene['setting']}")
    if scene.get("location"):
        lines.append(f"LOCATION: {scene['location']}")
    light_bits = [
        LIGHT_QUALITIES.get(beat.get("light_quality") or ""),
        scene.get("time_of_day"),
        scene.get("lighting"),
        scene.get("mood"),
    ]
    light = ", ".join(b for b in light_bits if b)
    if light:
        lines.append(f"LIGHT/MOOD: {light}")
    return "\n".join(lines)
