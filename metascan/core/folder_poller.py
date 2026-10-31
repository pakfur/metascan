"""Folder poller for the always-on import (spec section 9).

Pure: no database, no ingest, no backend import. The caller seeds it with every
path the library already holds, then calls ``poll()`` on the cadence
``next_interval()`` gives. Each poll reports what settled (new and stable
enough to ingest), what vanished, what changed and which folders could not be
seen. A folder that cannot be seen hides its files; it never means they were
deleted, so nothing under it vanishes and nothing is forgotten.

Limits the consumer must cover: a mount point *below* a configured folder is
not detected, and a folder emptied by hand stays unavailable while known files
lie under it (it cannot be told from an unmounted drive). A walk that finds
fewer files than expected can still report many vanished at once, so the
consumer must guard mass vanishes (hygiene's mass-removal guard does).

Paths are normalised (``abspath`` + ``normpath``) at the boundary (seed,
folders, walk), then keyed by ``path_key``: the database holds
POSIX spellings, the walk native ones, and they must key equal.
"""

import os
import time
import unicodedata
from dataclasses import dataclass, field
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from metascan.utils.fs_compat import (
    can_open_for_read,
    is_ignored_name,
    is_partial_download,
    path_key,
)

WATCH_INTERVAL_S = 5.0
SLOW_WALK_FACTOR = 20

# The states a ``probe`` returns: the same strings backend.api.scan._probe_folder
# uses (core cannot import backend, so the caller passes the function).
PROBE_MISSING = "missing"
PROBE_UNREADABLE = "unreadable"
PROBE_EMPTY = "empty"
PROBE_PRESENT = "present"


class FileStamp(NamedTuple):
    size: int
    mtime: float


@dataclass(frozen=True)
class FolderSpec:
    filepath: str
    search_subfolders: bool


@dataclass
class PollResult:
    settled_new: List[str] = field(default_factory=list)
    vanished: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    # (old spelling, new) of a file whose name changed only in case on a
    # case-insensitive OS: both spellings key equal, so it is neither new nor
    # vanished.
    respelled: List[Tuple[str, str]] = field(default_factory=list)
    walk_s: float = 0.0


class _WalkError(Exception):
    """A folder could not be listed completely."""


# A stamp whose mtime moves by no more than this is the same (FAT stores 2 s
# resolution; network mounts round).
MTIME_TOLERANCE_S = 2.0


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _same(a: FileStamp, b: FileStamp) -> bool:
    return a.size == b.size and abs(a.mtime - b.mtime) <= MTIME_TOLERANCE_S


def _under(root_prefix: str, key: str) -> bool:
    # Separator-aware: "lib/" never covers "library/x".
    return key.startswith(root_prefix)


class FolderPoller:
    def __init__(
        self,
        folders: Sequence[FolderSpec],
        probe: Callable[[str], str],
        is_media: Callable[[str], bool],
        normalize: Callable[[str], str] = _normalize_path,
        sep: str = os.sep,
    ) -> None:
        # normalize/sep are a seam so tests can simulate Windows spellings.
        self._normalize = normalize
        self._sep = sep
        self._folders = list(folders)
        self._probe = probe
        # key -> candidate stamp of a known file that is changing.
        self._changing: Dict[str, FileStamp] = {}
        self._is_media = is_media
        # key -> stamp seen on the last pass (None until a pass has seen it).
        self._known: Dict[str, Optional[FileStamp]] = {}
        # key -> (real path, stamp) of a new file waiting to settle.
        self._pending: Dict[str, tuple] = {}
        # key -> the spelling to report (as seeded or as last seen on disk).
        self._spelling: Dict[str, str] = {}
        self._last_walk_s = 0.0

    def _key(self, path: str) -> str:
        return path_key(self._normalize(path))

    def _prefix(self, path: str) -> str:
        key = self._key(path)
        return key if key.endswith(self._sep) else key + self._sep

    def seed(self, known: Iterable[str]) -> None:
        for p in known:
            key = self._key(p)
            self._known.setdefault(key, None)
            self._spelling.setdefault(key, p)

    def next_interval(self) -> float:
        return max(WATCH_INTERVAL_S, SLOW_WALK_FACTOR * self._last_walk_s)

    # -- walking ---------------------------------------------------------

    def _walk(self, spec: FolderSpec) -> Dict[str, tuple]:
        found: Dict[str, tuple] = {}
        stack = [self._normalize(spec.filepath)]
        first = True
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as it:
                    for entry in it:
                        name = entry.name
                        if is_ignored_name(name):
                            continue
                        path = os.path.join(directory, name)
                        if entry.is_dir(follow_symlinks=False):
                            if spec.search_subfolders:
                                stack.append(path)
                            continue
                        if is_partial_download(name) or not self._is_media(path):
                            continue
                        try:
                            st = entry.stat()
                        except FileNotFoundError:
                            continue  # gone between listing and stat
                        found[self._key(path)] = (
                            path,
                            FileStamp(st.st_size, st.st_mtime),
                        )
            except FileNotFoundError:
                if first:
                    raise _WalkError(directory)
                continue  # a subfolder removed mid-walk
            except OSError as exc:
                raise _WalkError(directory) from exc
            first = False
        return found

    def walk_folder(self, spec: FolderSpec) -> Optional[List[str]]:
        """The media files under one folder as the poll sees them (partial
        downloads and ignored names left out), or None when
        the folder could not be listed completely."""
        try:
            return [path for path, _ in self._walk(spec).values()]
        except _WalkError:
            return None

    def _covers(self, spec: FolderSpec, key: str) -> bool:
        prefix = self._prefix(spec.filepath)
        if not _under(prefix, key):
            return False
        return spec.search_subfolders or self._sep not in key[len(prefix) :]

    def _known_under(self, spec: FolderSpec) -> bool:
        return any(self._covers(spec, k) for k in self._known)

    # -- polling ---------------------------------------------------------

    def poll(self) -> PollResult:
        t0 = time.monotonic()
        result = PollResult()
        available: List[FolderSpec] = []
        disk: Dict[str, tuple] = {}

        for spec in self._folders:
            state = self._probe(spec.filepath)
            if state in (PROBE_MISSING, PROBE_UNREADABLE):
                result.unavailable.append(spec.filepath)
                continue
            if state == PROBE_EMPTY and self._known_under(spec):
                # An unmounted drive's mount point is an empty directory.
                result.unavailable.append(spec.filepath)
                continue
            try:
                found = self._walk(spec)
            except _WalkError:
                result.unavailable.append(spec.filepath)
                continue
            available.append(spec)
            disk.update(found)

        unavailable_specs = [
            s for s in self._folders if s.filepath in result.unavailable
        ]

        # New files: wait for two equal stamps, non-empty, openable.
        for key, (path, stamp) in disk.items():
            before = self._spelling.get(key)
            self._spelling[key] = path
            if key in self._known:
                if before is not None and self._respelled(before, path):
                    result.respelled.append((before, path))
                old = self._known[key]
                if old is None:
                    self._known[key] = stamp
                elif _same(old, stamp):
                    self._changing.pop(key, None)
                else:
                    # Report only once the new stamp has held for two polls,
                    # so an in-place write is one change, not one per step.
                    cand = self._changing.get(key)
                    if cand is not None and _same(cand, stamp):
                        del self._changing[key]
                        self._known[key] = stamp
                        result.changed.append(path)
                    else:
                        self._changing[key] = stamp
                continue
            prev = self._pending.get(key)
            if (
                prev is not None
                and _same(prev[1], stamp)
                and stamp.size > 0
                and can_open_for_read(path)
            ):
                del self._pending[key]
                self._known[key] = stamp
                result.settled_new.append(path)
            else:
                self._pending[key] = (path, stamp)

        # A pending file that vanished while settling is dropped silently.
        for key in list(self._pending):
            if key not in disk and any(self._covers(s, key) for s in available):
                if not any(self._covers(s, key) for s in unavailable_specs):
                    del self._pending[key]

        # Vanished: known, covered by an available folder, none unavailable.
        for key in list(self._known):
            if key in disk:
                continue
            if not any(self._covers(s, key) for s in available):
                continue
            if any(self._covers(s, key) for s in unavailable_specs):
                continue
            result.vanished.append(self._spelling.pop(key, key))
            del self._known[key]
            self._changing.pop(key, None)

        self._last_walk_s = time.monotonic() - t0
        result.walk_s = self._last_walk_s
        return result

    def _respelled(self, old: str, new: str) -> bool:
        # Unicode normalisation alone is not a rename (macOS lists NFD names).
        a, b = self._normalize(old), self._normalize(new)
        nfc = unicodedata.normalize
        return nfc("NFC", a) != nfc("NFC", b)

    def pending_paths(self) -> List[str]:
        """New files still settling (the consumer's "settling" activity)."""
        return [p for p, _ in self._pending.values()]
