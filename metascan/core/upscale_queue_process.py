"""
Process-Based Upscale Queue

This module provides a process-based queue system for upscaling operations.
It replaces the threading-based approach with subprocess management and
file-based JSON communication to eliminate deadlocks and threading issues.
"""

import json
import time
import uuid
import signal
import logging
import subprocess
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, cast
from dataclasses import dataclass, asdict
from PyQt6.QtCore import QObject, pyqtSignal


class UpscaleStatus(Enum):
    """Status of an upscale task."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DOWNLOADING_MODELS = "downloading_models"
    PAUSED = "paused"


@dataclass
class UpscaleTask:
    """Represents an upscale task."""

    id: str
    file_path: str
    output_path: Optional[str]
    file_type: str  # 'image' or 'video'
    scale: int
    face_enhance: bool
    model: str
    preserve_metadata: bool
    status: UpscaleStatus
    progress: float
    error_message: Optional[str]
    created_at: float
    last_updated: float
    process_id: Optional[int] = None
    replace_original: bool = False
    interpolate_frames: bool = False
    interpolation_factor: int = 2
    fps_override: Optional[float] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "UpscaleTask":
        """Create from dictionary loaded from JSON."""
        data["status"] = UpscaleStatus(data["status"])
        return cls(**data)


class ProcessUpscaleQueue(QObject):
    """
    Process-based upscale queue manager.

    Uses subprocess execution and file-based communication instead of threading.
    This eliminates all threading-related deadlocks and synchronization issues.
    """

    # Signals for GUI updates (emitted via polling, not cross-thread)
    task_added = pyqtSignal(UpscaleTask)
    task_updated = pyqtSignal(UpscaleTask)
    task_removed = pyqtSignal(str)
    queue_changed = pyqtSignal()

    def __init__(self, queue_dir: Optional[Path] = None):
        """
        Initialize the process-based queue.

        Args:
            queue_dir: Directory for queue files (default: ~/.metascan/queue)
        """
        super().__init__()

        self.logger = logging.getLogger(__name__)

        # Queue directory setup
        if queue_dir is None:
            queue_dir = Path.home() / ".metascan" / "queue"
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.queue_file = self.queue_dir / "queue.json"

        # Process tracking
        self.active_processes: Dict[str, subprocess.Popen] = {}

        # Initialize queue file
        self._ensure_queue_file()

        # Clean up any stale processes/files on startup
        self._cleanup_stale_state()

    def _ensure_queue_file(self) -> None:
        """Ensure the queue file exists with valid structure."""
        if not self.queue_file.exists():
            initial_data = {
                "tasks": {},
                "created_at": time.time(),
                "last_updated": time.time(),
            }
            self._write_queue_file(initial_data)

    def _read_queue_file(self) -> Dict[str, Any]:
        """Read the queue file atomically."""
        try:
            with open(self.queue_file, "r") as f:
                return cast(Dict[str, Any], json.load(f))
        except Exception as e:
            self.logger.error(f"Failed to read queue file: {e}")
            # Return empty queue structure on error
            return {"tasks": {}, "created_at": time.time(), "last_updated": time.time()}

    def _write_queue_file(self, data: Dict[str, Any]) -> None:
        """Write the queue file atomically."""
        try:
            data["last_updated"] = time.time()

            # Write to temporary file first, then atomic rename
            temp_file = self.queue_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(data, f, indent=2)
            temp_file.rename(self.queue_file)

        except Exception as e:
            self.logger.error(f"Failed to write queue file: {e}")
            raise

    def _cleanup_stale_state(self) -> None:
        """Clean up any stale processes and files from previous runs."""
        try:
            # Clean up progress files
            for progress_file in self.queue_dir.glob("progress_*.json"):
                try:
                    progress_file.unlink()
                except Exception:
                    pass

            # Clean up signal files
            for signal_file in self.queue_dir.glob("cancel_*.signal"):
                try:
                    signal_file.unlink()
                except Exception:
                    pass

            # Reset any processing tasks to pending
            queue_data = self._read_queue_file()
            changed = False

            for task_id, task_data in queue_data["tasks"].items():
                if task_data.get("status") == "processing":
                    task_data["status"] = "pending"
                    task_data["progress"] = 0
                    task_data["process_id"] = None
                    changed = True

            if changed:
                self._write_queue_file(queue_data)

        except Exception as e:
            self.logger.error(f"Failed to cleanup stale state: {e}")

    def add_task(
        self,
        file_path: str,
        file_type: str,
        scale: int = 2,
        replace_original: bool = False,
        enhance_faces: bool = False,
        interpolate_frames: bool = False,
        interpolation_factor: int = 2,
        model_type: str = "general",
        fps_override: Optional[float] = None,
        preserve_metadata: bool = True,
    ) -> str:
        """
        Add a new task to the queue.

        Args:
            file_path: Path to input file
            file_type: Type of file ('image' or 'video')
            scale: Upscale factor
            replace_original: Whether to replace original file
            enhance_faces: Whether to enhance faces
            interpolate_frames: Whether to interpolate frames (video only)
            interpolation_factor: Frame interpolation factor
            model_type: Model to use for upscaling
            fps_override: Override FPS for video
            preserve_metadata: Whether to preserve metadata

        Returns:
            Task ID
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"

        # Use provided file_type (caller already determined this)

        # Create task
        task = UpscaleTask(
            id=task_id,
            file_path=str(file_path),
            output_path=None,  # Will be determined by worker
            file_type=file_type,
            scale=scale,
            face_enhance=enhance_faces,
            model=model_type,
            preserve_metadata=preserve_metadata,
            status=UpscaleStatus.PENDING,
            progress=0.0,
            error_message=None,
            created_at=time.time(),
            last_updated=time.time(),
            replace_original=replace_original,
            interpolate_frames=interpolate_frames,
            interpolation_factor=interpolation_factor,
            fps_override=fps_override,
        )

        # Add to queue
        queue_data = self._read_queue_file()
        queue_data["tasks"][task_id] = task.to_dict()
        self._write_queue_file(queue_data)

        self.logger.info(f"Added task {task_id} to queue: {file_path}")

        # Emit signal
        self.task_added.emit(task)
        self.queue_changed.emit()

        return task_id

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a task.

        Args:
            task_id: ID of task to cancel

        Returns:
            True if task was cancelled, False if not found
        """
        queue_data = self._read_queue_file()

        if task_id not in queue_data["tasks"]:
            return False

        task_data = queue_data["tasks"][task_id]
        current_status = UpscaleStatus(task_data["status"])

        if current_status in [
            UpscaleStatus.COMPLETED,
            UpscaleStatus.FAILED,
            UpscaleStatus.CANCELLED,
        ]:
            return False  # Already finished

        # Update status
        task_data["status"] = UpscaleStatus.CANCELLED.value
        task_data["last_updated"] = time.time()
        self._write_queue_file(queue_data)

        # Signal process to stop if running
        if current_status == UpscaleStatus.PROCESSING:
            self._signal_process_stop(task_id)

        # Emit signals
        task = UpscaleTask.from_dict(task_data)
        self.task_updated.emit(task)
        self.queue_changed.emit()

        self.logger.info(f"Cancelled task {task_id}")
        return True

    def remove_task(self, task_id: str) -> bool:
        """
        Remove a task from the queue.

        Args:
            task_id: ID of task to remove

        Returns:
            True if task was removed, False if not found
        """
        queue_data = self._read_queue_file()

        if task_id not in queue_data["tasks"]:
            return False

        task_data = queue_data["tasks"][task_id]
        current_status = UpscaleStatus(task_data["status"])

        # Cancel first if processing
        if current_status == UpscaleStatus.PROCESSING:
            self._signal_process_stop(task_id)

        # Remove from queue
        del queue_data["tasks"][task_id]
        self._write_queue_file(queue_data)

        # Clean up files
        self._cleanup_task_files(task_id)

        # Emit signals
        self.task_removed.emit(task_id)
        self.queue_changed.emit()

        self.logger.info(f"Removed task {task_id}")
        return True

    def _signal_process_stop(self, task_id: str) -> None:
        """Signal a process to stop gracefully."""
        # Create cancel signal file
        cancel_file = self.queue_dir / f"cancel_{task_id}.signal"
        try:
            cancel_file.touch()
        except Exception as e:
            self.logger.error(f"Failed to create cancel signal for {task_id}: {e}")

        # Send SIGTERM to process if we have it
        process = self.active_processes.get(task_id)
        if process and process.poll() is None:
            try:
                process.terminate()
                self.logger.info(f"Sent SIGTERM to process for task {task_id}")
            except Exception as e:
                self.logger.error(
                    f"Failed to terminate process for task {task_id}: {e}"
                )

    def _cleanup_task_files(self, task_id: str) -> None:
        """Clean up files associated with a task."""
        files_to_clean = [
            self.queue_dir / f"progress_{task_id}.json",
            self.queue_dir / f"cancel_{task_id}.signal",
        ]

        for file_path in files_to_clean:
            try:
                if file_path.exists():
                    file_path.unlink()
            except Exception as e:
                self.logger.error(f"Failed to clean up {file_path}: {e}")

    def get_all_tasks(self) -> List[UpscaleTask]:
        """Get all tasks in the queue."""
        queue_data = self._read_queue_file()
        tasks = []

        for task_data in queue_data["tasks"].values():
            try:
                task = UpscaleTask.from_dict(task_data)
                tasks.append(task)
            except Exception as e:
                self.logger.error(f"Failed to parse task data: {e}")
                continue

        # Sort by creation time
        tasks.sort(key=lambda t: t.created_at)
        return tasks

    def get_task(self, task_id: str) -> Optional[UpscaleTask]:
        """Get a specific task by ID."""
        queue_data = self._read_queue_file()

        if task_id not in queue_data["tasks"]:
            return None

        try:
            return UpscaleTask.from_dict(queue_data["tasks"][task_id])
        except Exception as e:
            self.logger.error(f"Failed to parse task {task_id}: {e}")
            return None

    def get_next_pending(self) -> Optional[UpscaleTask]:
        """Get the next pending task."""
        tasks = self.get_all_tasks()
        for task in tasks:
            if task.status == UpscaleStatus.PENDING:
                return task
        return None

    def clear_completed(self) -> None:
        """Remove all completed, failed, and cancelled tasks."""
        queue_data = self._read_queue_file()

        completed_statuses = [
            UpscaleStatus.COMPLETED.value,
            UpscaleStatus.FAILED.value,
            UpscaleStatus.CANCELLED.value,
        ]

        tasks_to_remove = []
        for task_id, task_data in queue_data["tasks"].items():
            if task_data.get("status") in completed_statuses:
                tasks_to_remove.append(task_id)

        # Remove tasks
        for task_id in tasks_to_remove:
            del queue_data["tasks"][task_id]
            self._cleanup_task_files(task_id)
            self.task_removed.emit(task_id)

        if tasks_to_remove:
            self._write_queue_file(queue_data)
            self.queue_changed.emit()

        self.logger.info(f"Cleared {len(tasks_to_remove)} completed tasks")

    def pause_queue(self) -> int:
        """
        Pause the queue by changing all pending tasks to paused status.
        This acts as a "poison pill" - when the current task completes,
        processing will stop instead of starting the next task.

        Returns:
            Number of tasks paused
        """
        queue_data = self._read_queue_file()
        paused_task_ids = []

        # First pass: update all pending tasks to paused
        for task_id, task_data in queue_data["tasks"].items():
            if task_data.get("status") == UpscaleStatus.PENDING.value:
                queue_data["tasks"][task_id]["status"] = UpscaleStatus.PAUSED.value
                queue_data["tasks"][task_id]["last_updated"] = time.time()
                paused_task_ids.append(task_id)

        if paused_task_ids:
            # Write the queue file BEFORE emitting signals
            self._write_queue_file(queue_data)
            self.queue_changed.emit()
            self.logger.info(f"Paused {len(paused_task_ids)} pending tasks")

            # Now emit update signals for each paused task
            for task_id in paused_task_ids:
                task = UpscaleTask.from_dict(queue_data["tasks"][task_id])
                self.task_updated.emit(task)
        else:
            self.logger.debug("No pending tasks to pause")

        return len(paused_task_ids)

    def resume_queue(self) -> int:
        """
        Resume the queue by changing all paused tasks back to pending status
        and starting processing.

        Returns:
            Number of tasks resumed
        """
        queue_data = self._read_queue_file()
        resumed_task_ids = []

        # First pass: update all paused tasks to pending
        for task_id, task_data in queue_data["tasks"].items():
            if task_data.get("status") == UpscaleStatus.PAUSED.value:
                queue_data["tasks"][task_id]["status"] = UpscaleStatus.PENDING.value
                queue_data["tasks"][task_id]["last_updated"] = time.time()
                resumed_task_ids.append(task_id)

        if resumed_task_ids:
            # Write the queue file BEFORE emitting signals
            self._write_queue_file(queue_data)
            self.queue_changed.emit()
            self.logger.info(f"Resumed {len(resumed_task_ids)} paused tasks")

            # Now emit update signals for each resumed task
            for task_id in resumed_task_ids:
                task = UpscaleTask.from_dict(queue_data["tasks"][task_id])
                self.task_updated.emit(task)

            # Start processing the resumed tasks
            self.start_processing()
        else:
            self.logger.debug("No paused tasks to resume")

        return len(resumed_task_ids)

    def start_processing(self) -> None:
        """Start processing pending tasks."""
        pending_task = self.get_next_pending()

        if pending_task is None:
            return  # No pending tasks

        # Check if we already have a process running
        if self.active_processes:
            return  # Only run one task at a time for now

        self._start_task_process(pending_task)

    def _start_task_process(self, task: UpscaleTask) -> None:
        """Start a subprocess for processing a task."""
        try:
            # Update status to processing
            queue_data = self._read_queue_file()
            if task.id in queue_data["tasks"]:
                queue_data["tasks"][task.id]["status"] = UpscaleStatus.PROCESSING.value
                queue_data["tasks"][task.id]["last_updated"] = time.time()
                self._write_queue_file(queue_data)

            # Start worker process
            worker_script = (
                Path(__file__).parent.parent / "workers" / "upscale_worker.py"
            )
            cmd = ["python", str(worker_script), task.id, str(self.queue_dir)]

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            self.active_processes[task.id] = process

            # Update task with process ID
            queue_data = self._read_queue_file()
            if task.id in queue_data["tasks"]:
                queue_data["tasks"][task.id]["process_id"] = process.pid
                self._write_queue_file(queue_data)

            self.logger.info(f"Started worker process {process.pid} for task {task.id}")

            # Emit signal
            updated_task = self.get_task(task.id)
            if updated_task:
                self.task_updated.emit(updated_task)

        except Exception as e:
            self.logger.error(f"Failed to start process for task {task.id}: {e}")
            # Mark task as failed
            self._mark_task_failed(task.id, str(e))

    def _mark_task_failed(self, task_id: str, error_message: str) -> None:
        """Mark a task as failed."""
        queue_data = self._read_queue_file()
        if task_id in queue_data["tasks"]:
            queue_data["tasks"][task_id]["status"] = UpscaleStatus.FAILED.value
            queue_data["tasks"][task_id]["error_message"] = error_message
            queue_data["tasks"][task_id]["last_updated"] = time.time()
            self._write_queue_file(queue_data)

            # Emit signal
            task = UpscaleTask.from_dict(queue_data["tasks"][task_id])
            self.task_updated.emit(task)

    def poll_updates(self) -> None:
        """
        Poll for updates from worker processes.

        This should be called periodically by the GUI via QTimer.
        """
        self._check_process_status()
        self._update_progress_from_files()
        self._start_next_pending_task()

    def _check_process_status(self) -> None:
        """Check status of active processes."""
        completed_tasks = []

        for task_id, process in list(self.active_processes.items()):
            if process.poll() is not None:
                # Process has finished
                exit_code = process.returncode
                completed_tasks.append((task_id, exit_code, process))
                del self.active_processes[task_id]

        # Handle completed processes
        for task_id, exit_code, process in completed_tasks:
            self._handle_process_completion(task_id, exit_code, process)

    def _handle_process_completion(
        self, task_id: str, exit_code: int, process: subprocess.Popen
    ) -> None:
        """Handle completion of a worker process."""
        try:
            stdout, stderr = process.communicate()

            if exit_code == 0:
                self.logger.info(f"Task {task_id} completed successfully")
            else:
                self.logger.error(f"Task {task_id} failed with exit code {exit_code}")
                if stderr:
                    self.logger.error(f"Task {task_id} stderr: {stderr}")

            # The worker process should have already updated the task status
            # Just emit the update signal
            task = self.get_task(task_id)
            if task:
                self.task_updated.emit(task)
                self.queue_changed.emit()

            # Clean up task files
            self._cleanup_task_files(task_id)

        except Exception as e:
            self.logger.error(f"Failed to handle completion of task {task_id}: {e}")

    def _update_progress_from_files(self) -> None:
        """Update task progress from progress files."""
        for progress_file in self.queue_dir.glob("progress_*.json"):
            try:
                task_id = progress_file.stem.replace("progress_", "")

                with open(progress_file, "r") as f:
                    progress_data = json.load(f)

                # Update task progress
                queue_data = self._read_queue_file()
                if task_id in queue_data["tasks"]:
                    queue_data["tasks"][task_id]["progress"] = progress_data.get(
                        "progress", 0
                    )
                    if progress_data.get("status"):
                        queue_data["tasks"][task_id]["status"] = progress_data["status"]
                    queue_data["tasks"][task_id]["last_updated"] = time.time()
                    self._write_queue_file(queue_data)

                    # Emit update signal
                    task = UpscaleTask.from_dict(queue_data["tasks"][task_id])
                    self.task_updated.emit(task)

            except Exception as e:
                self.logger.error(
                    f"Failed to update progress from {progress_file}: {e}"
                )

    def _start_next_pending_task(self) -> None:
        """Start the next pending task if no tasks are currently processing."""
        if not self.active_processes:  # No active processes
            self.start_processing()

    def shutdown(self) -> None:
        """Shutdown the queue and clean up processes."""
        self.logger.info("Shutting down upscale queue")

        # Terminate all active processes
        for task_id, process in self.active_processes.items():
            try:
                if process.poll() is None:
                    process.terminate()
                    # Give it a moment to terminate gracefully
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()

                self.logger.info(f"Terminated process for task {task_id}")
            except Exception as e:
                self.logger.error(
                    f"Failed to terminate process for task {task_id}: {e}"
                )

        self.active_processes.clear()

        # Clean up any remaining progress files
        for progress_file in self.queue_dir.glob("progress_*.json"):
            try:
                progress_file.unlink()
            except Exception:
                pass
