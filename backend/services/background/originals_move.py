"""Moving Metascan's originals to a new Original folder (spec: upscaling).

One file at a time: copy, verify, repoint its record, delete the old copy. The
setting changes only when the last file has arrived, and records hold absolute
paths, so a move interrupted anywhere leaves every original findable.

The target waits in ``<data dir>/originals_move.json``, so a restart picks the
move up unless the owner paused or stopped it. Pause lands between files; Stop
lands there too, drops the target and leaves the setting alone.

Accept and revert are refused while a move is pending (``active``). Counts
only in the activity line, never a path."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.background.protocol import (
    Capabilities,
    Progress,
    ProcessReport,
    State,
)
from backend.services.background.registry import ProcessRegistry
from metascan.utils.path_utils import to_native_path, to_posix_path

logger = logging.getLogger(__name__)

STATE_FILE = "originals_move.json"


class OriginalsMoveProcess:
    id = "originals_move"
    label = "Moving originals"
    order = 65
    holds: Tuple[str, ...] = ()

    def __init__(
        self,
        registry: ProcessRegistry,
        db: Any,
        data_dir: Path,
        finish_setting: Callable[[str], None],
    ) -> None:
        self._registry = registry
        self._db = db
        self._file = Path(data_dir) / STATE_FILE
        self._finish_setting = finish_setting
        self._gate = asyncio.Event()
        self._gate.set()
        self._task: Optional["asyncio.Task[None]"] = None
        self._stopping = False
        self._done = 0
        self._total = 0
        self._error: Optional[str] = None

    # ---- the pending target ----------------------------------------------
    def _target(self) -> Optional[str]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning("originals move state unreadable")
            return None
        target = data.get("target") if isinstance(data, dict) else None
        return target if isinstance(target, str) and target else None

    def _write_target(self, target: Optional[str]) -> None:
        if target is None:
            self._file.unlink(missing_ok=True)
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps({"target": target}), encoding="utf-8")

    def active(self) -> bool:
        """A move is pending, running or paused."""
        return self._running() or self._target() is not None

    def _running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- protocol ----------------------------------------------------------
    def capabilities(self) -> Capabilities:
        pending = self._running() or self._target() is not None
        return Capabilities(can_start=pending, can_pause=pending, can_stop=pending)

    def report(self) -> ProcessReport:
        progress = Progress(self._done, self._total, "files") if self._total else None
        if self._running():
            if not self._gate.is_set():
                return ProcessReport(
                    State.RUNNING,
                    progress,
                    "Pausing after the current file",
                    winding_down=True,
                )
            text = f"File {min(self._done + 1, self._total)} of {self._total}"
            return ProcessReport(State.RUNNING, progress, text)
        if self._error is not None:
            return ProcessReport(
                State.FAILED, progress, "Move failed", error=self._error
            )
        if self._target() is not None:
            return ProcessReport(State.WAITING, progress, "Waiting to continue")
        return ProcessReport(State.IDLE, None, "No move")

    async def start(self) -> None:
        self._launch()

    async def pause(self) -> None:
        self._gate.clear()
        self._notify()

    async def resume(self) -> None:
        self._gate.set()
        self._launch()
        self._notify()

    async def stop(self) -> None:
        self._stopping = True
        self._gate.set()
        await asyncio.to_thread(self._write_target, None)
        self._notify()

    async def aclose(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            self._gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ---- entry points ------------------------------------------------------
    async def begin(self, target: str) -> None:
        """The owner asked to move the folder: that clears a stop or a pause."""
        await asyncio.to_thread(self._write_target, target)
        self._error = None
        state = self._registry.status(self.id).state
        if state in (State.STOPPED, State.PAUSED):
            await self._registry.command(self.id, "start")
        self._launch()

    def resume_at_startup(self) -> None:
        """A move left by the last run continues, unless the owner stopped it."""
        if self._target() is not None and not self._registry.blocked(self.id):
            self._launch()

    # ---- the work ------------------------------------------------------------
    def _launch(self) -> None:
        if self._running() or self._target() is None:
            return
        self._stopping = False
        self._error = None
        self._task = asyncio.get_running_loop().create_task(self._move_all())
        self._notify()

    def _todo(self, target: str) -> List[Dict[str, Any]]:
        """Thread. Records whose original is not in ``target`` yet."""
        prefix = to_posix_path(target).rstrip("/") + "/"
        return [
            r
            for r in self._db.list_upscale_records()
            if not str(r["original_path"]).startswith(prefix)
        ]

    async def _move_all(self) -> None:
        target = await asyncio.to_thread(self._target)
        if target is None:
            return
        try:
            records = await asyncio.to_thread(self._todo, target)
            self._done, self._total = 0, len(records)
            self._notify()
            for record in records:
                await self._gate.wait()
                if self._stopping:
                    return
                await asyncio.to_thread(self._move_one, record, target)
                self._done += 1
                self._notify()
            await self._gate.wait()
            if self._stopping:
                return
            await asyncio.to_thread(self._finish_setting, target)
            await asyncio.to_thread(self._write_target, None)
            self._done = self._total = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The rest stays where it is, still findable: Play tries again.
            logger.error("originals move failed: %s", type(exc).__name__)
            self._error = type(exc).__name__
        finally:
            self._task = None
            self._notify()

    def _move_one(self, record: Dict[str, Any], target: str) -> None:
        """Thread. The record is repointed only once the copy is whole."""
        source = Path(to_native_path(record["original_path"]))
        destination = Path(target) / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        with destination.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        if destination.stat().st_size != source.stat().st_size:
            destination.unlink(missing_ok=True)
            raise OSError("copy came out a different size")
        self._db.repoint_upscale_original(
            record["file_path"], to_posix_path(destination)
        )
        source.unlink(missing_ok=True)

    def _notify(self) -> None:
        try:
            self._registry.notify(self.id)
        except Exception:
            logger.exception("originals move notify failed")
