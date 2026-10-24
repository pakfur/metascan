"""Constants and value types for taste-rating capture (judgments)."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

REASONS: Tuple[str, ...] = (
    "realism",
    "composition",
    "emotion",
    "light",
    "concept",
    "motion",
)

Verdict = Literal["favorite", "pass"]

# Pass / fail grades (spec 2026-10-24-pass-fail-grades): the only training
# signal, independent of ★ favorite. Stored in ``grade`` columns (NULL = no
# grade). Never reuse the words pass / passed / unpass / verdict for the new
# ✓: in every pre-v6 column, event and snapshot they mean *reject*.
Grade = Literal["pass", "fail"]
GRADES: Tuple[Grade, ...] = ("pass", "fail")
EVENT_GRADE_PASS = "grade_pass"
EVENT_GRADE_FAIL = "grade_fail"
EVENT_GRADE_CLEAR = "grade_clear"
# The old favorite-or-pass verdict's reject events ("pass" = rejected,
# "unpass" = reject withdrawn). They stay in the append-only log and are
# never read as a grade.
LEGACY_REJECT_EVENTS: Tuple[str, ...] = ("pass", "unpass")

# Minimum gap between two reconfirm events on the same item.
RECONFIRM_MIN_INTERVAL_S: int = 86_400

# Dwell time before an item counts as seen. Mirrored in the frontend.
SEEN_DWELL_MS: int = 1000

# Lineage writer ids on generated media.
WRITER_BASE = "base"
# The base writer with up to TASTE_EXAMPLES_K favorite prompts as examples.
WRITER_BASE_TASTE = "base+taste"
WRITER_FALLBACK = "fallback"
WRITER_MANUAL = "manual"
# Writers a supplied draft may name (a typed-from-scratch prompt is MANUAL).
DRAFT_WRITERS: Tuple[str, ...] = (WRITER_BASE, WRITER_BASE_TASTE, WRITER_FALLBACK)


# Writers whose drafts come from a VLM, so the lineage names its model.
MODEL_WRITERS: Tuple[str, ...] = (WRITER_BASE, WRITER_BASE_TASTE)


class WriterModelError(ValueError):
    """A supplied ``writer_model`` names no model the writer role can use."""


def writer_id_allowed(
    writer_id: Any, db: Any = None, kind: Optional[str] = None
) -> bool:
    """True when ``writer_id`` may be named by a supplied draft: a
    ``DRAFT_WRITERS`` id, or ``adapter:<id>`` of a registry adapter that is
    ``ready`` / ``untested`` (``db`` is needed to look it up) whose ``kind``
    matches the surface asking (``t2i`` / ``i2v``) when ``kind`` is given. A training,
    failed or deleted adapter is refused for new drafts."""
    if isinstance(writer_id, str) and writer_id in DRAFT_WRITERS:
        return True
    return _servable_adapter(writer_id, db, kind) is not None


def _servable_adapter(
    writer_id: Any, db: Any, kind: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    from metascan.core.writer_adapters import (
        SERVABLE_STATUSES,
        parse_adapter_writer_id,
    )

    adapter_id = parse_adapter_writer_id(writer_id)
    if adapter_id is None or db is None:
        return None
    row: Optional[Dict[str, Any]] = db.get_writer_adapter(adapter_id)
    if row is None or row["status"] not in SERVABLE_STATUSES:
        return None
    if kind is not None and row["kind"] != kind:
        return None
    return row


def check_writer_model(
    writer_id: Optional[str],
    writer_model: Any,
    db: Any = None,
    kind: Optional[str] = None,
) -> Optional[str]:
    """The ``writer_model`` to record for a supplied prompt. Only a VLM
    writer (``base`` / ``base+taste``) has one: ``manual`` and ``fallback``
    record None whatever was sent. A VLM writer's model must be a
    registered model the writer role may use; None (an older client, a
    draft from before the column) is kept as unknown rather than guessed,
    so it can never mix into a model's baseline."""
    from metascan.core.writer_adapters import parse_adapter_writer_id

    if parse_adapter_writer_id(writer_id) is not None:
        # An adapter drafts on its base model only; the lineage must say so.
        adapter = _servable_adapter(writer_id, db, kind)
        if adapter is None:
            raise WriterModelError(f"writer {writer_id!r} is not a usable adapter")
        if writer_model != adapter["base_model"]:
            raise WriterModelError(
                f"writer_model {writer_model!r} is not the base model of "
                f"{writer_id!r} ({adapter['base_model']})"
            )
        return str(adapter["base_model"])
    if writer_id not in MODEL_WRITERS or writer_model is None:
        return None
    from metascan.core.vlm_models import writer_allowed

    if not isinstance(writer_model, str) or not writer_allowed(writer_model):
        raise WriterModelError(
            f"writer_model {writer_model!r} is not a model the prompt writer " "can use"
        )
    return writer_model


def normalize_prompt(text: str) -> str:
    """``text`` with every run of whitespace collapsed to one space and the
    ends trimmed: a draft and a rendered prompt that differ only in spacing
    or line breaks are not an edit."""
    return " ".join(text.split())


CATEGORY_SOURCES: Tuple[str, ...] = ("viewer", "sticky", "generation", "bulk")
CATEGORY_NAME_MAX = 40


@dataclass
class CategoryRef:
    id: int
    name: str


@dataclass
class JudgmentState:
    file_path: str
    verdict: Optional[Verdict]
    reasons: List[str] = field(default_factory=list)
    seen_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    is_favorite: bool = False
    categories: List[CategoryRef] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "seen_at": self.seen_at,
            "confirmed_at": self.confirmed_at,
            "is_favorite": self.is_favorite,
            "categories": [
                {"id": c.id, "name": c.name}
                for c in sorted(self.categories, key=lambda c: c.name)
            ],
        }
