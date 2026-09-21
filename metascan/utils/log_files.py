"""The one place log files are opened, so they are all bounded the same way.

Policy: a log file's live copy never exceeds ``LOG_MAX_BYTES`` (10 MB) and
only the ``LOG_BACKUP_COUNT`` (3) most recent rollovers are kept --
``server.log``, ``server.log.1`` … ``server.log.3``, 40 MB worst case per
log. Built on the standard library's ``RotatingFileHandler``.

Never open a log with a bare ``open(path, "a")`` or construct a
``FileHandler`` elsewhere: the metadata extraction report did the former
and reached 4.25 GB in one full import. ``tests/test_log_files.py``
enforces this.

``RotatingFileHandler`` is safe across threads but NOT across processes:
give each process its own file.
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional, Union

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3

DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

PathLike = Union[str, Path]


class BoundedFileHandler(RotatingFileHandler):
    """``RotatingFileHandler`` that survives the file being deleted and can
    stamp a header line at the top of every file it starts."""

    def __init__(
        self,
        path: PathLike,
        max_bytes: int,
        backup_count: int,
        header: Optional[str] = None,
    ) -> None:
        self._header = header
        super().__init__(
            str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

    def _open(self):  # type: ignore[no-untyped-def]
        # Called for the first open and again after every rollover, so a
        # structured file (CSV) stays parseable on its own.
        stream = super()._open()
        if self._header is not None and stream.tell() == 0:
            stream.write(self._header + self.terminator)
            stream.flush()
        return stream

    def emit(self, record: logging.LogRecord) -> None:
        # Logs get cleared by hand while the server runs. Without this the
        # handler keeps writing to the unlinked inode: invisible output
        # that still eats disk until the process exits.
        if self.stream is not None and not os.path.exists(self.baseFilename):
            self.acquire()
            try:
                self.stream.close()
                self.stream = self._open()
            finally:
                self.release()
        super().emit(record)


def rotating_file_handler(
    path: PathLike,
    *,
    fmt: Optional[str] = DEFAULT_FORMAT,
    header: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> BoundedFileHandler:
    """A handler for ``path`` under the shared size/rollover policy.

    ``max_bytes`` / ``backup_count`` exist for tests; production callers
    leave them unset. The policy constants are read at call time.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = BoundedFileHandler(
        path,
        max_bytes=LOG_MAX_BYTES if max_bytes is None else max_bytes,
        backup_count=LOG_BACKUP_COUNT if backup_count is None else backup_count,
        header=header,
    )
    if fmt is not None:
        handler.setFormatter(logging.Formatter(fmt))
    return handler


# ---- raw-text file loggers -------------------------------------------
#
# For files that are reports rather than log streams (the metadata
# extraction report and its error CSV): the message is written verbatim,
# nothing propagates to the root logger, and every caller naming the same
# path shares ONE handler -- two handlers on one file would each rotate it
# out from under the other.

_file_loggers: Dict[Path, logging.Logger] = {}
_file_loggers_lock = threading.Lock()


def get_file_logger(
    path: PathLike,
    *,
    header: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> logging.Logger:
    key = Path(path).resolve()
    with _file_loggers_lock:
        logger = _file_loggers.get(key)
        if logger is None:
            logger = logging.Logger(f"metascan.file.{key.name}", level=logging.INFO)
            logger.propagate = False
            handler = rotating_file_handler(
                key, fmt=None, header=header, max_bytes=max_bytes
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            _file_loggers[key] = logger
        return logger


def close_file_loggers(path: Optional[PathLike] = None) -> None:
    """Close (and forget) one file logger, or all of them."""
    with _file_loggers_lock:
        keys = [Path(path).resolve()] if path is not None else list(_file_loggers)
        for key in keys:
            logger = _file_loggers.pop(key, None)
            if logger is None:
                continue
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


def rollover_files(path: PathLike) -> list:
    """``path`` plus every numbered rollover of it that exists."""
    path = Path(path)
    return [
        p for p in [path, *sorted(path.parent.glob(path.name + ".*"))] if p.exists()
    ]


# ---- the server's own log ----------------------------------------------

_SERVER_LOG_ENV = "METASCAN_LOG_FILE"


def install_server_log(log_dir: PathLike) -> Optional[Path]:
    """Mirror the root logger into ``<log_dir>/server.log``.

    Idempotent. Set ``METASCAN_LOG_FILE=0`` to keep logging console-only
    (the test suite does). Returns the log path, or None when disabled.
    """
    if os.environ.get(_SERVER_LOG_ENV, "1").strip().lower() in {"0", "false", "off"}:
        return None
    path = Path(log_dir) / "server.log"
    root = logging.getLogger()
    for existing in root.handlers:
        if (
            isinstance(existing, BoundedFileHandler)
            and Path(existing.baseFilename) == path.resolve()
        ):
            return path
    root.addHandler(rotating_file_handler(path))
    return path
