"""
Process-Based Embedding Queue

Manages a background subprocess that computes pHash and CLIP embeddings
for media files. Communicates via JSON files following the same pattern
as the upscale queue.
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

# If the worker hasn't updated progress in this many seconds, consider it stale
WORKER_STALE_TIMEOUT = 120.0


class EmbeddingQueue:
    """Process-based embedding queue manager.

    Spawns a subprocess worker to compute embeddings, and polls for
    progress updates via JSON files.

    Connect callbacks to receive updates:
        eq.on_progress = lambda current, total, status: ...
        eq.on_complete = lambda total: ...
        eq.on_error = lambda msg: ...
    """

    def __init__(self) -> None:
        self._queue_dir = get_data_dir() / "similarity"
        self._queue_dir.mkdir(parents=True, exist_ok=True)

        self._process: Optional[subprocess.Popen] = None
        self._last_progress: Dict[str, Any] = {}
        self._start_time: float = 0.0
        self._last_progress_time: float = 0.0

        # Callback-based signals (replace PyQt pyqtSignal)
        self.on_progress: Optional[Callable[[int, int, str], None]] = None
        self.on_complete: Optional[Callable[[int], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    def _emit_progress(self, current: int, total: int, status: str) -> None:
        if self.on_progress:
            self.on_progress(current, total, status)

    def _emit_complete(self, total: int) -> None:
        if self.on_complete:
            self.on_complete(total)

    def _emit_error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(msg)

    @property
    def index_dir(self) -> Path:
        """Directory where FAISS index files are stored."""
        return self._queue_dir

    def is_indexing(self) -> bool:
        """Check if the embedding worker is currently running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def start_indexing(
        self,
        file_paths: List[str],
        clip_model_key: str = "small",
        device: str = "auto",
        db_path: str = "",
        compute_phash: bool = True,
        video_keyframes: int = 4,
    ) -> bool:
        """Start the embedding worker subprocess.

        Args:
            file_paths: List of file paths to process.
            clip_model_key: CLIP model size key (small/medium/large).
            device: Device to use (auto/cpu/cuda).
            db_path: Path to the database directory.
            compute_phash: Whether to compute perceptual hashes.
            video_keyframes: Number of keyframes to extract from videos.

        Returns:
            True if the worker was started, False if already running.
        """
        if self.is_indexing():
            logger.warning("Embedding worker is already running")
            return False

        if not file_paths:
            self._emit_complete(0)
            return True

        # Clean up old signal/progress files
        cancel_file = self._queue_dir / "cancel_embedding.signal"
        if cancel_file.exists():
            cancel_file.unlink()

        progress_file = self._queue_dir / "progress_embedding.json"
        if progress_file.exists():
            progress_file.unlink()

        # Also clean up stale temp file from previous run
        temp_file = progress_file.with_suffix(".tmp")
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

        self._last_progress = {}
        self._start_time = time.time()
        self._last_progress_time = time.time()

        # Write the task file
        task = {
            "model_key": clip_model_key,
            "device": device,
            "file_paths": file_paths,
            "db_path": db_path,
            "index_dir": str(self._queue_dir),
            "compute_phash": compute_phash,
            "video_keyframes": video_keyframes,
        }
        task_file = self._queue_dir / "embedding_task.json"
        temp_task = task_file.with_suffix(".tmp")
        with open(temp_task, "w") as f:
            json.dump(task, f)
        os.replace(str(temp_task), str(task_file))

        # Spawn the worker
        worker_script = Path(__file__).parent.parent / "workers" / "embedding_worker.py"
        cmd = [sys.executable, str(worker_script), str(self._queue_dir)]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            logger.info(
                f"Started embedding worker (PID={self._process.pid}, "
                f"files={len(file_paths)}, model={clip_model_key})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to start embedding worker: {e}")
            self._emit_error(str(e))
            return False

    def cancel_indexing(self) -> None:
        """Cancel the running embedding worker."""
        if not self.is_indexing():
            return

        # Create cancel signal file
        cancel_file = self._queue_dir / "cancel_embedding.signal"
        cancel_file.touch()

        # Also send SIGTERM
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass

        logger.info("Embedding indexing cancelled")

    def poll_updates(self) -> None:  # noqa: C901
        """Read progress from the worker and emit callbacks.

        Should be called periodically (e.g. every 500ms) while indexing.
        """
        # No active worker to monitor. Bail before re-reading the stale
        # progress file left on disk by the previous run; otherwise the
        # stale-progress branch below fires a warning (and a spurious
        # 'progress' callback) on every tick forever.
        if self._process is None:
            return

        # First check: did the process exit?
        process_exited = self._process.poll() is not None

        progress_file = self._queue_dir / "progress_embedding.json"

        if not progress_file.exists():
            # No progress file yet — check for early process death
            if process_exited:
                stderr_text = self._read_process_stderr()
                error_msg = "Worker exited before writing progress"
                if stderr_text:
                    error_msg += f": {stderr_text[:500]}"
                logger.error(error_msg)
                self._emit_error(error_msg)
                self._cleanup()
                return

            # Check for startup timeout (no progress after 60s means likely crash)
            elapsed = time.time() - self._start_time
            if self._start_time > 0 and elapsed > 60:
                logger.warning(
                    f"No progress file after {elapsed:.0f}s — worker may have crashed"
                )
                self._emit_progress(0, 0, f"Waiting for worker ({elapsed:.0f}s)...")
            return

        # Read progress file
        try:
            with open(progress_file, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not read progress file: {e}")
            return

        # Avoid re-emitting identical progress
        if data == self._last_progress:
            # Check for stale progress (worker hung)
            if not process_exited:
                stale_seconds = time.time() - self._last_progress_time
                if stale_seconds > WORKER_STALE_TIMEOUT:
                    status = data.get("status", "")
                    # loading_model and downloading_model can take a long time
                    if status not in ("loading_model", "downloading_model"):
                        logger.warning(
                            f"Worker progress stale for {stale_seconds:.0f}s "
                            f"(status={status})"
                        )
                        self._emit_progress(
                            data.get("current", 0),
                            data.get("total", 0),
                            f"Worker may be stuck ({stale_seconds:.0f}s no update)...",
                        )
            # Fall through to check process exit below
        else:
            self._last_progress = data
            self._last_progress_time = time.time()

            status = data.get("status", "")
            current = data.get("current", 0)
            total = data.get("total", 0)
            current_file = data.get("current_file", "")
            error = data.get("error", "")
            errors_count = data.get("errors_count", 0)

            if status == "starting":
                self._emit_progress(0, total, "Worker starting...")
            elif status == "downloading_model":
                dl_file = data.get("current_file", "")
                self._emit_progress(0, total, f"Downloading model... {dl_file}")
            elif status == "loading_model":
                self._emit_progress(0, total, "Loading CLIP model...")
            elif status == "processing":
                label = f"Indexing {current}/{total}"
                if current_file:
                    label += f" — {current_file}"
                if errors_count > 0:
                    label += f" ({errors_count} errors)"
                self._emit_progress(current, total, label)
            elif status == "complete":
                self._emit_progress(total, total, "Indexing complete")
                self._emit_complete(current)
                self._cleanup()
                return
            elif status == "cancelled":
                self._emit_progress(
                    current, total, f"Indexing cancelled at {current}/{total}"
                )
                self._emit_complete(current)
                self._cleanup()
                return
            elif status == "error":
                self._emit_error(error)
                self._cleanup()
                return

        # Check if process exited unexpectedly (not a terminal status)
        if process_exited:
            last_status = self._last_progress.get("status", "")
            if last_status not in ("complete", "cancelled", "error"):
                stderr_text = self._read_process_stderr()
                rc = self._process.returncode if self._process else -1
                error_msg = (
                    f"Worker exited unexpectedly (exit code {rc}, "
                    f"last status: {last_status!r})"
                )
                if stderr_text:
                    # Take last 500 chars of stderr (most useful part)
                    error_msg += f"\n{stderr_text[-500:]}"
                logger.error(error_msg)
                self._emit_error(error_msg)
                self._cleanup()

    def _read_process_stderr(self) -> str:
        """Read stderr from the subprocess (non-blocking best-effort)."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            return self._process.stderr.read() or ""
        except Exception:
            return ""

    def get_last_progress(self) -> Dict[str, Any]:
        """Return the last progress data read from the worker."""
        return dict(self._last_progress)

    def _cleanup(self) -> None:
        """Clean up after the worker finishes."""
        self._process = None
        self._start_time = 0.0
        # Clean up task file but leave progress for debugging
        task_file = self._queue_dir / "embedding_task.json"
        if task_file.exists():
            try:
                task_file.unlink()
            except Exception:
                pass
        cancel_file = self._queue_dir / "cancel_embedding.signal"
        if cancel_file.exists():
            try:
                cancel_file.unlink()
            except Exception:
                pass
