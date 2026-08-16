"""Per-video-target constants shared by story composition and compilers.

The shot-duration cap is the one deliberate coupling between story
composition and the video dialect (spec V3 §10.3): H3 generates at most
~15 s per clip, so the shots stage and the beats editor take their
duration guidance from here rather than hardcoding.
"""

from __future__ import annotations

from typing import Optional

TARGET_CAPS: dict[str, float] = {"minimax": 15.0}
DEFAULT_SHOT_CAP: float = 15.0


def shot_cap(video_target: Optional[str]) -> float:
    """Max recommended shot duration (seconds) for a video target."""
    if video_target is None:
        return DEFAULT_SHOT_CAP
    return TARGET_CAPS.get(video_target, DEFAULT_SHOT_CAP)
