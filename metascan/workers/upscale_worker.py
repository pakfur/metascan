#!/usr/bin/env python3
"""
Upscale Worker Process

This script runs as a separate process to perform upscaling operations.
It communicates with the main GUI process via JSON files.

Usage:
    python upscale_worker.py <task_id> <queue_dir>
"""

import sys
import json
import time
import signal
import logging
import traceback
from pathlib import Path
from typing import Optional

# Add the parent directory to sys.path so we can import metascan modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from metascan.core.media_upscaler import MediaUpscaler
from metascan.core.upscale_queue_process import UpscaleStatus
from metascan.utils.app_paths import get_data_dir


class UpscaleWorker:
    """Worker process for performing upscaling tasks."""

    def __init__(self, task_id: str, queue_dir: Path):
        """
        Initialize the worker.

        Args:
            task_id: ID of the task to process
            queue_dir: Directory containing queue files
        """
        self.task_id = task_id
        self.queue_dir = Path(queue_dir)
        self.queue_file = self.queue_dir / "queue.json"
        self.progress_file = self.queue_dir / f"progress_{task_id}.json"
        self.cancel_file = self.queue_dir / f"cancel_{task_id}.signal"

        self.cancelled = False

        # Set up logging
        self.logger = logging.getLogger(f"upscale_worker_{task_id}")
        self.logger.setLevel(logging.INFO)

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully")
        self.cancelled = True

    def _check_cancelled(self) -> bool:
        """Check if the task has been cancelled."""
        if self.cancelled:
            return True

        # Check for cancel signal file
        if self.cancel_file.exists():
            self.logger.info("Cancel signal file detected")
            self.cancelled = True
            return True

        return False

    def _update_progress(
        self, progress: float, status: Optional[UpscaleStatus] = None
    ) -> bool:
        """
        Update task progress.

        Args:
            progress: Progress percentage (0-100)
            status: Optional status update

        Returns:
            False if task was cancelled, True otherwise
        """
        if self._check_cancelled():
            return False

        try:
            progress_data = {
                "task_id": self.task_id,
                "progress": progress,
                "timestamp": time.time(),
                "status": status.value if status else None,
            }

            # Write progress atomically
            temp_file = self.progress_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(progress_data, f, indent=2)
            temp_file.rename(self.progress_file)

            self.logger.info(f"Progress updated: {progress}%")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update progress: {e}")
            return True  # Continue processing even if progress update fails

    def _load_task(self):
        """Load task information from queue file."""
        try:
            with open(self.queue_file, "r") as f:
                queue_data = json.load(f)

            if self.task_id not in queue_data.get("tasks", {}):
                raise ValueError(f"Task {self.task_id} not found in queue")

            task_data = queue_data["tasks"][self.task_id]
            return task_data

        except Exception as e:
            self.logger.error(f"Failed to load task: {e}")
            raise

    def _update_task_status(
        self,
        status: UpscaleStatus,
        error_message: Optional[str] = None,
        output_path: Optional[str] = None,
        progress: Optional[float] = None,
    ):
        """Update task status in the queue file."""
        try:
            # Read current queue
            with open(self.queue_file, "r") as f:
                queue_data = json.load(f)

            # Update task
            if self.task_id in queue_data.get("tasks", {}):
                task = queue_data["tasks"][self.task_id]
                task["status"] = status.value
                if error_message:
                    task["error_message"] = error_message
                if output_path:
                    task["output_path"] = output_path
                if progress is not None:
                    task["progress"] = progress
                task["last_updated"] = time.time()

            # Write back atomically
            temp_file = self.queue_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(queue_data, f, indent=2)
            temp_file.rename(self.queue_file)

            self.logger.info(f"Task status updated to {status.value}")

        except Exception as e:
            self.logger.error(f"Failed to update task status: {e}")

    def run(self):
        """Run the upscaling task."""
        try:
            # Load task data
            task_data = self._load_task()

            self.logger.info(f"Starting upscale task: {task_data['file_path']}")

            # Update status to processing
            self._update_task_status(UpscaleStatus.PROCESSING)
            self._update_progress(0, UpscaleStatus.PROCESSING)

            if self._check_cancelled():
                self._update_task_status(UpscaleStatus.CANCELLED)
                return 1

            # Initialize upscaler with same config as main window
            models_dir = get_data_dir() / "models"
            upscaler = MediaUpscaler(
                models_dir=models_dir, device="auto", tile_size=512, debug=False
            )

            # Perform upscaling with progress callback
            def progress_callback(progress: float) -> bool:
                return self._update_progress(progress)

            input_path = Path(task_data["file_path"])

            # Generate output path if not provided
            output_path = None
            if task_data.get("output_path"):
                output_path = Path(task_data["output_path"])
            else:
                # Generate output path based on input path and scale
                stem = input_path.stem
                suffix = input_path.suffix
                scale = task_data["scale"]
                output_path = input_path.parent / f"{stem}_upscaled_{scale}x{suffix}"

            # Call appropriate method based on file type
            if task_data["file_type"] == "video":
                success = upscaler.process_video(
                    input_path=input_path,
                    output_path=output_path,
                    scale=task_data["scale"],
                    fps=task_data.get("fps_override"),
                    enhance_faces=task_data.get("face_enhance", False),
                    model_type=task_data.get("model", "general"),
                    preserve_metadata=task_data.get("preserve_metadata", True),
                    progress_callback=progress_callback,
                )
            else:  # image
                success = upscaler.process_image(
                    input_path=input_path,
                    output_path=output_path,
                    scale=task_data["scale"],
                    enhance_faces=task_data.get("face_enhance", False),
                    model_type=task_data.get("model", "general"),
                    preserve_metadata=task_data.get("preserve_metadata", True),
                    progress_callback=progress_callback,
                )

            if self._check_cancelled():
                self._update_task_status(UpscaleStatus.CANCELLED)
                return 1

            if success:
                self._update_progress(100, UpscaleStatus.COMPLETED)
                self._update_task_status(
                    UpscaleStatus.COMPLETED, output_path=str(output_path), progress=100
                )
                self.logger.info("Task completed successfully")
                return 0
            else:
                self._update_task_status(
                    UpscaleStatus.FAILED, error_message="Upscaling failed"
                )
                self.logger.error("Task failed")
                return 1

        except Exception as e:
            error_msg = f"Task failed with exception: {str(e)}"
            self.logger.error(f"{error_msg}\n{traceback.format_exc()}")
            self._update_task_status(UpscaleStatus.FAILED, error_message=error_msg)
            return 1

        finally:
            # Clean up progress file
            try:
                if self.progress_file.exists():
                    self.progress_file.unlink()
            except Exception:
                pass


def main():
    """Main entry point."""
    if len(sys.argv) != 3:
        print("Usage: python upscale_worker.py <task_id> <queue_dir>", file=sys.stderr)
        sys.exit(1)

    task_id = sys.argv[1]
    queue_dir = Path(sys.argv[2])

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s - upscale_worker_{task_id} - %(levelname)s - %(message)s",
    )

    worker = UpscaleWorker(task_id, queue_dir)
    exit_code = worker.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
