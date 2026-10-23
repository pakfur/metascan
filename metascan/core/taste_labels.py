"""Training labels, time-decay weights and within-batch pairs for the taste
scorer. Pure: ``DatabaseManager.taste_training_raw`` reads the rows,
``build_label_set`` turns them into labels. Images only -- the DB layer drops
videos before they get here."""

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_HALF_LIFE_DAYS: float = 180.0
# A verdict given while a score was on screen is partly an echo of the score.
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

TRAINING_EVENT_KINDS: Tuple[str, ...] = (
    "favorite",
    "unfavorite",
    "pass",
    "unpass",
    "reason_on",
    "reason_off",
    "category_on",
    "category_off",
)

_SECONDS_PER_DAY = 86_400.0

logger = logging.getLogger(__name__)


@dataclass
class LabelRow:
    key: str
    file_path: str
    label: int
    weight: float
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
    if row.get("is_favorite"):
        return 1
    if row.get("passed") or row.get("seen_at"):
        return 0
    return None


def _label_row(
    row: Dict[str, Any], key: str, now: datetime, half_life_days: float
) -> Tuple[Optional[LabelRow], bool]:
    """``(row, skipped)``; ``skipped`` is True for a labeled row dropped
    because its timestamp is empty or unparseable."""
    label = _label_for(row)
    if label is None:
        return None, False
    rated_at = row.get("confirmed_at") or row.get("judged_at") or row.get("seen_at")
    if not rated_at:
        return None, True
    try:
        weight = decay_weight(rated_at, now, half_life_days)
    except ValueError:
        return None, True
    shown = row.get("last_verdict_score_shown")
    if shown is None and not row.get("is_favorite") and not row.get("passed"):
        # Seen-only negative: the score on screen at dwell time anchors it
        # just as it would a verdict.
        shown = row.get("seen_score_shown")
    if shown is not None:
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
        ),
        False,
    )


def _labeled_rows(
    raw: Dict[str, Any], now: datetime, half_life_days: float, warn: bool = True
) -> List[LabelRow]:
    """Every labeled row: live images, then the newest tombstone per path
    whose path has no labeled live row. ``warn`` logs skipped rows."""
    rows: List[LabelRow] = []
    skipped = 0
    for m in raw.get("media", []):
        lr, bad = _label_row(m, m["file_path"], now, half_life_days)
        skipped += bad
        if lr is not None:
            rows.append(lr)
    # A re-scanned path has a live row plus its old tombstone(s): live wins,
    # and among tombstones only the newest survives.
    live_paths = {r.file_path for r in rows}
    newest: Dict[str, Dict[str, Any]] = {}
    for t in raw.get("tombstones", []):
        cur = newest.get(t["file_path"])
        if cur is None or t["id"] > cur["id"]:
            newest[t["file_path"]] = t
    for t in sorted(newest.values(), key=lambda t: t["id"]):
        if t["file_path"] in live_paths:
            continue
        lr, bad = _label_row(t, f"tombstone:{t['id']}", now, half_life_days)
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
# Taste collapse: the newest favorite diversity falls below this share of
# the median of the previous COLLAPSE_PREV_WINDOWS windows while the
# favorite rate rises.
COLLAPSE_RATIO: float = 0.75
COLLAPSE_PREV_WINDOWS: int = 5


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
    """Favorite rate per window over every T2I writer, oldest first."""
    seen: Dict[str, int] = {}
    fav: Dict[str, int] = {}
    for w in windows:
        if (w.get("kind") or "t2i") != "t2i":
            continue
        start = str(w["window_start"])
        seen[start] = seen.get(start, 0) + int(w.get("n_seen") or 0)
        fav[start] = fav.get(start, 0) + int(w.get("n_fav") or 0)
    return [fav[s] / seen[s] if seen[s] else None for s in sorted(seen)]


def collapse_warning(
    diversity: List[Dict[str, Any]],
    windows: List[Dict[str, Any]],
    window_days: int = WRITER_WINDOW_DAYS,
) -> bool:
    """Taste collapse: the latest window's favorite diversity is below
    ``COLLAPSE_RATIO`` x the median of the previous up-to-5 windows (those
    with a value) **and** the latest pooled T2I favorite rate is above the
    previous window's. Diversity is stored per training run with that run's
    window starts while rates slide with today: unless the latest diversity
    window starts within ``window_days`` of the latest rate window they
    describe different periods, so no flag. False whenever either side
    lacks the data."""
    div = sorted(diversity, key=lambda d: str(d["window_start"]))
    if not div or div[-1].get("mean_distance") is None:
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
