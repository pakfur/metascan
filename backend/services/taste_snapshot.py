"""Pending taste snapshot for **Full clean & rescan**.

A full clean writes ``export_taste_state()`` to
``<data dir>/taste_snapshot_before_full_clean.json`` before truncating.
That file is *pending state*: it holds every rating that has not been put
back yet, and it is only deleted once nothing is left in it.

* A completed full clean restores what re-appeared, tombstones the rest
  and deletes the file.
* A cancelled / failed full clean restores what re-appeared so far and
  rewrites the file with only the unrestored entries (no tombstones).
* A shutdown mid-scan leaves the file as written.
* :func:`apply_pending_taste_snapshot` runs at startup and at the end of
  every scan; it restores whatever has re-appeared since and rewrites (or
  deletes) the file. It never tombstones.
* A corrupt file is renamed to ``<name>.corrupt-<timestamp>`` and logged,
  so it is neither retried forever nor lost.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TASTE_SNAPSHOT_FILENAME = "taste_snapshot_before_full_clean.json"


def snapshot_file_path(data_dir: Path) -> Path:
    return Path(data_dir) / TASTE_SNAPSHOT_FILENAME


def _is_empty(snapshot: Dict[str, Any]) -> bool:
    return not (
        snapshot.get("judgments")
        or snapshot.get("categories")
        or snapshot.get("favorites")
    )


def write_snapshot_file(data_dir: Path, snapshot: Dict[str, Any]) -> None:
    """Atomically replace the pending file with ``snapshot``."""
    target = snapshot_file_path(data_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    os.replace(tmp, target)


def _persist_remaining(data_dir: Path, remaining: Dict[str, Any]) -> None:
    """Rewrite the pending file with ``remaining``; delete it if empty."""
    if _is_empty(remaining):
        snapshot_file_path(data_dir).unlink(missing_ok=True)
    else:
        write_snapshot_file(data_dir, remaining)


_JUDGMENT_KEYS = {
    "file_path",
    "passed",
    "reasons",
    "seen_at",
    "judged_at",
    "confirmed_at",
}
_LINK_KEYS = {"file_path", "category_id", "category_name", "source", "assigned_at"}


def _valid_shape(snap: Any) -> bool:
    """Enough structure for ``restore_taste_state`` not to choke on it."""
    if not isinstance(snap, dict):
        return False
    lists = [snap.get(k, []) for k in ("judgments", "categories", "favorites")]
    if not all(isinstance(v, list) for v in lists):
        return False
    judgments, links, favorites = lists
    return (
        all(isinstance(j, dict) and _JUDGMENT_KEYS <= j.keys() for j in judgments)
        and all(isinstance(lk, dict) and _LINK_KEYS <= lk.keys() for lk in links)
        and all(isinstance(p, str) for p in favorites)
    )


def _quarantine(target: Path, why: str) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = target.with_name(f"{target.name}.corrupt-{stamp}")
    try:
        os.replace(target, dest)
        logger.warning(
            "Pending taste snapshot %s is corrupt (%s); kept as %s",
            target,
            why,
            dest,
        )
    except OSError as e:
        logger.warning(
            "Pending taste snapshot %s is corrupt (%s) and could not be " "renamed: %s",
            target,
            why,
            e,
        )


def load_pending_snapshot(data_dir: Path) -> Optional[Dict[str, Any]]:
    """The pending snapshot, or None when there is none. A file that cannot
    be parsed (or has the wrong shape) is quarantined and None returned."""
    target = snapshot_file_path(data_dir)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        _quarantine(target, f"unreadable: {e}")
        return None
    try:
        snap = json.loads(raw)
    except ValueError as e:
        _quarantine(target, str(e))
        return None
    if not _valid_shape(snap):
        _quarantine(target, "unexpected structure")
        return None
    return snap


def restore_and_persist(
    db: Any, data_dir: Path, snapshot: Dict[str, Any], tombstone_missing: bool
) -> Dict[str, int]:
    """Restore ``snapshot`` and update the pending file: deleted after a
    tombstoning restore (nothing is pending any more), otherwise rewritten
    with the unrestored entries (deleted if none). Returns the int counts."""
    result = db.restore_taste_state(snapshot, tombstone_missing)
    if tombstone_missing:
        snapshot_file_path(data_dir).unlink(missing_ok=True)
    else:
        _persist_remaining(data_dir, result["remaining"])
    return {k: int(v) for k, v in result.items() if k != "remaining"}


def apply_pending_taste_snapshot(db: Any, data_dir: Path) -> Dict[str, int]:
    """Restore pending entries whose media rows exist again (never
    tombstones) and rewrite / delete the pending file. Returns
    ``{restored, missing}``; zeros when there is no usable file."""
    snap = load_pending_snapshot(data_dir)
    if snap is None:
        return {"restored": 0, "missing": 0}
    counts = restore_and_persist(db, data_dir, snap, False)
    if counts["restored"]:
        logger.info(
            "Pending taste snapshot: restored %d paths, %d still pending",
            counts["restored"],
            counts["missing"],
        )
    return {"restored": counts["restored"], "missing": counts["missing"]}


def merge_pending_into_snapshot(
    data_dir: Path, snapshot: Dict[str, Any]
) -> Dict[str, Any]:
    """Return ``snapshot`` plus every pending entry whose path the fresh
    snapshot does not already cover (the fresh one is newer). Call after
    :func:`apply_pending_taste_snapshot` so the leftovers are exactly the
    paths that are not in the DB."""
    pending = load_pending_snapshot(data_dir)
    if pending is None:
        return snapshot
    fresh_paths = (
        {j["file_path"] for j in snapshot.get("judgments") or []}
        | {lk["file_path"] for lk in snapshot.get("categories") or []}
        | set(snapshot.get("favorites") or [])
    )
    merged = dict(snapshot)
    merged["judgments"] = list(snapshot.get("judgments") or []) + [
        j for j in pending.get("judgments") or [] if j["file_path"] not in fresh_paths
    ]
    merged["categories"] = list(snapshot.get("categories") or []) + [
        lk
        for lk in pending.get("categories") or []
        if lk["file_path"] not in fresh_paths
    ]
    merged["favorites"] = sorted(
        set(snapshot.get("favorites") or [])
        | {p for p in pending.get("favorites") or [] if p not in fresh_paths}
    )
    return merged
