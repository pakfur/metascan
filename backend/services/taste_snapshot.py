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

Files carry ``"version": 2``: each judgment row has its ``grade`` (✓
``pass`` / ✗ ``fail`` / null) and the favorites list is ★ only. A file
without a version was written before grades and keeps its old meaning --
``passed = 1`` is a reject, a favorite is ★ and the positive -- so on load
:func:`upgrade_legacy_snapshot` translates it exactly as the v6 migration
maps live ratings. Both shapes validate; any other version is quarantined.
"""

import itertools
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from metascan.core.database_sqlite import TASTE_SNAPSHOT_VERSION
from metascan.core.judgments import GRADES

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


def _fsync_dir(directory: Path) -> None:
    """Make a rename in ``directory`` durable. Best effort: Windows (and
    some filesystems) cannot open or fsync a directory."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_snapshot_file(data_dir: Path, snapshot: Dict[str, Any]) -> None:
    """Atomically and durably replace the pending file with ``snapshot``.

    The full clean truncates the DB right after this returns, and that
    DELETE is durable -- so the file's bytes and the rename are fsynced
    first, or a power loss could leave an empty DB beside a zero-length
    snapshot."""
    target = snapshot_file_path(data_dir)
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(snapshot))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    _fsync_dir(target.parent)


def _empty_like(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": TASTE_SNAPSHOT_VERSION,
        "taken_at": snapshot.get("taken_at"),
        "judgments": [],
        "categories": [],
        "favorites": [],
    }


def _consume_pending_file(data_dir: Path, snapshot: Dict[str, Any]) -> None:
    """Remove the pending file once nothing is left in it. Retries the
    unlink once (e.g. a virus scanner holding it on Windows); if it still
    fails, overwrites it with an empty snapshot so already-handled entries
    are never restored or tombstoned a second time. Never raises."""
    target = snapshot_file_path(data_dir)
    for attempt in (1, 2):
        try:
            target.unlink(missing_ok=True)
            return
        except OSError as e:
            err = e
            if attempt == 1:
                time.sleep(0.2)
    try:
        write_snapshot_file(data_dir, _empty_like(snapshot))
        logger.warning(
            "Could not delete pending taste snapshot %s (%s); emptied it instead",
            target,
            err,
        )
    except OSError as e:
        logger.error(
            "Could not delete or empty pending taste snapshot %s (%s, %s); "
            "remove it by hand -- its entries were already handled",
            target,
            err,
            e,
        )


def _persist_remaining(data_dir: Path, remaining: Dict[str, Any]) -> None:
    """Rewrite the pending file with ``remaining``; delete it if empty."""
    if _is_empty(remaining):
        _consume_pending_file(data_dir, remaining)
    else:
        write_snapshot_file(data_dir, remaining)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _opt_str(v: Any) -> bool:
    return v is None or isinstance(v, str)


def _valid_grade(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v in GRADES)


def _valid_judgment(j: Any, versioned: bool) -> bool:
    """A version-2 row needs a valid ``grade``; a version-less row the
    legacy reject flag ``passed``, plus a valid ``grade`` if it has one
    (the interim export wrote both)."""
    if not (
        isinstance(j, dict)
        and isinstance(j.get("file_path"), str)
        and isinstance(j.get("reasons"), str)
        and all(
            k in j and _opt_str(j[k]) for k in ("seen_at", "judged_at", "confirmed_at")
        )
    ):
        return False
    if versioned:
        return "grade" in j and _valid_grade(j["grade"])
    return (_is_int(j.get("passed")) or isinstance(j.get("passed"), bool)) and (
        "grade" not in j or _valid_grade(j["grade"])
    )


def _valid_link(lk: Any) -> bool:
    return (
        isinstance(lk, dict)
        and isinstance(lk.get("file_path"), str)
        and _is_int(lk.get("category_id"))
        and isinstance(lk.get("category_name"), str)
        and isinstance(lk.get("source"), str)
        and isinstance(lk.get("assigned_at"), str)
    )


def _valid_shape(snap: Any) -> bool:
    """Structure *and* value types ``restore_taste_state`` relies on, so a
    bad file is quarantined instead of failing every restore forever."""
    if not isinstance(snap, dict):
        return False
    versioned = "version" in snap
    if versioned and not (
        _is_int(snap["version"]) and snap["version"] == TASTE_SNAPSHOT_VERSION
    ):
        return False
    lists = [snap.get(k, []) for k in ("judgments", "categories", "favorites")]
    if not all(isinstance(v, list) for v in lists):
        return False
    judgments, links, favorites = lists
    return (
        all(_valid_judgment(j, versioned) for j in judgments)
        and all(_valid_link(lk) for lk in links)
        and all(isinstance(p, str) for p in favorites)
    )


_JUDGMENT_KEYS = ("file_path", "reasons", "seen_at", "judged_at", "confirmed_at")


def upgrade_legacy_snapshot(
    snapshot: Dict[str, Any], now: Optional[datetime] = None
) -> Dict[str, Any]:
    """``snapshot`` as version 2 (a version-2 one is returned as is). Takes
    a snapshot that passed validation.

    A version-less file was written before grades, with the old meaning,
    and is mapped the way the v6 migration maps live ratings: a favorite
    gets ✓ (★ first, as v5 read a row that was both), else ``passed = 1``
    (a reject) gets ✗, else no grade; a favorite with no judgment row gets
    a ✓ row with seen_at / judged_at / confirmed_at = ``now`` (v6 dates the
    row it creates for one the same way). Reasons and timestamps are kept.

    A row that already carries a ``grade`` key (the interim export of the
    grades work, still version-less) is taken as stored, and a file with
    such rows creates no row for a bare favorite: there ★ is not a grade.
    """
    if snapshot.get("version") == TASTE_SNAPSHOT_VERSION:
        return snapshot
    rows = list(snapshot.get("judgments") or [])
    favorites = list(snapshot.get("favorites") or [])
    starred = set(favorites)
    judgments: List[Dict[str, Any]] = []
    for j in rows:
        if "grade" in j:
            grade = j["grade"]
        elif j["file_path"] in starred:
            grade = "pass"
        elif j["passed"]:
            grade = "fail"
        else:
            grade = None
        judgments.append({**{k: j[k] for k in _JUDGMENT_KEYS}, "grade": grade})
    if not any("grade" in j for j in rows):
        ts = (now or datetime.now(timezone.utc)).isoformat()
        rowed = {j["file_path"] for j in rows}
        judgments += [
            {
                "file_path": p,
                "grade": "pass",
                "reasons": "[]",
                "seen_at": ts,
                "judged_at": ts,
                "confirmed_at": ts,
            }
            for p in sorted(starred - rowed)
        ]
    return {
        "version": TASTE_SNAPSHOT_VERSION,
        "taken_at": snapshot.get("taken_at"),
        "judgments": judgments,
        "categories": list(snapshot.get("categories") or []),
        "favorites": favorites,
    }


class PendingSnapshotUnreadable(OSError):
    """The pending file exists but could neither be read nor quarantined.
    It may hold valid ratings: callers must leave it alone, and a full
    clean must abort rather than overwrite it."""


_quarantine_seq = itertools.count()


def _quarantine(target: Path, why: str) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = target.with_name(
        f"{target.name}.corrupt-{stamp}-{os.getpid()}-{next(_quarantine_seq)}"
    )
    try:
        os.replace(target, dest)
        logger.warning(
            "Pending taste snapshot %s is corrupt (%s); kept as %s",
            target,
            why,
            dest,
        )
    except OSError as e:
        raise PendingSnapshotUnreadable(
            f"pending ratings file {target} is corrupt ({why}) and could not "
            f"be moved aside: {e}"
        ) from e


def load_pending_snapshot(data_dir: Path) -> Optional[Dict[str, Any]]:
    """The pending snapshot as version 2 (one written before grades is
    translated, :func:`upgrade_legacy_snapshot`), or None when there is
    none. A file that cannot be parsed (or has the wrong shape / value
    types / an unknown version) is quarantined and None returned. Raises
    :class:`PendingSnapshotUnreadable` when the file exists but can't be
    read (locked, permissions -- it may well be valid) or can't be
    quarantined; the file is left in place."""
    target = snapshot_file_path(data_dir)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise PendingSnapshotUnreadable(
            f"pending ratings file {target} could not be read: {e}"
        ) from e
    try:
        snap = json.loads(raw)
    except ValueError as e:
        _quarantine(target, str(e))
        return None
    if not _valid_shape(snap):
        _quarantine(target, "unexpected structure")
        return None
    return upgrade_legacy_snapshot(cast(Dict[str, Any], snap))


def restore_and_persist(
    db: Any, data_dir: Path, snapshot: Dict[str, Any], tombstone_missing: bool
) -> Dict[str, int]:
    """Restore ``snapshot`` and update the pending file: deleted after a
    tombstoning restore (nothing is pending any more), otherwise rewritten
    with the unrestored entries (deleted if none). Returns the int counts."""
    result = db.restore_taste_state(snapshot, tombstone_missing)
    if tombstone_missing:
        _consume_pending_file(data_dir, snapshot)
    else:
        _persist_remaining(data_dir, result["remaining"])
    return {k: int(v) for k, v in result.items() if k != "remaining"}


def apply_pending_taste_snapshot(
    db: Any, data_dir: Path, raise_unreadable: bool = False
) -> Dict[str, int]:
    """Restore pending entries whose media rows exist again (never
    tombstones) and rewrite / delete the pending file. Returns
    ``{restored, missing}``; zeros when there is no usable file.

    A file that exists but can't be read is left in place: logged and
    skipped, or -- with ``raise_unreadable`` (the full-clean path, which
    must not overwrite it) -- :class:`PendingSnapshotUnreadable` raised."""
    try:
        return _apply_pending(db, data_dir)
    except PendingSnapshotUnreadable as e:
        if raise_unreadable:
            raise
        logger.warning("%s; left in place, will retry later", e)
        return {"restored": 0, "missing": 0}


def _apply_pending(db: Any, data_dir: Path) -> Dict[str, int]:
    snap = load_pending_snapshot(data_dir)
    if snap is None:
        return {"restored": 0, "missing": 0}
    try:
        counts = restore_and_persist(db, data_dir, snap, False)
    except (
        TypeError,
        ValueError,
        KeyError,
        sqlite3.IntegrityError,
        sqlite3.ProgrammingError,
    ) as e:
        # The data itself is unusable (the restore rolled back): quarantine
        # it rather than fail -- and block every full clean -- forever.
        _quarantine(snapshot_file_path(data_dir), f"restore rejected it: {e!r}")
        return {"restored": 0, "missing": 0}
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
