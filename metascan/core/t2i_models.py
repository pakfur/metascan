"""Model profiles for the t2i dialog (spec section 5.1). Pure: no I/O.

A profile ties a dialog model id to the guideline the VLM follows when it
writes that model's prompt, whether the model takes a negative prompt, how
later mentions of a character read by default, the size grid its latents
need and the token budget for the VLM's reply.

The shared ``TargetModel`` literal (storyboard and Prompt Playground) is
deliberately not extended: profiles reach the prompt YAML through the prompt
store using ``meta_key`` directly. ``max_tokens`` values are starting points,
tunable here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from metascan.core.t2i_characters import IDENTITY_STYLES


@dataclass(frozen=True)
class T2iModelProfile:
    id: str
    label: str
    meta_key: str  # the guideline's key in data/meta_prompt.yml
    has_negative: bool
    identity: str  # default identity style: ref | noun | name
    dim_multiple: int  # both edges of the latent must be a multiple of this
    max_tokens: int  # token budget for the VLM's reply


MODEL_PROFILES: Dict[str, T2iModelProfile] = {
    "krea2": T2iModelProfile("krea2", "Krea 2", "META_KREA2", False, "ref", 16, 420),
    "qwen": T2iModelProfile("qwen", "Qwen-Image", "META_QWEN", True, "ref", 16, 320),
    "sd": T2iModelProfile("sd", "SDXL", "META_SDXL", True, "noun", 64, 480),
    "zimage": T2iModelProfile(
        "zimage", "Z-Image", "META_ZIMAGE", False, "ref", 16, 280
    ),
}


class T2iModelError(KeyError):
    """An unknown t2i model id."""

    def __str__(self) -> str:  # KeyError would show the repr, quotes and all
        return str(self.args[0]) if self.args else ""


def get_profile(model_id: str) -> T2iModelProfile:
    try:
        return MODEL_PROFILES[model_id]
    except KeyError:
        known = ", ".join(MODEL_PROFILES)
        raise T2iModelError(
            f"unknown t2i model {model_id!r}; known models: {known}"
        ) from None


def effective_identity(profile: T2iModelProfile, overrides: Mapping[str, str]) -> str:
    """The identity style for ``profile``: a valid ``t2i.identity`` override
    from config wins, anything else falls back to the profile default."""
    override = overrides.get(profile.id)
    if isinstance(override, str) and override in IDENTITY_STYLES:
        return override
    return profile.identity
