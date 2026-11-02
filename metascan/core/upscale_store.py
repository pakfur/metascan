"""Swapping a library file for its upscaled version, and back.

One media item has at most one upscaled file (in the library, at its normal
path) and one original (in the Original folder, outside the library, kept for
compare, re-upscale and revert only). A swap changes two files and a database
row, so it is journalled in ``upscale_journal`` and every step can be crashed
out of:

``prepared``
    The journal row is written first, then the copies are made: the original
    into the Original folder (first upscale only) and the incoming file to a
    ``.tmp`` name next to the target. The library is unchanged, and recovery
    rolls back.
``placing``
    Written **before** anything in the library moves: the library file is
    renamed aside (the same ``.tmp`` rule, so Import ignores it) and the
    incoming file replaces the target. From here recovery rolls forward: an
    undone rollback could delete the only copy of the original.
``committed``
    Written by the database transaction that re-keys the row. What is left is
    removing the files nobody needs; a file that cannot be removed yet (a
    viewer holds it on Windows) keeps the row, and hygiene retries.

Renaming the library file aside comes first because it doubles as the probe
for a locked file: when it fails nothing has changed and the swap is refused.

The callers hold ``scan.move_gate`` around :meth:`UpscaleStore.commit`, so a
swap cannot land after a Full clean's snapshot or beside an Import move. The
copies run outside it: a large video must not make ``start_scan`` give up.

Paths in the database are stored POSIX; everything here converts at the edges.
Log lines carry no paths or names beyond what an exception type says."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from metascan.utils.fs_compat import path_key
from metascan.utils.path_utils import to_native_path, to_posix_path
from metascan.utils.trash import trash_file

if TYPE_CHECKING:
    from metascan.core.database_sqlite import DatabaseManager

logger = logging.getLogger(__name__)

# Windows refuses to rename a file a stream still has open; a tab closing its
# <video> lets go a moment later.
LOCK_RETRY_DELAYS_S = (0.1, 0.25, 0.5, 1.0)

CODE_NOT_FOUND = "not_found"
CODE_NO_RECORD = "not_upscaled"
CODE_FILE_IN_USE = "file_in_use"
CODE_UNFINISHED = "swap_unfinished"
CODE_WRONG_TYPE = "unsupported_output"


class UpscaleError(Exception):
    """A swap was refused; ``code`` is what the route answers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _native(path: str) -> Path:
    return Path(to_native_path(path))


def _same_file_name(a: str, b: str) -> bool:
    """``a`` and ``b`` are one path on this file system (``a.PNG`` / ``a.png``
    on NTFS, an NFC / NFD spelling on macOS)."""
    return path_key(a) == path_key(b)


def _copy_verified(source: Path, target: Path) -> None:
    """Copy with the timestamps, flushed to disk, or raise."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    with target.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    if target.stat().st_size != source.stat().st_size:
        raise OSError("copy came out a different size")


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


class UpscaleStore:
    """``original_root()`` is read on every first upscale, so a changed
    setting applies at once. ``technical(path)`` gives the file's facts for
    the media row (``Scanner.read_technical``)."""

    def __init__(
        self,
        db: "DatabaseManager",
        original_root: Callable[[], Path],
        technical: Callable[[Path], Dict[str, Any]],
        supported: Iterable[str] = (),
        trash: Callable[[Path], None] = trash_file,
    ) -> None:
        self.db = db
        self.original_root = original_root
        self.technical = technical
        self.supported = {s.lower() for s in supported}
        self.trash = trash

    # ---- paths ------------------------------------------------------------
    def _target_for(self, media_path: str, name: str) -> str:
        """Where a file called ``name`` goes in ``media_path``'s folder: its
        own path when it names the same file, else the first free name. A
        clash is decided here, before anything moves."""
        folder = media_path.rsplit("/", 1)[0]
        wanted = f"{folder}/{name}"
        if _same_file_name(wanted, media_path):
            return media_path
        stem, suffix = os.path.splitext(name)
        n = 1
        while True:
            candidate = wanted if n == 1 else f"{folder}/{stem}_{n}{suffix}"
            free = not _native(
                candidate
            ).exists() and candidate not in self.db.upscale_paths_in_use([candidate])
            if free:
                return candidate
            n += 1

    # ---- accept -----------------------------------------------------------
    def prepare_accept(self, candidate_id: int) -> int:
        """The slow half, outside the gate: copies made, journal ``prepared``.
        Returns the journal id for :meth:`commit`."""
        candidate = self.db.get_upscale_candidate(candidate_id)
        if candidate is None:
            raise UpscaleError(CODE_NOT_FOUND, "That result no longer exists.")
        incoming = _native(candidate["file_path"])
        if not incoming.is_file():
            raise UpscaleError(CODE_NOT_FOUND, "That result's file is gone.")
        if self.supported and incoming.suffix.lower() not in self.supported:
            raise UpscaleError(
                CODE_WRONG_TYPE, f"Metascan cannot read {incoming.suffix} files."
            )
        media_path = candidate["source_path"]
        current = _native(media_path)
        if not current.is_file():
            raise UpscaleError(CODE_NOT_FOUND, "The library file is gone.")
        record = self.db.upscale_record(media_path)
        name = f"{current.stem}{incoming.suffix}"
        target = self._target_for(media_path, name)
        original_name = record["original_name"] if record else current.name
        if record:
            original = record["original_path"]
        else:
            stored = self.original_root() / f"{uuid.uuid4().hex}{current.suffix}"
            original = to_posix_path(stored)
        token = uuid.uuid4().hex
        tmp = f"{target}.{token}.tmp"
        journal_id = self.db.open_upscale_journal(
            "accept",
            media_path,
            target,
            original,
            candidate_id,
            tmp,
            f"{media_path}.{token}.old.tmp",
            json.dumps(
                {
                    "first": record is None,
                    "original_name": original_name,
                    "preset_id": candidate.get("preset_id"),
                }
            ),
        )
        try:
            if record is None:
                _copy_verified(current, _native(original))
            _copy_verified(incoming, _native(tmp))
            self._complete_payload(journal_id, incoming)
        except Exception:
            self._roll_back(self._journal(journal_id))
            raise
        return journal_id

    def _complete_payload(self, journal_id: int, incoming: Path) -> None:
        """The facts of the file about to go in: read now, so neither the
        gate nor a recovery waits on a probe. Read from ``incoming``, not its
        ``.tmp`` copy, which has the same size and time: readers go by the
        extension."""
        row = self._journal(journal_id)
        payload = json.loads(row["payload"])
        try:
            payload["stats"] = self.technical(incoming)
        except ValueError as exc:
            raise UpscaleError(CODE_WRONG_TYPE, "That file cannot be read.") from exc
        self.db.set_upscale_journal(journal_id, payload=json.dumps(payload))

    def _journal(self, journal_id: int) -> Dict[str, Any]:
        row = self.db.get_upscale_journal(journal_id)
        if row is None:
            raise UpscaleError(CODE_NOT_FOUND, "That swap is over.")
        return row

    # ---- revert -----------------------------------------------------------
    def prepare_revert(self, media_path: str) -> int:
        record = self.db.upscale_record(media_path)
        if record is None:
            raise UpscaleError(CODE_NO_RECORD, "This file has no original to restore.")
        original = _native(record["original_path"])
        if not original.is_file():
            raise UpscaleError(CODE_NOT_FOUND, "The original file is missing.")
        target = self._target_for(media_path, record["original_name"])
        token = uuid.uuid4().hex
        tmp = f"{target}.{token}.tmp"
        journal_id = self.db.open_upscale_journal(
            "revert",
            media_path,
            target,
            record["original_path"],
            None,
            tmp,
            f"{media_path}.{token}.old.tmp",
            "{}",
        )
        try:
            _copy_verified(original, _native(tmp))
            self._complete_payload(journal_id, original)
        except Exception:
            self._roll_back(self._journal(journal_id))
            raise
        return journal_id

    def abandon(self, journal_id: int) -> None:
        """A prepared swap the caller will not commit."""
        self._roll_back(self._journal(journal_id))

    # ---- the critical section ---------------------------------------------
    def commit(self, journal_id: int) -> str:
        """Under ``move_gate``: put the file in place and write the row, then
        tidy up. Returns the media item's path, which an extension change
        moves. The swap is refused, with nothing changed, while the library
        file is locked; a failure after the file moved leaves the journal at
        ``placing`` and raises ``swap_unfinished`` (recovery finishes it)."""
        row = self._journal(journal_id)
        current = _native(row["media_path"])
        if not current.is_file():
            self._roll_back(row)
            raise UpscaleError(CODE_NOT_FOUND, "The library file is gone.")
        taken = row["target_path"] != row["media_path"] and row[
            "target_path"
        ] in self.db.upscale_paths_in_use([row["target_path"]])
        if taken:
            self._roll_back(row)
            raise UpscaleError(
                CODE_FILE_IN_USE, "The target name was taken. Try again."
            )
        self.db.set_upscale_journal(journal_id, phase="placing")
        try:
            self._rename_with_retry(current, _native(row["aside_path"]))
        except OSError as exc:
            self.db.set_upscale_journal(journal_id, phase="prepared")
            self._roll_back(row)
            raise UpscaleError(
                CODE_FILE_IN_USE,
                "The file is open somewhere. Close it and try again.",
            ) from exc
        try:
            self._place(row)
        except OSError as exc:
            # The incoming file could not land: put the library file back.
            logger.warning("upscale swap: placing failed: %s", type(exc).__name__)
            os.replace(_native(row["aside_path"]), current)
            self.db.set_upscale_journal(journal_id, phase="prepared")
            self._roll_back(row)
            raise UpscaleError(
                CODE_FILE_IN_USE,
                "The file is open somewhere. Close it and try again.",
            ) from exc
        try:
            self._write_row(row)
        except Exception as exc:
            logger.error("upscale swap: commit failed: %s", type(exc).__name__)
            raise UpscaleError(
                CODE_UNFINISHED,
                "The file was swapped but the library could not be updated. "
                "It will be finished automatically.",
            ) from exc
        self._tidy(self._journal(journal_id))
        return str(row["target_path"])

    def _rename_with_retry(self, source: Path, target: Path) -> None:
        for delay in (*LOCK_RETRY_DELAYS_S, None):
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)

    def _place(self, row: Dict[str, Any]) -> None:
        self._rename_with_retry(_native(row["tmp_path"]), _native(row["target_path"]))

    def _write_row(self, row: Dict[str, Any]) -> None:
        payload = json.loads(row["payload"])
        stats = payload["stats"]
        record = None
        if row["op"] == "accept":
            record = {
                "original_path": row["original_path"],
                "original_name": payload["original_name"],
                "upscaled_size": stats["file_size"],
                "preset_id": payload.get("preset_id"),
            }
        self.db.apply_upscale_swap(
            row["id"], row["media_path"], row["target_path"], stats, record
        )

    def _tidy(self, row: Dict[str, Any]) -> None:
        """The swap is committed: remove what nobody needs, then the journal
        row. A file that will not go keeps the row for a later retry."""
        try:
            self._remove_aside(row)
            if row["op"] == "revert":
                _unlink(_native(row["original_path"]))
            elif row["candidate_id"] is not None:
                self._drop_candidate(int(row["candidate_id"]))
        except OSError as exc:
            logger.warning("upscale swap: tidy-up left: %s", type(exc).__name__)
            return
        self.db.close_upscale_journal(row["id"])

    def _remove_aside(self, row: Dict[str, Any]) -> None:
        aside = _native(row["aside_path"])
        if row["op"] == "accept" and json.loads(row["payload"]).get("first"):
            # It is the file now safe in the Original folder.
            _unlink(aside)
        else:
            # A previous upscaled file: replaced, so recoverable.
            self.trash(aside)

    # ---- candidates -------------------------------------------------------
    def _drop_candidate(self, candidate_id: int) -> None:
        row = self.db.get_upscale_candidate(candidate_id)
        if row is None:
            return
        path = _native(row["file_path"])
        _unlink(path)
        self.db.delete_upscale_candidate(candidate_id)
        try:
            path.parent.rmdir()
        except OSError:
            pass  # another candidate of the same job is still in it

    def discard_candidate(self, candidate_id: int) -> None:
        self._drop_candidate(candidate_id)

    def purge_stale_candidates(self, source_path: str) -> int:
        """Candidates older than the file's accepted upscale: a new run after
        an accept starts clean. Before an accept they stay until Discard."""
        record = self.db.upscale_record(source_path)
        if record is None:
            return 0
        stale = [
            c
            for c in self.db.list_upscale_candidates(source_path)
            if str(c["created_at"]) <= str(record["upscaled_at"])
        ]
        for candidate in stale:
            self._drop_candidate(int(candidate["id"]))
        return len(stale)

    # ---- a deleted media item ---------------------------------------------
    def release(self, posix_paths: Iterable[str]) -> None:
        """These items left the library: the original has no purpose any more
        and goes to the trash, their candidates with it. Never raises: the
        delete has already happened."""
        for path in posix_paths:
            try:
                self._release_one(path)
            except Exception as exc:
                logger.warning("upscale release failed: %s", type(exc).__name__)

    def _release_one(self, path: str) -> None:
        record = self.db.upscale_record(path)
        if record is not None:
            self.trash(_native(record["original_path"]))
            self.db.delete_upscale_record(path)
        for candidate in self.db.list_upscale_candidates(path):
            self._drop_candidate(int(candidate["id"]))

    # ---- recovery ---------------------------------------------------------
    def recover(self) -> int:
        """Finish or undo every open swap. Runs at startup before anything
        watches the library, and again from hygiene for a swap whose commit
        or tidy-up failed. Files and database only. Returns how many rows
        it closed."""
        closed = 0
        for row in self.db.list_upscale_journal():
            try:
                self.recover_one(row)
            except Exception as exc:
                logger.error(
                    "upscale recovery left a swap open: %s", type(exc).__name__
                )
                continue
            if self.db.get_upscale_journal(row["id"]) is None:
                closed += 1
        return closed

    def recover_one(self, row: Dict[str, Any]) -> None:
        if row["phase"] == "prepared":
            self._roll_back(row)
        elif row["phase"] == "placing":
            self._finish_placing(row)
            self._write_row(row)
            self._tidy(self._journal(row["id"]))
        else:
            self._tidy(row)

    def _finish_placing(self, row: Dict[str, Any]) -> None:
        """Complete the file half from wherever a crash left it. The ``.tmp``
        file still existing means the replace did not happen."""
        tmp = _native(row["tmp_path"])
        current = _native(row["media_path"])
        aside = _native(row["aside_path"])
        if tmp.exists():
            if current.exists() and not aside.exists():
                os.replace(current, aside)
            os.replace(tmp, _native(row["target_path"]))

    def _roll_back(self, row: Dict[str, Any]) -> None:
        """Undo a swap that never reached the library."""
        _unlink(_native(row["tmp_path"]))
        first = row["op"] == "accept" and json.loads(row["payload"]).get("first")
        if first:
            # No record points at it: the library still has this file.
            _unlink(_native(row["original_path"]))
        self.db.close_upscale_journal(row["id"])

    def open_journal(self) -> List[Dict[str, Any]]:
        return self.db.list_upscale_journal()


def original_for(db: "DatabaseManager", file_path: Path, size: int) -> Optional[Path]:
    """The kept original of the upscaled file at ``file_path``, for reading
    generation metadata. None for any other file: no record, a record whose
    size says another file has since landed on this path, or an original
    that is not there."""
    record = db.upscale_record(to_posix_path(file_path))
    if record is None or record["upscaled_size"] != size:
        return None
    original = _native(record["original_path"])
    return original if original.is_file() else None


def release_upscale(db: "DatabaseManager", paths: Iterable[Any]) -> None:
    """After a delete: :meth:`UpscaleStore.release` with no store at hand."""
    UpscaleStore(db, lambda: Path(), lambda path: {}).release(
        to_posix_path(p) for p in paths
    )
