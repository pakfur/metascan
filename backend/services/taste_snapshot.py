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

Files carry ``"version": 3``: each judgment row has its ``grade`` (✓
``pass`` / ✗ ``fail`` / null), the favorites list is ★ only, ``briefs``
holds every hindsight brief and ``library_prompts`` each ✓ library image's
metadata prompt and model names -- what a completed full clean keeps on the
tombstone of a path that did not come back, as a delete keeps them. A
version-2 file (grades, neither list) reads as version 3 with no briefs and
no prompts. A file without a version was written before grades and keeps
its old meaning -- ``passed = 1`` is a reject, a favorite is ★ and the
positive -- so on load :func:`upgrade_legacy_snapshot` translates it exactly
as the v6 migration maps live ratings. All three shapes validate; any other
version is quarantined. Briefs and prompts are training text: nothing here
logs them (counts only).

While a full clean runs, its file exists, or its lasting marker
``<data dir>/full_clean_incomplete`` does (written before the truncate,
removed only when a scan of every configured folder completes), the T2I /
I2V listings must not prune their lineage rows (:func:`lineage_prune_guard`):
the rescan re-adds the media rows one by one, and a completed full clean
reads the vanished items' lineage from those rows when it tombstones them.
"""

import itertools
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    cast,
)

from metascan.core.database_sqlite import TASTE_SNAPSHOT_VERSION
from metascan.core.judgments import GRADES

logger = logging.getLogger(__name__)

TASTE_SNAPSHOT_FILENAME = "taste_snapshot_before_full_clean.json"

# The versioned pending files this module reads: 2 (grades; no briefs or
# library prompts) and 3 (the current version: also briefs and library
# prompts). A file without a version is the pre-grades form. Spelled out,
# never derived from TASTE_SNAPSHOT_VERSION, and every reader names the
# version it means: a later bump must say how it reads a version-3 file,
# which would otherwise be read as a version-2 one, briefs dropped.
_READABLE_VERSIONS = (2, 3)

# A snapshot's lists of per-path rows (``favorites`` is a list of paths).
_ROW_LISTS = ("judgments", "categories", "briefs", "library_prompts")


def snapshot_file_path(data_dir: Path) -> Path:
    return Path(data_dir) / TASTE_SNAPSHOT_FILENAME


# ---- the lineage prune pause ---------------------------------------------------
#
# ``list_t2i_images`` / ``list_i2v_videos`` delete the lineage rows whose
# media row is gone. A full clean empties ``media`` and the rescan re-adds
# it file by file, so until the rescan reaches a file its lineage row looks
# dangling: one listing (the T2I dialog opening, a WebSocket reconnect)
# would wipe the lot. The lifespan installs lineage_prune_guard on the
# database (``DatabaseManager.set_lineage_prune_guard``).

_full_clean_runs = 0
_full_clean_lock = threading.Lock()


@contextmanager
def full_clean_running() -> Iterator[None]:
    """Mark a full clean as running in this process for the ``with`` block.
    The scan route wraps the whole run, from before its snapshot to after
    its restore; the mark goes however the block ends (an abort, an error,
    a shutdown's CancelledError). The marker and the pending file outlive a
    run, this mark does not."""
    global _full_clean_runs
    with _full_clean_lock:
        _full_clean_runs += 1
    try:
        yield
    finally:
        with _full_clean_lock:
            _full_clean_runs -= 1


def is_full_clean_running() -> bool:
    return _full_clean_runs > 0


def _exists(path: Path) -> bool:
    """Only a missing file is False: any other error checking for it
    (permissions, a data dir that is not a directory) is raised, so a
    caller that must fail safe can. Never ``Path.exists``, which reads some
    of those errors as "missing"."""
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    return True


def pending_snapshot_exists(data_dir: Path) -> bool:
    """Whether the pending file exists (see ``_exists``)."""
    return _exists(snapshot_file_path(data_dir))


# The lasting mark of an unfinished full clean. The pending file only lists
# rated paths and goes as soon as they are all back (or empty, at once),
# while the rescan may still be far from the unrated T2I / I2V files.
FULL_CLEAN_MARKER_FILENAME = "full_clean_incomplete"

_MARKER_TEXT = """\
Metascan started a full clean & rescan at {started}. It is unfinished until
a scan walks every configured folder to the end and brings back every
generated image and clip still on disk, with neither generated-output
folder missing or unreadable: Text-to-Image's and Image-to-Video's output
folders, the ones set in their configuration or else the t2i and i2v
folders in ComfyUI's output folder. A folder missing inside one of them (an
image's or a day's) holds nothing back: its files count as deleted.

While this file exists, the Text-to-Image and Image-to-Video lists keep the
records (prompts, writers, variants) of generated images and clips whose
library entry is missing, instead of deleting them: the rescan may still
bring them back. Metascan deletes this file itself when such a scan
completes.

If a generated-output folder is gone for good, recreate it (empty is fine)
and run Full clean & rescan: its missing images then count as deleted, and
the ones the library had a grade, a view, a favorite, a category or a
hindsight brief for are recorded as deleted with their history.

To let the lists drop those records without scanning, delete this file;
Metascan may keep running. A full clean that is still running, or a pending
ratings file next to it ({pending}), also holds them back: while that file
is there, deleting this one alone is not enough.
"""

# What the last scan could not bring back of the generated output, appended
# to the marker by note_generated_output and replaced by the next scan's
# findings. Every section starts with _LAST_SCAN_COULD_NOT, which the text
# above never has at the start of a line: the block is everything from the
# first one on -- also the one an older build wrote (the unreachable
# folders alone). Never a file's path: the owner may share this file, and a
# generated file's name can carry prompt text.
_LAST_SCAN_COULD_NOT = "The last scan could not "
_UNREACHABLE_HEADER = _LAST_SCAN_COULD_NOT + "reach these generated-output folders:"
_UNREACHABLE_ADVICE = """\
Their images and clips may still be there, so their ratings are kept,
pending, and none of them is recorded as deleted. Reconnect or mount the
drive and run a scan. If a folder is gone for good, recreate it (empty is
fine) and run Full clean & rescan.
"""
_UNREADABLE_ADVICE = """\
A file still on disk is never recorded as deleted: its ratings are kept,
pending, and the full clean stays unfinished. The scan's Done step lists
the files. Delete each one, or move it out of the output folder (or repair
it), then run Full clean & rescan. A file that only failed this time (a
busy disk) comes back by itself at the next scan.
"""
_CHECK_FAILED = (
    _LAST_SCAN_COULD_NOT
    + """check the generated output ({error};
the server log has the details), so the full clean stays unfinished: the
ratings of the generated images and clips that did not come back are kept,
pending, and none of them is recorded as deleted. The next scan tries
again.
"""
)


def full_clean_marker_path(data_dir: Path) -> Path:
    return Path(data_dir) / FULL_CLEAN_MARKER_FILENAME


def full_clean_marker_exists(data_dir: Path) -> bool:
    """Whether the marker exists (see ``_exists``)."""
    return _exists(full_clean_marker_path(data_dir))


def write_full_clean_marker(data_dir: Path) -> None:
    """Create (or refresh) the marker, durably: a full clean writes it before
    anything else -- before it applies or writes the pending file, and so
    before the truncate -- with its data and directory entry fsynced, so a
    crash after the truncate still finds it, and a marker that can't be
    written stops the clean with nothing written. Raises OSError."""
    target = full_clean_marker_path(data_dir)
    text = _MARKER_TEXT.format(
        started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        pending=TASTE_SNAPSHOT_FILENAME,
    )
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _fsync_dir(target.parent)


def generated_output_place(root: Optional[str]) -> str:
    """Where generated files are, said by the output folder that holds them:
    ``in <root>``, or, for ``None`` (a file under none of the configured
    output folders), ``outside the configured output folders``. The marker
    and the scan route's log say it the same way."""
    return "outside the configured output folders" if root is None else f"in {root}"


def _unreadable_section(count: int, roots: Sequence[Optional[str]]) -> str:
    what = "image or clip that is" if count == 1 else "images or clips that are"
    lines = "".join(f"  {generated_output_place(root)}\n" for root in roots)
    return (
        f"{_LAST_SCAN_COULD_NOT}read or import {count} generated {what}\n"
        f"on disk:\n{lines}{_UNREADABLE_ADVICE}"
    )


def _generated_output_block(
    unreachable: Mapping[str, str],
    unreadable_count: int,
    unreadable_roots: Sequence[Optional[str]],
    check_failed: Optional[str],
) -> str:
    sections: List[str] = []
    if unreachable:
        lines = "".join(f"  {folder} ({why})\n" for folder, why in unreachable.items())
        sections.append(f"{_UNREACHABLE_HEADER}\n{lines}{_UNREACHABLE_ADVICE}")
    if unreadable_count > 0:
        sections.append(_unreadable_section(unreadable_count, unreadable_roots))
    if check_failed:
        sections.append(_CHECK_FAILED.format(error=check_failed))
    return "".join("\n" + section for section in sections)


def note_generated_output(
    data_dir: Path,
    unreachable: Mapping[str, str],
    unreadable_count: int = 0,
    unreadable_roots: Sequence[Optional[str]] = (),
    check_failed: Optional[str] = None,
) -> bool:
    """Say in the marker, after its text, what the last scan could not bring
    back of the generated output, in place of what an earlier scan said
    there; nothing to say leaves the marker as it was written:

    * ``unreachable``: the output folders it could not reach, each mapped
      to why ("missing", "can't be read"), with the way out;
    * ``unreadable_count`` files on disk it could not read or import, and
      ``unreadable_roots``, the output folders that hold them (``None``: a
      file under none of them), with the way out. Never a file's path: the
      owner may share this file, and a generated file's name can carry
      prompt text;
    * ``check_failed``: the type name of the error the check itself raised
      (nothing about the output is known), and that the next scan tries
      again.

    Folders are written as given (the scan route passes them absolute).
    Only an existing marker is rewritten: one deleted by hand while the scan
    ran stays deleted. Never raises (the text is advice; the marker's
    existence holds the pause): False when the marker is not there or could
    not be rewritten."""
    target = full_clean_marker_path(data_dir)
    block = _generated_output_block(
        unreachable, unreadable_count, unreadable_roots, check_failed
    )
    try:
        with open(target, "r+", encoding="utf-8") as f:
            text = f.read()
            new = text.partition("\n" + _LAST_SCAN_COULD_NOT)[0] + block
            if new != text:
                f.seek(0)
                f.write(new)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as e:  # ValueError: not UTF-8
        logger.warning(
            "Could not note the last scan's generated-output findings in %s (%s)",
            target,
            e,
        )
        return False
    return True


def clear_full_clean_marker(data_dir: Path) -> bool:
    """Remove the marker: only the scan route calls it, once a scan has walked
    every configured folder to the end and re-ingested the generated output
    still on disk -- every file still on disk in a configured folder, and
    every T2I / I2V output still on disk, has its media row again. True when
    it is gone. A failed removal (OSError) is logged and returns False: the
    marker stays, so the prune stays paused (fail safe)."""
    target = full_clean_marker_path(data_dir)
    try:
        target.unlink(missing_ok=True)
    except OSError as e:
        logger.error(
            "Could not remove %s (%s): the T2I / I2V lists keep their records "
            "of missing files until it is gone; delete it by hand to resume "
            "(a running full clean or a pending ratings file still holds them)",
            target,
            e,
        )
        return False
    return True


def lineage_prune_guard(data_dir: Path) -> Callable[[], bool]:
    """The guard for ``DatabaseManager.set_lineage_prune_guard``: True --
    no prune -- while a full clean runs in this process, its marker exists
    in ``data_dir`` (written before the truncate; removed only once a scan
    of every configured folder completes, so it outlives a cancel, an error
    and a restart), or its pending file does (it holds the ratings that have
    not come back). A failed check raises, which the database counts as
    True. Runs under the DB lock: two stats, nothing else."""

    def paused() -> bool:
        return (
            is_full_clean_running()
            or full_clean_marker_exists(data_dir)
            or pending_snapshot_exists(data_dir)
        )

    return paused


def _is_empty(snapshot: Dict[str, Any]) -> bool:
    return not (snapshot.get("favorites") or any(snapshot.get(k) for k in _ROW_LISTS))


def snapshot_paths(snapshot: Dict[str, Any]) -> Set[str]:
    """Every path ``snapshot`` holds an entry for (a judgment row, a
    category link, ★, a brief or a library prompt), in the stored POSIX
    form."""
    paths = set(snapshot.get("favorites") or [])
    for key in _ROW_LISTS:
        paths.update(e["file_path"] for e in snapshot.get(key) or [])
    return paths


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
        "briefs": [],
        "library_prompts": [],
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
    """A versioned row (2 or 3) needs a valid ``grade``; a version-less row
    the legacy reject flag ``passed``, plus a valid ``grade`` if it has one
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


def _utf8(v: Any) -> bool:
    """Text SQLite can store: a str that encodes as UTF-8. JSON can carry
    a lone surrogate (``"\\udc80"``) that does not, and binding one fails
    the restore halfway with the whole text in the error."""
    if not isinstance(v, str):
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_brief(b: Any) -> bool:
    """A ``hindsight_briefs`` row: every column is NOT NULL text."""
    return isinstance(b, dict) and all(
        _utf8(b.get(k)) for k in ("file_path", "brief", "model", "created_at")
    )


def _valid_model_name(v: Any) -> bool:
    """None, or a list of names as the JSON text a tombstone's
    ``model_name`` stores (``_library_prompt``)."""
    if v is None:
        return True
    if not _utf8(v):
        return False
    try:
        names = json.loads(v)
    except (ValueError, RecursionError):
        return False
    return isinstance(names, list) and all(_utf8(n) for n in names)


def _valid_library_prompt(lp: Any) -> bool:
    return (
        isinstance(lp, dict)
        and _utf8(lp.get("file_path"))
        and "prompt" in lp
        and (lp["prompt"] is None or _utf8(lp["prompt"]))
        and "model_name" in lp
        and _valid_model_name(lp["model_name"])
    )


def _valid_shape(snap: Any) -> bool:
    """Structure *and* value types ``restore_taste_state`` relies on, so a
    bad file is quarantined instead of failing every restore forever."""
    if not isinstance(snap, dict):
        return False
    versioned = "version" in snap
    if versioned and not (
        _is_int(snap["version"]) and snap["version"] in _READABLE_VERSIONS
    ):
        return False
    lists = [snap.get(k, []) for k in ("judgments", "categories", "favorites")]
    if not all(isinstance(v, list) for v in lists):
        return False
    judgments, links, favorites = lists
    if not (
        all(_valid_judgment(j, versioned) for j in judgments)
        and all(_valid_link(lk) for lk in links)
        and all(isinstance(p, str) for p in favorites)
    ):
        return False
    if not versioned or snap["version"] == 2:
        return True  # neither shape has briefs or library prompts
    # Version 3: briefs and library prompts.
    briefs, prompts = snap.get("briefs", []), snap.get("library_prompts", [])
    return (
        isinstance(briefs, list)
        and isinstance(prompts, list)
        and all(_valid_brief(b) for b in briefs)
        and all(_valid_library_prompt(lp) for lp in prompts)
    )


_JUDGMENT_KEYS = ("file_path", "reasons", "seen_at", "judged_at", "confirmed_at")


def upgrade_legacy_snapshot(
    snapshot: Dict[str, Any], now: Optional[datetime] = None
) -> Dict[str, Any]:
    """``snapshot`` as the current version, 3 (a version-3 one is returned
    as is). Takes a snapshot that passed validation; a versioned one this
    function has no upgrade for raises ValueError (a version bump must add
    its own step, never read an older file as version 2).

    A version-2 file already has its grades and no briefs or library
    prompts: its rows are kept, and it gets both lists empty.

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
    version = snapshot.get("version")
    if version == TASTE_SNAPSHOT_VERSION:
        return snapshot
    if version == 2:  # the rows already carry their grades
        return _as_current(snapshot, list(snapshot.get("judgments") or []))
    if "version" in snapshot:
        raise ValueError(f"no upgrade from taste snapshot version {version!r}")
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
    return _as_current(snapshot, judgments)


def _as_current(
    snapshot: Dict[str, Any], judgments: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """An older ``snapshot`` in the current shape, with ``judgments`` as its
    rows. Neither older shape has briefs or library prompts: it was written
    before a snapshot kept them."""
    return {
        "version": TASTE_SNAPSHOT_VERSION,
        "taken_at": snapshot.get("taken_at"),
        "judgments": judgments,
        "categories": list(snapshot.get("categories") or []),
        "favorites": list(snapshot.get("favorites") or []),
        "briefs": [],
        "library_prompts": [],
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
    """The pending snapshot as version 3 (a version-2 one, or one written
    before grades, is translated: :func:`upgrade_legacy_snapshot`), or None
    when there is none. A file that cannot be parsed (not UTF-8, not JSON,
    or nested deeper than the parser goes) or has the wrong shape / value
    types / an unknown version is quarantined and None returned. Raises
    :class:`PendingSnapshotUnreadable` when the file exists but can't be
    read (locked, permissions -- it may well be valid) or can't be
    quarantined; the file is left in place."""
    target = snapshot_file_path(data_dir)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise PendingSnapshotUnreadable(
            f"pending ratings file {target} could not be read: {e}"
        ) from e
    # Decoded here, not by the read: a decode error is not an OSError, and
    # if it escaped the loader it would abort every full clean on the file.
    try:
        snap = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as e:  # UnicodeDecodeError: a ValueError
        _quarantine(target, str(e))
        return None
    if not _valid_shape(snap):
        _quarantine(target, "unexpected structure")
        return None
    return upgrade_legacy_snapshot(cast(Dict[str, Any], snap))


def pending_snapshot_paths(data_dir: Path) -> Set[str]:
    """The paths the pending file holds ratings for, as
    :func:`load_pending_snapshot` reads them -- an older file upgraded
    (:func:`upgrade_legacy_snapshot`), so stray ``briefs`` or
    ``library_prompts`` in a version-2 or version-less file are ignored as
    the loader drops them -- but without side effects: nothing is
    quarantined or raised, and a missing, unreadable or invalid file holds
    none -- one the parser cannot read too (not UTF-8, not JSON, or nested
    deeper than it goes). For checks that must change nothing; the full
    clean itself loads the file with :func:`load_pending_snapshot`."""
    try:
        snap = json.loads(snapshot_file_path(data_dir).read_text(encoding="utf-8"))
        if not _valid_shape(snap):
            return set()
        return snapshot_paths(upgrade_legacy_snapshot(cast(Dict[str, Any], snap)))
    except (OSError, ValueError, RecursionError):
        return set()


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
        # it rather than fail -- and block every full clean -- forever. The
        # error by its type only: its text can quote the data (a
        # UnicodeEncodeError's repr carries the whole brief it failed on).
        _quarantine(
            snapshot_file_path(data_dir), f"restore rejected it: {type(e).__name__}"
        )
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
    """Return ``snapshot`` plus every pending entry -- briefs and library
    prompts included -- whose path the fresh snapshot does not already
    cover (the fresh one is newer). Call after
    :func:`apply_pending_taste_snapshot` so the leftovers are exactly the
    paths that are not in the DB."""
    pending = load_pending_snapshot(data_dir)
    if pending is None:
        return snapshot
    fresh_paths = snapshot_paths(snapshot)
    merged = dict(snapshot)
    for key in _ROW_LISTS:
        merged[key] = list(snapshot.get(key) or []) + [
            e for e in pending.get(key) or [] if e["file_path"] not in fresh_paths
        ]
    merged["favorites"] = sorted(
        set(snapshot.get("favorites") or [])
        | {p for p in pending.get("favorites") or [] if p not in fresh_paths}
    )
    return merged
