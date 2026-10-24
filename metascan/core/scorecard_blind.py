"""Server-side blinding of writer-adapter scorecard images.

A scorecard image (``t2i_images.scorecard_id`` set) is judged blind until
it has an explicit verdict (favorite or pass): base vs adapter must not be
readable from anything the UI fetches. The UI hides those fields too; this
is the defence in depth -- the payloads simply do not carry them. Kept is
what a tile or the viewer needs to render the image (path, size, the seed
and model, which both arms share) plus ``scorecard_id`` /
``scorecard_blind``. After a verdict the next fetch is the full record.

Pure functions over JSON-ready dicts; each mutates and returns its argument.
"""

from __future__ import annotations

from typing import Any, Dict, MutableMapping

# ``t2i_images`` columns that reveal the writer or its prompt.
BLIND_T2I_FIELDS = (
    "prompt_used",
    "negative_used",
    "writer_id",
    "writer_model",
    "variant_group",
    "variant_index",
    "draft_prompt",
    "edited",
    "resolved_caption",
    "direction",
    "prompt_categories",
)

# ``form_state`` keys that carry the prompt.
BLIND_FORM_FIELDS = ("prompt", "negative")

# ``batch_step`` frame / listing keys that reveal the writer or its prompt.
BLIND_STEP_FIELDS = (
    "prompt",
    "negative",
    "draft",
    "writer_id",
    "writer_model",
    "variant_index",
    "lineage",
    "direction",
    "direction_parts",
    "warnings",
)

# ``GET /api/media/{path}`` keys that carry the prompt.
BLIND_MEDIA_FIELDS = ("prompt", "negative_prompt")


def blind_t2i_row(row: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Strip a blind scorecard ``t2i_images`` row (``scorecard_blind``)."""
    if not row.get("scorecard_blind"):
        return row
    for key in BLIND_T2I_FIELDS:
        if key in row:
            row[key] = None
    form = row.get("form_state")
    if isinstance(form, dict):
        for key in BLIND_FORM_FIELDS:
            if key in form:
                form[key] = "" if key == "prompt" else None
    return row


def blind_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """Strip a scorecard batch's step frame (always: it has no verdict)."""
    for key in BLIND_STEP_FIELDS:
        if key in step:
            step[key] = None
    return step


def blind_media_detail(detail: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Strip a blind media detail record (``scorecard_blind``): no prompt and
    no tags (prompt tags would give the wording away)."""
    if not detail.get("scorecard_blind"):
        return detail
    for key in BLIND_MEDIA_FIELDS:
        if key in detail:
            detail[key] = None
    detail["tags"] = []
    return detail


__all__ = [
    "BLIND_FORM_FIELDS",
    "BLIND_MEDIA_FIELDS",
    "BLIND_STEP_FIELDS",
    "BLIND_T2I_FIELDS",
    "blind_media_detail",
    "blind_step",
    "blind_t2i_row",
]
