"""Training labels, time-decay weights and within-batch pairs for the taste
scorer. Pure: ``DatabaseManager.taste_training_raw`` reads the rows,
``build_label_set`` turns them into labels. Images only -- the DB layer drops
videos before they get here.

Only a grade is a label (spec 2026-10-24-pass-fail-grades, "Taste scorer"):
✓ pass -> 1, ✗ fail -> 0, no grade -> excluded. ★ favorites, views and
categories alone never are."""

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from metascan.core.judgments import (
    EVENT_GRADE_CLEAR,
    EVENT_GRADE_FAIL,
    EVENT_GRADE_PASS,
    Grade,
)

DEFAULT_HALF_LIFE_DAYS: float = 180.0
# A grade given while a score was on screen is partly an echo of the score.
SCORE_SHOWN_WEIGHT: float = 0.5
MIN_POSITIVES: int = 20
MIN_NEGATIVES: int = 20
RETRAIN_MIN_EVENTS: int = 25
RETRAIN_IDLE_S: int = 120
RETRAIN_CHECK_S: int = 30
# A model trained while more than this share of its labeled rows had no
# CLIP vector (a partial index) is due for retrain after the next
# embedding run.
MAX_NO_VECTOR_SHARE: float = 0.10

# The events whose ``score_shown`` applies to the grade they set.
GRADE_EVENT_KINDS: Tuple[str, ...] = (
    EVENT_GRADE_PASS,
    EVENT_GRADE_FAIL,
    EVENT_GRADE_CLEAR,
)

# What counts toward ``RETRAIN_MIN_EVENTS``: the events that change a label
# or a model input, on images only (``count_training_events_since``). Never
# ★ favorite / unfavorite (view events), seen, or the pre-grades verdict
# events left in the log.
TRAINING_EVENT_KINDS: Tuple[str, ...] = (
    *GRADE_EVENT_KINDS,
    "reason_on",
    "reason_off",
    "category_on",
    "category_off",
)

# The score_shown legacy mapping, read for one thing only: a grade the v6
# migration made from an old verdict has no grade event, so the halving
# that applied to that verdict still comes from the last legacy event that
# set it -- a "favorite" for ✓, a "pass" (the old *reject*) for ✗ -- at or
# before the row's judged_at. Later favorite / unfavorite events are view
# events and never count.
LEGACY_GRADE_EVENTS: Dict[Grade, str] = {"pass": "favorite", "fail": "pass"}

_SECONDS_PER_DAY = 86_400.0

logger = logging.getLogger(__name__)


@dataclass
class LabelRow:
    key: str
    file_path: str
    label: int
    weight: float
    # A deleted image: the worker may use only its own stored vector, never
    # the index's vector for its old path (another file may live there now).
    # Required: a default would make every row of a new source "live".
    tombstone: bool
    categories: List[str] = field(default_factory=list)
    batch_id: Optional[str] = None
    embedding: Optional[bytes] = None
    embedding_model: Optional[str] = None


@dataclass
class LabelSet:
    rows: List[LabelRow]
    pairs: List[Tuple[int, int, float]]
    n_pos: int
    n_neg: int
    as_of: str


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def decay_weight(rated_at: str, now: datetime, half_life_days: float) -> float:
    """``0.5 ** (age_days / half_life_days)``; a future timestamp is age 0."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - _parse(rated_at)).total_seconds() / _SECONDS_PER_DAY)
    return float(0.5 ** (age_days / half_life_days))


def _label_for(row: Dict[str, Any]) -> Optional[int]:
    """✓ pass -> 1, ✗ fail -> 0, anything else (no grade) -> None."""
    grade = row.get("grade")
    if grade == "pass":
        return 1
    if grade == "fail":
        return 0
    return None


def _label_row(
    row: Dict[str, Any],
    key: str,
    now: datetime,
    half_life_days: float,
    *,
    tombstone: bool,
) -> Tuple[Optional[LabelRow], bool]:
    """``(row, skipped)``; ``skipped`` is True for a labeled row dropped
    because its timestamp is empty or unparseable. The clock runs from the
    last time the grade was set or confirmed; ``grade_score_shown`` (the
    score on screen then, see ``LEGACY_GRADE_EVENTS``) halves the weight.
    ``tombstone`` (required) says whether ``row`` is a deleted image."""
    label = _label_for(row)
    if label is None:
        return None, False
    rated_at = row.get("confirmed_at") or row.get("judged_at")
    if not rated_at:
        return None, True
    try:
        weight = decay_weight(rated_at, now, half_life_days)
    except ValueError:
        return None, True
    if row.get("grade_score_shown") is not None:
        weight *= SCORE_SHOWN_WEIGHT
    return (
        LabelRow(
            key=key,
            file_path=row["file_path"],
            label=label,
            weight=weight,
            categories=list(row.get("categories") or []),
            batch_id=row.get("t2i_batch_id") or row.get("batch_id"),
            embedding=row.get("embedding"),
            embedding_model=row.get("embedding_model"),
            tombstone=tombstone,
        ),
        False,
    )


def _labeled_rows(
    raw: Dict[str, Any], now: datetime, half_life_days: float, warn: bool = True
) -> List[LabelRow]:
    """Every labeled row: live images, then the newest graded tombstone per
    path whose path has no labeled live row. ``warn`` logs skipped rows."""
    rows: List[LabelRow] = []
    skipped = 0
    for m in raw.get("media", []):
        lr, bad = _label_row(m, m["file_path"], now, half_life_days, tombstone=False)
        skipped += bad
        if lr is not None:
            rows.append(lr)
    # A re-scanned path has a live row plus its old tombstone(s): a graded
    # live row wins, and among the graded tombstones only the newest
    # survives. An ungraded row (seen, ★ or a category only) hides nothing:
    # viewing a re-added copy must not drop the grade it was deleted with.
    live_paths = {r.file_path for r in rows}
    newest: Dict[str, Dict[str, Any]] = {}
    for t in raw.get("tombstones", []):
        if _label_for(t) is None:
            continue
        cur = newest.get(t["file_path"])
        if cur is None or t["id"] > cur["id"]:
            newest[t["file_path"]] = t
    for t in sorted(newest.values(), key=lambda t: t["id"]):
        if t["file_path"] in live_paths:
            continue
        lr, bad = _label_row(
            t, f"tombstone:{t['id']}", now, half_life_days, tombstone=True
        )
        skipped += bad
        if lr is not None:
            rows.append(lr)
    if skipped and warn:
        logger.warning(
            "taste labels: skipped %d labeled row(s) with an empty or "
            "unparseable timestamp",
            skipped,
        )
    return rows


def count_labels(raw: Dict[str, Any], now: datetime) -> Tuple[int, int]:
    """``(n_pos, n_neg)`` exactly as ``build_label_set`` would count them;
    ``raw`` may be the slimmer ``DatabaseManager.taste_label_counts_raw``."""
    # Polled by status and the retrain loop: training logs the skips.
    rows = _labeled_rows(raw, now, DEFAULT_HALF_LIFE_DAYS, warn=False)
    n_pos = sum(1 for r in rows if r.label == 1)
    return n_pos, len(rows) - n_pos


def build_label_set(
    raw: Dict[str, Any], now: datetime, half_life_days: float
) -> LabelSet:
    """Labels plus the within-T2I-batch pairs: every ✓ of a batch against
    every ✗ of the same batch, weighted by the lighter of the two."""
    rows = _labeled_rows(raw, now, half_life_days)
    by_batch: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        if r.batch_id:
            by_batch.setdefault(r.batch_id, []).append(i)
    pairs: List[Tuple[int, int, float]] = []
    for idxs in by_batch.values():
        pos = [i for i in idxs if rows[i].label == 1]
        neg = [i for i in idxs if rows[i].label == 0]
        for p in pos:
            for n in neg:
                pairs.append((p, n, min(rows[p].weight, rows[n].weight)))

    n_pos = sum(1 for r in rows if r.label == 1)
    return LabelSet(
        rows=rows,
        pairs=pairs,
        n_pos=n_pos,
        n_neg=len(rows) - n_pos,
        as_of=now.isoformat(),
    )


def missing_for_training(n_pos: int, n_neg: int) -> Optional[Dict[str, int]]:
    """``None`` when there is enough to train, else how many more of each."""
    need_pos = max(0, MIN_POSITIVES - n_pos)
    need_neg = max(0, MIN_NEGATIVES - n_neg)
    if need_pos == 0 and need_neg == 0:
        return None
    return {"positives": need_pos, "negatives": need_neg}


# ----------------------------------------------------------------------
# Writer stats / diversity windows
# ----------------------------------------------------------------------

WRITER_WINDOW_DAYS: int = 14
WRITER_WINDOWS: int = 6
# Taste collapse: the newest ✓ pass diversity falls below this share of the
# median of the previous COLLAPSE_PREV_WINDOWS windows while the pass rate
# rises.
COLLAPSE_RATIO: float = 0.75
COLLAPSE_PREV_WINDOWS: int = 5
# The latest window needs at least this many ✓ T2I images with a vector: a
# mean distance over two or three is noise, not a collapse.
COLLAPSE_MIN_PASSES: int = 5


def window_starts(
    now: datetime,
    window_days: int = WRITER_WINDOW_DAYS,
    windows: int = WRITER_WINDOWS,
) -> List[str]:
    """ISO dates (UTC) of the last ``windows`` ``window_days``-day windows,
    oldest first. The newest window ends today (inclusive)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(timezone.utc).date()
    latest = today - timedelta(days=window_days - 1)
    return [
        (latest - timedelta(days=window_days * k)).isoformat()
        for k in reversed(range(windows))
    ]


def window_for(
    created_at: Optional[str], starts: List[str], window_days: int
) -> Optional[str]:
    """The entry of ``starts`` whose window holds ``created_at`` (UTC; a
    naive timestamp is UTC, as SQLite's ``datetime('now')`` writes it), or
    None when it is outside every window or unparseable."""
    if not created_at or not starts:
        return None
    try:
        day = _parse(str(created_at).strip()).astimezone(timezone.utc).date()
    except ValueError:
        return None
    first = date.fromisoformat(starts[0])
    offset = (day - first).days
    if offset < 0:
        return None
    idx = offset // window_days
    return starts[idx] if idx < len(starts) else None


def _pooled_t2i_rates(windows: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Pass rate (✓ of graded) per window over every T2I writer, oldest
    first; None for a window with nothing graded."""
    n_graded: Dict[str, int] = {}
    n_pass: Dict[str, int] = {}
    for w in windows:
        if (w.get("kind") or "t2i") != "t2i":
            continue
        start = str(w["window_start"])
        n_graded[start] = n_graded.get(start, 0) + int(w.get("n_graded") or 0)
        n_pass[start] = n_pass.get(start, 0) + int(w.get("n_pass") or 0)
    return [n_pass[s] / n_graded[s] if n_graded[s] else None for s in sorted(n_graded)]


def collapse_warning(
    diversity: List[Dict[str, Any]],
    windows: List[Dict[str, Any]],
    window_days: int = WRITER_WINDOW_DAYS,
) -> bool:
    """Taste collapse: the latest window's ✓ pass diversity is below
    ``COLLAPSE_RATIO`` x the median of the previous up-to-5 windows (those
    with a value) **and** the latest pooled T2I pass rate is above the
    previous window's, and the latest window holds at least
    ``COLLAPSE_MIN_PASSES`` ✓ images with a vector. Diversity is stored
    per training run with that run's window starts while rates slide with
    today: unless the latest diversity window starts within
    ``window_days`` of the latest rate window they describe different
    periods, so no flag. False whenever either side lacks the data."""
    div = sorted(diversity, key=lambda d: str(d["window_start"]))
    if not div or div[-1].get("mean_distance") is None:
        return False
    if int(div[-1].get("n") or 0) < COLLAPSE_MIN_PASSES:
        return False
    rate_starts = sorted(
        {str(w["window_start"]) for w in windows if (w.get("kind") or "t2i") == "t2i"}
    )
    if not rate_starts:
        return False
    try:
        gap = (
            date.fromisoformat(str(div[-1]["window_start"]))
            - date.fromisoformat(rate_starts[-1])
        ).days
    except ValueError:
        return False
    if abs(gap) >= window_days:
        return False
    latest = float(div[-1]["mean_distance"])
    prev = [
        float(d["mean_distance"])
        for d in div[-1 - COLLAPSE_PREV_WINDOWS : -1]
        if d.get("mean_distance") is not None
    ]
    if not prev or not latest < COLLAPSE_RATIO * statistics.median(prev):
        return False
    rates = _pooled_t2i_rates(windows)
    if len(rates) < 2 or rates[-1] is None or rates[-2] is None:
        return False
    return rates[-1] > rates[-2]
