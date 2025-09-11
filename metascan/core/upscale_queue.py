"""
Upscale queue management for persistent processing of media files.
"""

import json
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal, QThread
import logging


class UpscaleStatus(Enum):
    """Status of an upscale task."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class UpscaleTask:
    """Represents a single upscale task."""

    id: str
    file_path: str
    file_type: str  # "image" or "video"
    scale: int
    created_at: str
    replace_original: bool = True  # Always replace original now
    status: UpscaleStatus = UpscaleStatus.PENDING
    progress: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    output_path: Optional[str] = None
    enhance_faces: bool = False
    interpolate_frames: bool = False
    interpolation_factor: int = 2
    model_type: str = "general"
    fps_override: Optional[float] = None
    preserve_metadata: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UpscaleTask":
        """Create from dictionary."""
        data["status"] = UpscaleStatus(data["status"])
        return cls(**data)


class UpscaleQueue(QObject):
    """Manages the queue of upscale tasks with persistence."""

    # Signals
    task_added = pyqtSignal(UpscaleTask)
    task_updated = pyqtSignal(UpscaleTask)
    task_removed = pyqtSignal(str)  # task_id
    queue_changed = pyqtSignal()

    def __init__(self, queue_file: Path):
        """
        Initialize the upscale queue.

        Args:
            queue_file: Path to the JSON file for persistence
        """
        super().__init__()
        self.queue_file = queue_file
        self.tasks: Dict[str, UpscaleTask] = {}
        self._lock = threading.Lock()
        self._next_id = 1

        # Load existing queue
        self._load_queue()

    def _load_queue(self) -> None:
        """Load queue from file."""
        if self.queue_file.exists():
            try:
                with open(self.queue_file, "r") as f:
                    data = json.load(f)
                    for task_data in data.get("tasks", []):
                        task = UpscaleTask.from_dict(task_data)
                        self.tasks[task.id] = task
                    self._next_id = data.get("next_id", 1)
            except Exception as e:
                logging.error(f"Failed to load queue: {e}")

    def _save_queue(self) -> None:
        """Save queue to file."""
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "tasks": [task.to_dict() for task in self.tasks.values()],
                "next_id": self._next_id,
            }
            with open(self.queue_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save queue: {e}")

    def add_task(
        self,
        file_path: str,
        file_type: str,
        scale: int,
        replace_original: bool,
        enhance_faces: bool = False,
        interpolate_frames: bool = False,
        interpolation_factor: int = 2,
        model_type: str = "general",
        fps_override: Optional[float] = None,
        preserve_metadata: bool = True,
    ) -> UpscaleTask:
        """
        Add a new task to the queue.

        Args:
            file_path: Path to the media file
            file_type: Type of file ("image" or "video")
            scale: Upscaling factor
            replace_original: Whether to replace the original file
            enhance_faces: Whether to enhance faces using GFPGAN
            interpolate_frames: Whether to interpolate frames (video only)
            interpolation_factor: Frame interpolation factor (2, 4, 8)
            model_type: Type of model to use ('general' or 'anime')
            fps_override: Custom FPS for video output (None to keep original)
            preserve_metadata: Whether to preserve original file metadata

        Returns:
            The created task
        """
        with self._lock:
            task_id = f"task_{self._next_id}"
            self._next_id += 1

            task = UpscaleTask(
                id=task_id,
                file_path=file_path,
                file_type=file_type,
                scale=scale,
                replace_original=replace_original,
                status=UpscaleStatus.PENDING,
                progress=0.0,
                created_at=datetime.now().isoformat(),
                enhance_faces=enhance_faces,
                interpolate_frames=interpolate_frames,
                interpolation_factor=interpolation_factor,
                model_type=model_type,
                fps_override=fps_override,
                preserve_metadata=preserve_metadata,
            )

            self.tasks[task_id] = task
            self._save_queue()

        self.task_added.emit(task)
        self.queue_changed.emit()
        return task

    def update_task(
        self,
        task_id: str,
        status: Optional[UpscaleStatus] = None,
        progress: Optional[float] = None,
        error_message: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> None:
        """Update a task's status."""
        with self._lock:
            if task_id not in self.tasks:
                return

            task = self.tasks[task_id]

            if status is not None:
                task.status = status
                if status == UpscaleStatus.PROCESSING:
                    task.started_at = datetime.now().isoformat()
                elif status in [
                    UpscaleStatus.COMPLETED,
                    UpscaleStatus.FAILED,
                    UpscaleStatus.CANCELLED,
                ]:
                    task.completed_at = datetime.now().isoformat()

            if progress is not None:
                task.progress = progress

            if error_message is not None:
                task.error_message = error_message

            if output_path is not None:
                task.output_path = output_path

            self._save_queue()

        self.task_updated.emit(task)
        self.queue_changed.emit()

    def remove_task(self, task_id: str) -> None:
        """Remove a task from the queue."""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                # If the task is currently processing, mark it as cancelled first
                # so the worker can detect and stop processing it
                if task.status == UpscaleStatus.PROCESSING:
                    task.status = UpscaleStatus.CANCELLED
                    self._save_queue()
                    self.task_updated.emit(task)

                    # Give the worker a moment to detect the cancellation
                    # Then actually remove it using QTimer (Qt-native, no threading issues)
                    def delayed_remove():
                        with self._lock:
                            if task_id in self.tasks:
                                del self.tasks[task_id]
                                self._save_queue()
                                self.task_removed.emit(task_id)
                                self.queue_changed.emit()

                    # Use QTimer.singleShot to delay removal by 500ms on main thread
                    from PyQt6.QtCore import QTimer

                    QTimer.singleShot(500, delayed_remove)
                else:
                    # Task is not processing, safe to remove immediately
                    del self.tasks[task_id]
                    self._save_queue()
                    self.task_removed.emit(task_id)
                    self.queue_changed.emit()
            else:
                # Task not found, still emit signals for UI consistency
                self.task_removed.emit(task_id)
                self.queue_changed.emit()

    def get_next_pending(self) -> Optional[UpscaleTask]:
        """Get the next pending task."""
        with self._lock:
            for task in self.tasks.values():
                if task.status == UpscaleStatus.PENDING:
                    return task
        return None

    def get_all_tasks(self) -> List[UpscaleTask]:
        """Get all tasks in the queue."""
        with self._lock:
            return list(self.tasks.values())

    def get_task(self, task_id: str) -> Optional[UpscaleTask]:
        """Get a specific task."""
        with self._lock:
            return self.tasks.get(task_id)

    def cancel_task(self, task_id: str) -> None:
        """Cancel a pending or processing task."""
        self.update_task(task_id, status=UpscaleStatus.CANCELLED)

    def clear_completed(self) -> None:
        """Remove all completed tasks."""
        with self._lock:
            completed_ids = [
                task_id
                for task_id, task in self.tasks.items()
                if task.status
                in [
                    UpscaleStatus.COMPLETED,
                    UpscaleStatus.FAILED,
                    UpscaleStatus.CANCELLED,
                ]
            ]
            for task_id in completed_ids:
                del self.tasks[task_id]
            self._save_queue()

        for task_id in completed_ids:
            self.task_removed.emit(task_id)

        if completed_ids:
            self.queue_changed.emit()

    def reset_processing_tasks(self) -> None:
        """Reset any processing tasks to pending (useful on app restart)."""
        with self._lock:
            for task in self.tasks.values():
                if task.status == UpscaleStatus.PROCESSING:
                    task.status = UpscaleStatus.PENDING
                    task.progress = 0.0
                    task.started_at = None
            self._save_queue()

        self.queue_changed.emit()


class UpscaleWorker(QThread):
    """Worker thread for processing upscale tasks."""

    # Signals
    progress_updated = pyqtSignal(str, float)  # task_id, progress
    task_completed = pyqtSignal(str, str)  # task_id, output_path
    task_failed = pyqtSignal(str, str)  # task_id, error_message
    database_updated = pyqtSignal(str, int, int)  # file_path, width, height

    def __init__(
        self, queue: UpscaleQueue, upscaler: Any, db_manager: Any = None
    ) -> None:
        """
        Initialize the worker.

        Args:
            queue: The upscale queue
            upscaler: MediaUpscaler instance
            db_manager: Database manager for updating media info (optional)
        """
        super().__init__()
        self.queue = queue
        self.upscaler = upscaler
        self.db_manager = db_manager
        self._stop_requested = False
        self._current_task_id: Optional[str] = None
        self.logger = logging.getLogger(__name__)

    def stop(self) -> None:
        """Request the worker to stop."""
        self._stop_requested = True

    def run(self) -> None:
        """Process tasks from the queue."""
        while not self._stop_requested:
            # Get next task
            task = self.queue.get_next_pending()

            if not task:
                # No tasks, wait a bit
                self.msleep(1000)
                continue

            # Check if task was cancelled before we start processing
            current_task = self.queue.get_task(task.id)
            if not current_task or current_task.status == UpscaleStatus.CANCELLED:
                continue

            self._current_task_id = task.id

            # Update task status
            self.queue.update_task(task.id, status=UpscaleStatus.PROCESSING)

            try:
                # Process the task
                success = self._process_task(task)

                # Check again if task was cancelled after processing
                current_task = self.queue.get_task(task.id)
                if not current_task or current_task.status == UpscaleStatus.CANCELLED:
                    self.logger.info(f"Task {task.id} was cancelled after processing")
                    continue

                if success:
                    # Determine output path
                    input_path = Path(task.file_path)
                    if task.replace_original:
                        output_path = str(input_path)
                    else:
                        output_path = str(
                            input_path.with_name(
                                f"{input_path.stem}_x{task.scale}{input_path.suffix}"
                            )
                        )

                    # Update database with new dimensions if database manager is available
                    if self.db_manager and hasattr(
                        self.db_manager, "update_media_dimensions"
                    ):
                        try:
                            # Get the new dimensions from the output file
                            output_file_path = Path(output_path)
                            if output_file_path.exists():
                                (
                                    new_width,
                                    new_height,
                                ) = self.upscaler.get_media_dimensions(output_file_path)
                                if new_width > 0 and new_height > 0:
                                    # Update the database - file was replaced at original path
                                    if self.db_manager.update_media_dimensions(
                                        input_path, new_width, new_height
                                    ):
                                        updated_path = str(input_path)

                                    if updated_path:
                                        self.logger.info(
                                            f"Updated database dimensions for {output_path}: {new_width}x{new_height}"
                                        )
                                        self.database_updated.emit(
                                            updated_path, new_width, new_height
                                        )
                        except Exception as e:
                            self.logger.error(
                                f"Failed to update database dimensions: {e}"
                            )

                    self.queue.update_task(
                        task.id,
                        status=UpscaleStatus.COMPLETED,
                        progress=100.0,
                        output_path=output_path,
                    )
                    self.task_completed.emit(task.id, output_path)
                else:
                    raise Exception("Processing failed")

            except Exception as e:
                error_msg = str(e)
                self.logger.error(f"Task {task.id} failed: {error_msg}")
                self.queue.update_task(
                    task.id, status=UpscaleStatus.FAILED, error_message=error_msg
                )
                self.task_failed.emit(task.id, error_msg)

            self._current_task_id = None

    def _process_task(self, task: UpscaleTask) -> bool:
        """Process a single task."""
        input_path = Path(task.file_path)

        if not input_path.exists():
            raise FileNotFoundError(f"File not found: {task.file_path}")

        # Determine output path - always use a suffix initially
        # The processing function will handle moving to original location if replace_original=True
        output_path = input_path.with_name(
            f"{input_path.stem}_x{task.scale}{input_path.suffix}"
        )

        # Progress callback
        def progress_callback(progress: float) -> bool:
            if self._stop_requested:
                return False

            # Check if the task has been cancelled
            current_task = self.queue.get_task(task.id)
            if not current_task or current_task.status == UpscaleStatus.CANCELLED:
                self.logger.info(f"Task {task.id} was cancelled during processing")
                return False

            self.queue.update_task(task.id, progress=progress)
            self.progress_updated.emit(task.id, progress)
            return True

        # Process based on file type
        if task.file_type == "image":
            result = self.upscaler.process_image(
                input_path,
                output_path,
                scale=task.scale,
                enhance_faces=task.enhance_faces,
                model_type=task.model_type,
                preserve_metadata=task.preserve_metadata,
                progress_callback=progress_callback,
            )
            return bool(result)
        elif task.file_type == "video":
            if task.interpolate_frames:
                # For interpolation, we'll do it in stages: interpolate first, then upscale if needed
                if task.scale > 1:
                    # First interpolate, then upscale
                    temp_interpolated = input_path.with_name(
                        f"{input_path.stem}_temp_interp{input_path.suffix}"
                    )

                    # Interpolate frames
                    interp_result = self.upscaler.interpolate_frames_rife(
                        input_path,
                        temp_interpolated,
                        interpolation_factor=task.interpolation_factor,
                        replace_original=False,
                        progress_callback=lambda p: progress_callback(p * 0.5),
                    )

                    if not interp_result:
                        return False

                    # Then upscale the interpolated video
                    result = self.upscaler.process_video(
                        temp_interpolated,
                        output_path,
                        scale=task.scale,
                        fps=task.fps_override,
                        enhance_faces=task.enhance_faces,
                        model_type=task.model_type,
                        preserve_metadata=task.preserve_metadata,
                        progress_callback=lambda p: progress_callback(50 + p * 0.5),
                    )

                    # Clean up temp file
                    if temp_interpolated.exists():
                        temp_interpolated.unlink()

                    return bool(result)
                else:
                    # Just interpolate
                    result = self.upscaler.interpolate_frames_rife(
                        input_path,
                        output_path,
                        interpolation_factor=task.interpolation_factor,
                        replace_original=task.replace_original,
                        progress_callback=progress_callback,
                    )
                    return bool(result)
            else:
                # Regular video upscaling
                result = self.upscaler.process_video(
                    input_path,
                    output_path,
                    scale=task.scale,
                    fps=task.fps_override,
                    enhance_faces=task.enhance_faces,
                    model_type=task.model_type,
                    preserve_metadata=task.preserve_metadata,
                    progress_callback=progress_callback,
                )
                return bool(result)
        else:
            raise ValueError(f"Unknown file type: {task.file_type}")
