"""Recognising a moved or renamed file (spec section 11.1).

Pure: no database, no filesystem. The caller reads a ``Candidate`` for every
file that vanished (``db.candidate_for``) and for every settled new file, and
asks ``match_moves`` which new file is which vanished one. Same-looking
duplicates are the failure this guards against, so ambiguity is always "new
file": a vanished candidate that more than one new file could be, or a new
file with more than one candidate, is not a move.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

# FAT and exFAT round mtimes to 2 s.
MTIME_TOLERANCE_S = 2.0
# A vanished row waits this long for its new path before it is left to hygiene.
MOVE_GRACE_S = 60.0


@dataclass(frozen=True)
class Candidate:
    path: str
    size: int
    mtime: Optional[float]
    phash: Optional[str] = None


def parse_mtime(raw: Any) -> Optional[float]:
    """``modified_at`` as epoch seconds, from either historical shape: a unix
    epoch (a number, or a float stringified) or an ISO-8601 / SQL timestamp.
    None when it is neither."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


# The scanner stores a naive local timestamp; one in the repeated autumn hour
# reads back an hour off. An hour apart (within the tolerance) is the same time.
DST_SHIFT_S = 3600.0


def mtimes_equal(a: Optional[float], b: Optional[float]) -> bool:
    """Two modification times that are the same moment: within the 2 s
    tolerance, or an hour apart within it (a daylight-saving fold)."""
    if a is None or b is None:
        return False
    d = abs(a - b)
    return d <= MTIME_TOLERANCE_S or abs(d - DST_SHIFT_S) <= MTIME_TOLERANCE_S


def _near(a: Optional[float], b: Optional[float]) -> bool:
    return mtimes_equal(a, b)


def _phash_conflict(a: Candidate, b: Candidate) -> bool:
    return bool(a.phash and b.phash and a.phash != b.phash)


def _pick(new: Candidate, vanished: Sequence[Candidate]) -> Optional[Candidate]:
    same_size = [v for v in vanished if v.size == new.size]
    # Rule 1: same size and mtime, exactly one candidate, phash (if both) agrees.
    close = [v for v in same_size if _near(v.mtime, new.mtime)]
    if len(close) > 1:
        return None
    if len(close) == 1:
        return None if _phash_conflict(new, close[0]) else close[0]
    # Rule 2: same size, a different mtime: only the phash can say, and only
    # with exactly one candidate.
    if len(same_size) == 1 and new.phash and same_size[0].phash == new.phash:
        return same_size[0]
    return None


def match_moves(
    new: Sequence[Candidate], vanished: Sequence[Candidate]
) -> List[Tuple[str, str]]:
    """``(old, new)`` path pairs that are moves, per spec 11.1. A vanished
    file two new files could each be is nobody's move."""
    picked: Dict[str, List[Candidate]] = {}
    for n in new:
        v = _pick(n, vanished)
        if v is not None:
            picked.setdefault(v.path, []).append(n)
    return [(old, ns[0].path) for old, ns in picked.items() if len(ns) == 1]
