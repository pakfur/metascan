"""Folder poller for the always-on import (spec section 9).

Pure: no database, no ingest, no backend import. The caller seeds it with every
path the library already holds, then calls ``poll()`` on the cadence
``next_interval()`` gives. Each poll reports what settled (new and stable
enough to ingest), what vanished, what changed and which folders could not be
seen. A folder that cannot be seen hides its files; it never means they were
deleted, so nothing under it vanishes and nothing is forgotten.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set

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
    walk_s: float = 0.0


class _WalkError(Exception):
    """A folder could not be listed completely."""


def _prefix(path: str) -> str:
    key = path_key(path)
    return key if key.endswith(os.sep) else key + os.sep


def _under(root_prefix: str, key: str) -> bool:
    # Separator-aware: "lib/" never covers "library/x".
    return key.startswith(root_prefix)


class FolderPoller:
    def __init__(
        self,
        folders: Sequence[FolderSpec],
        probe: Callable[[str], str],
        skip_roots: Sequence[str],
        is_media: Callable[[str], bool],
    ) -> None:
        self._folders = list(folders)
        self._probe = probe
        self._skip = [_prefix(r) for r in skip_roots]
        self._is_media = is_media
        # key -> stamp seen on the last pass (None until a pass has seen it).
        self._known: Dict[str, Optional[FileStamp]] = {}
        # key -> (real path, stamp) of a new file waiting to settle.
        self._pending: Dict[str, tuple] = {}
        # key -> the spelling to report (as seeded or as last seen on disk).
        self._spelling: Dict[str, str] = {}
        self._last_walk_s = 0.0

    def seed(self, known: Iterable[str]) -> None:
        for p in known:
            key = path_key(p)
            self._known.setdefault(key, None)
            self._spelling.setdefault(key, p)

    def next_interval(self) -> float:
        return max(WATCH_INTERVAL_S, SLOW_WALK_FACTOR * self._last_walk_s)

    # -- walking ---------------------------------------------------------

    def _skipped(self, path: str) -> bool:
        key = path_key(path) + os.sep
        return any(_under(s, key) for s in self._skip)

    def _walk(self, spec: FolderSpec) -> Dict[str, tuple]:
        found: Dict[str, tuple] = {}
        stack = [spec.filepath]
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
                            if spec.search_subfolders and not self._skipped(path):
                                stack.append(path)
                            continue
                        if is_partial_download(name) or not self._is_media(path):
                            continue
                        if self._skipped(path):
                            continue
                        try:
                            st = entry.stat()
                        except FileNotFoundError:
                            continue  # gone between listing and stat
                        found[path_key(path)] = (
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

    def _covers(self, spec: FolderSpec, key: str) -> bool:
        prefix = _prefix(spec.filepath)
        if not _under(prefix, key):
            return False
        return spec.search_subfolders or os.sep not in key[len(prefix) :]

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
            self._spelling[key] = path
            if key in self._known:
                old = self._known[key]
                if old is not None and old != stamp:
                    result.changed.append(path)
                self._known[key] = stamp
                continue
            prev = self._pending.get(key)
            if (
                prev is not None
                and prev[1] == stamp
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
            if key in disk or self._skipped_key(key):
                continue
            if not any(self._covers(s, key) for s in available):
                continue
            if any(self._covers(s, key) for s in unavailable_specs):
                continue
            result.vanished.append(self._spelling.pop(key, key))
            del self._known[key]

        self._last_walk_s = time.monotonic() - t0
        result.walk_s = self._last_walk_s
        return result

    def _skipped_key(self, key: str) -> bool:
        k = key + os.sep
        return any(_under(s, k) for s in self._skip)
