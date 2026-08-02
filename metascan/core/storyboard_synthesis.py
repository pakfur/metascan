"""Panel brief -> target-model prompt dialect. Pure: prompt text only;
the VLM call lives in storyboard_runner.

The verbatim-descriptor rule is consistency mechanic #1 (spec §7):
paraphrase drift on subject descriptions is the main reason
panel-to-panel identity decays, so both dialect instructions forbid it.
"""

from __future__ import annotations

from typing import Optional

TAG_STYLE_TARGETS: frozenset[str] = frozenset({"sd", "pony"})

_TAG_STYLE_SYSTEM = """\
You convert a storyboard panel brief into a prompt for an SDXL-class
image model. Output one comma-separated, tag-style prompt.

Rules:
- Carry every SUBJECT description through verbatim, word for word.
  Never paraphrase, shorten, reorder, or embellish subject descriptors.
- Translate the SHOT line into composition tags (framing, camera angle,
  lens character).
- Include the location, lighting, and mood as tags.
- Describe exactly the moment in ACTION — one frame, not a sequence.
- Output only the prompt text: no quotes, no labels, no explanations.
"""

_NATURAL_SYSTEM = """\
You convert a storyboard panel brief into a prompt for a
natural-language image model. Write one flowing paragraph of two to
five sentences describing exactly the keyframe.

Rules:
- Carry every SUBJECT description through verbatim, word for word.
  Never paraphrase, shorten, reorder, or embellish subject descriptors.
- Establish the framing and camera angle from the SHOT line in the
  first sentence.
- Weave the location, lighting, and mood into the description.
- Describe exactly the moment in ACTION — one frame, not a sequence.
- Output only the prompt text: no quotes, no labels, no explanations.
"""

_USER_TEMPLATE = "Brief:\n{brief}\n\nWrite the image prompt."


def build_render_messages(brief: str, target_model: str) -> tuple[str, str]:
    system = _TAG_STYLE_SYSTEM if target_model in TAG_STYLE_TARGETS else _NATURAL_SYSTEM
    return system, _USER_TEMPLATE.format(brief=brief)


def finalize_prompt(text: str, style_block: Optional[str]) -> str:
    """Concatenate the storyboard's style block outside the LLM call so
    the global look cannot drift through paraphrase (spec §7.2)."""
    out = text.strip()
    style = (style_block or "").strip()
    return f"{out}, {style}" if style else out
