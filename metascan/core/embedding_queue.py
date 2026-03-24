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
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import QObject, pyqtSignal

from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

# If the worker hasn't updated progress in this many seconds, consider it stale
WORKER_STALE_TIMEOUT = 120.0


class EmbeddingQueue(QObject):
    """Process-based embedding queue manager.

    Spawns a subprocess worker to compute embeddings, and polls for
    progress updates via JSON files. Owns its own poll timer so that
    progress monitoring continues even when the settings dialog is closed.
    """

    progress_updated = pyqtSignal(int, int, str)  # current, total, status_text
    indexing_complete = pyqtSignal(int)  # total files indexed
    indexing_error = pyqtSignal(str)  # error message

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._queue_dir = get_data_dir() / "similarity"
        self._queue_dir.mkdir(parents=True, exist_ok=True)

        self._process: Optional[subprocess.Popen] = None
        self._last_progress: Dict[str, Any] = {}
        self._start_time: float = 0.0
        self._last_progress_time: float = 0.0

        # Self-owned poll timer — runs as long as the queue object lives
        from PyQt6.QtCore import QTimer

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.poll_updates)
        self._poll_timer.setInterval(500)

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
            self.indexing_complete.emit(0)
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
        worker_script = (
            Path(__file__).parent.parent / "workers" / "embedding_worker.py"
        )
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
            self._poll_timer.start()
            return True

        except Exception as e:
            logger.error(f"Failed to start embedding worker: {e}")
            self.indexing_error.emit(str(e))
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

    def poll_updates(self) -> None:
        """Read progress from the worker and emit signals.

        Called periodically by a QTimer in the main window.
        """
        # First check: did the process exit?
        process_exited = (
            self._process is not None and self._process.poll() is not None
        )

        progress_file = self._queue_dir / "progress_embedding.json"

        if not progress_file.exists():
            # No progress file yet — check for early process death
            if process_exited:
                stderr_text = self._read_process_stderr()
                error_msg = f"Worker exited before writing progress"
                if stderr_text:
                    error_msg += f": {stderr_text[:500]}"
                logger.error(error_msg)
                self.indexing_error.emit(error_msg)
                self._cleanup()
                return

            # Check for startup timeout (no progress after 60s means likely crash)
            elapsed = time.time() - self._start_time
            if self._start_time > 0 and elapsed > 60:
                logger.warning(
                    f"No progress file after {elapsed:.0f}s — worker may have crashed"
                )
                self.progress_updated.emit(
                    0, 0, f"Waiting for worker ({elapsed:.0f}s)..."
                )
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
                        self.progress_updated.emit(
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
                self.progress_updated.emit(0, total, "Worker starting...")
            elif status == "downloading_model":
                dl_file = data.get("current_file", "")
                self.progress_updated.emit(
                    0, total, f"Downloading model... {dl_file}"
                )
            elif status == "loading_model":
                self.progress_updated.emit(0, total, "Loading CLIP model...")
            elif status == "processing":
                label = f"Indexing {current}/{total}"
                if current_file:
                    label += f" — {current_file}"
                if errors_count > 0:
                    label += f" ({errors_count} errors)"
                self.progress_updated.emit(current, total, label)
            elif status == "complete":
                self.progress_updated.emit(total, total, "Indexing complete")
                self.indexing_complete.emit(current)
                self._cleanup()
                return
            elif status == "cancelled":
                self.progress_updated.emit(
                    current, total, f"Indexing cancelled at {current}/{total}"
                )
                self.indexing_complete.emit(current)
                self._cleanup()
                return
            elif status == "error":
                self.indexing_error.emit(error)
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
                self.indexing_error.emit(error_msg)
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
        """Return the last progress data read from the worker.

        Used by the settings dialog to restore UI state on reopen.
        """
        return dict(self._last_progress)

    def _cleanup(self) -> None:
        """Clean up after the worker finishes."""
        self._poll_timer.stop()
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
