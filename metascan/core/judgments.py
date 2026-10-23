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
