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
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Deque

from metascan.utils.app_paths import get_data_dir

logger = logging.getLogger(__name__)

# If the worker hasn't updated progress in this many seconds, consider it stale
WORKER_STALE_TIMEOUT = 120.0

# The worker's last statuses. One ends a run only once the worker process has
# exited: a cancelled worker still stamps, saves and marks its last batch
# after writing "cancelled", and a second worker started meanwhile would
# write the same index (the FAISS index must have one writer).
_FINAL_STATUSES = ("complete", "cancelled", "error")

# The worker's stderr is drained line by line into the server log (a pipe
# nobody reads holds 64 KB; a worker that wrote more would block forever and
# leave the queue indexing until a restart). The last lines are kept for the
# error message when it dies.
STDERR_TAIL_LINES = 200

# A worker that wrote its final status but has not exited after this long is
# logged (WARNING), and again as long again while it stays: it holds every
# later index build.
FINAL_EXIT_WARN_S = 60.0


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
        # A final status read while the worker was still running: reported
        # (and the queue cleaned up) once the process has exited.
        self._finished: Optional[Dict[str, Any]] = None
        self._finished_at: float = 0.0
        self._exit_warned_at: float = 0.0
        # The last lines of the current (or last) worker's stderr, filled by
        # the drain thread.
        self._stderr_tail: Deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_thread: Optional[threading.Thread] = None

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
        """Check if the embedding worker is currently running -- until its
        process has exited, also after it wrote its last status."""
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
        tag_with_vlm: bool = False,
    ) -> bool:
        """Start the embedding worker subprocess.

        Args:
            file_paths: List of file paths to process.
            clip_model_key: CLIP model size key (small/medium/large).
            device: Device to use (auto/cpu/cuda).
            db_path: Path to the database directory.
            compute_phash: Whether to compute perceptual hashes.
            video_keyframes: Number of keyframes to extract from videos.
            tag_with_vlm: When True, skip CLIP tagging and instead append
                each successfully-embedded file path to
                ``<queue_dir>/vlm_pending.jsonl`` for the VlmTagPump to
                drain (Task 17).

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
        self._finished = None
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
            "tag_with_vlm": tag_with_vlm,
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
            # stdout is unused (the worker reports through files); stderr is
            # drained at once -- never a PIPE nobody reads.
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._stderr_tail = deque(maxlen=STDERR_TAIL_LINES)
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process, self._stderr_tail),
                name="embedding-worker-stderr",
                daemon=True,
            )
            self._stderr_thread.start()
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

        # The worker wrote its last status earlier and was still running:
        # the run ends when the process does, with the status it left last
        # (a cancelled run whose final save failed ends in "error").
        if self._finished is not None:
            if process_exited:
                self._finish(self._read_final_status() or self._finished)
            else:
                self._warn_if_the_exit_is_late()
            return

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
                    # Long-running phases (model load / download / vocab
                    # encode) should not trigger a staleness warning even
                    # when the cadence between updates exceeds the timeout.
                    if status not in (
                        "loading_model",
                        "downloading_model",
                        "loading_vocab",
                        "encoding_vocab",
                    ):
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
            errors_count = data.get("errors_count", 0)

            if status == "starting":
                self._emit_progress(0, total, "Worker starting...")
            elif status == "downloading_model":
                dl_file = data.get("current_file", "")
                self._emit_progress(0, total, f"Downloading model... {dl_file}")
            elif status == "loading_model":
                self._emit_progress(0, total, "Loading CLIP model...")
            elif status == "loading_vocab":
                label = current_file or "Loading tag vocabulary..."
                self._emit_progress(0, total, label)
            elif status == "encoding_vocab":
                vc = data.get("vocab_current", 0)
                vt = data.get("vocab_total", 0)
                if vt:
                    self._emit_progress(
                        0, total, f"Encoding tag vocabulary {vc:,} / {vt:,}"
                    )
                else:
                    self._emit_progress(0, total, "Encoding tag vocabulary...")
            elif status == "processing":
                label = f"Indexing {current}/{total}"
                if current_file:
                    label += f" — {current_file}"
                if errors_count > 0:
                    label += f" ({errors_count} errors)"
                self._emit_progress(current, total, label)
            elif status in _FINAL_STATUSES:
                if status == "complete":
                    self._emit_progress(total, total, "Indexing complete")
                elif status == "cancelled":
                    self._emit_progress(
                        current, total, f"Indexing cancelled at {current}/{total}"
                    )
                if process_exited:
                    self._finish(data)
                else:
                    self._finished = data  # still finishing its last batch
                    self._finished_at = self._exit_warned_at = time.time()
                return
            elif status != "":
                # Forward unknown statuses to the UI rather than silently
                # dropping them — this keeps the parent forward-compatible
                # with new worker statuses added later.
                label = current_file or status
                self._emit_progress(current, total, label)

        # Check if process exited unexpectedly (not a terminal status)
        if process_exited:
            last_status = self._last_progress.get("status", "")
            if last_status not in _FINAL_STATUSES:
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

    def _warn_if_the_exit_is_late(self) -> None:
        """Log a worker that wrote its final status but has not exited for
        ``FINAL_EXIT_WARN_S`` (and again every as long): the queue keeps
        indexing until it exits, holding every later build."""
        now = time.time()
        if now - self._exit_warned_at < FINAL_EXIT_WARN_S:
            return
        self._exit_warned_at = now
        status = (self._finished or {}).get("status")
        pid = getattr(self._process, "pid", None)
        logger.warning(
            "The embedding worker (PID=%s) wrote its final status %r %.0f s ago "
            "and has not exited; new index builds wait for it",
            pid,
            status,
            now - self._finished_at,
        )

    @staticmethod
    def _drain_stderr(proc: "subprocess.Popen[str]", tail: Deque[str]) -> None:
        """Read the worker's stderr line by line until it closes: each line
        goes to the server log, the last ``STDERR_TAIL_LINES`` stay in
        ``tail``. Runs on a daemon thread from the spawn on -- the pipe
        buffer would otherwise fill (64 KB) and block the worker for good
        (a full disk makes it log an error per file, and logging's own error
        dumps go to stderr)."""
        stream = proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip()
                if not text:
                    continue
                tail.append(text)
                logger.info("embedding_worker: %s", text)
        except Exception:
            logger.exception("embedding worker stderr drainer crashed")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _read_final_status(self) -> Optional[Dict[str, Any]]:
        """The progress file when it holds a final status, else None."""
        try:
            with open(self._queue_dir / "progress_embedding.json", "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(data, dict) and data.get("status") in _FINAL_STATUSES:
            return data
        return None

    def _finish(self, data: Dict[str, Any]) -> None:
        """Report the end of a run whose worker process has exited (its
        last status ``data``), then clean up."""
        if data.get("status") == "error":
            self._emit_error(data.get("error", ""))
        else:
            self._emit_complete(data.get("current", 0))
        self._cleanup()

    def _read_process_stderr(self) -> str:
        """The last lines the worker wrote to stderr, once the drain has read
        them (it ends when the exited worker's pipe closes; bounded wait)."""
        thread = self._stderr_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        return "\n".join(self._stderr_tail)

    def get_last_progress(self) -> Dict[str, Any]:
        """Return the last progress data read from the worker."""
        return dict(self._last_progress)

    def _cleanup(self) -> None:
        """Clean up after the worker finishes."""
        self._process = None
        self._finished = None
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
