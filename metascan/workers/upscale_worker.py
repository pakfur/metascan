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
import os

# Add the parent directory to sys.path so we can import metascan modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from metascan.core.media_upscaler import MediaUpscaler
from metascan.core.upscale_queue_process import UpscaleStatus
from metascan.utils.app_paths import get_models_dir


class UpscaleWorker:
    """Worker process for performing upscaling tasks."""

    def __init__(self, task_id: str, queue_dir: Path, debug: bool = False):
        """
        Initialize the worker.

        Args:
            task_id: ID of the task to process
            queue_dir: Directory containing queue files
            debug: Enable debug logging
        """
        self.task_id = task_id
        self.queue_dir = Path(queue_dir)
        self.queue_file = self.queue_dir / "queue.json"
        self.progress_file = self.queue_dir / f"progress_{task_id}.json"
        self.cancel_file = self.queue_dir / f"cancel_{task_id}.signal"
        self.debug = debug

        self.cancelled = False

        # Set up logging
        self.logger = logging.getLogger(f"upscale_worker_{task_id}")
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully")
        self.logger.debug(f"Signal frame: {frame}")
        self.cancelled = True

    def _check_cancelled(self) -> bool:
        """Check if the task has been cancelled."""
        if self.cancelled:
            self.logger.debug("Task already marked as cancelled")
            return True

        # Check for cancel signal file
        if self.cancel_file.exists():
            self.logger.info("Cancel signal file detected")
            self.logger.debug(f"Cancel file path: {self.cancel_file}")
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

            self.logger.debug(f"Progress updated: {progress}% (status: {status})")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update progress: {e}")
            if self.debug:
                self.logger.debug(
                    f"Progress update traceback: {traceback.format_exc()}"
                )
            return True  # Continue processing even if progress update fails

    def _load_task(self):
        """Load task information from queue file."""
        try:
            self.logger.debug(f"Loading task from queue file: {self.queue_file}")
            with open(self.queue_file, "r") as f:
                queue_data = json.load(f)

            if self.debug:
                self.logger.debug(f"Queue data keys: {list(queue_data.keys())}")
                self.logger.debug(
                    f"Available tasks: {list(queue_data.get('tasks', {}).keys())}"
                )

            if self.task_id not in queue_data.get("tasks", {}):
                raise ValueError(f"Task {self.task_id} not found in queue")

            task_data = queue_data["tasks"][self.task_id]
            self.logger.debug(f"Loaded task data: {json.dumps(task_data, indent=2)}")
            return task_data

        except Exception as e:
            self.logger.error(f"Failed to load task: {e}")
            if self.debug:
                self.logger.debug(f"Task load traceback: {traceback.format_exc()}")
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
            if self.debug:
                self.logger.debug(
                    f"Task update details - Error: {error_message}, Output: {output_path}, Progress: {progress}"
                )

        except Exception as e:
            self.logger.error(f"Failed to update task status: {e}")
            if self.debug:
                self.logger.debug(f"Status update traceback: {traceback.format_exc()}")

    def run(self):
        """Run the upscaling task."""
        try:
            # Load task data
            task_data = self._load_task()

            self.logger.info(f"Starting upscale task: {task_data['file_path']}")
            if self.debug:
                self.logger.debug(f"Task parameters:")
                self.logger.debug(f"  - File type: {task_data.get('file_type')}")
                self.logger.debug(f"  - Scale: {task_data.get('scale')}")
                self.logger.debug(f"  - Model: {task_data.get('model')}")
                self.logger.debug(f"  - Face enhance: {task_data.get('face_enhance')}")
                self.logger.debug(
                    f"  - Replace original: {task_data.get('replace_original')}"
                )
                self.logger.debug(
                    f"  - Preserve metadata: {task_data.get('preserve_metadata')}"
                )

            # Update status to processing
            self._update_task_status(UpscaleStatus.PROCESSING)
            self._update_progress(0, UpscaleStatus.PROCESSING)

            if self._check_cancelled():
                self._update_task_status(UpscaleStatus.CANCELLED)
                return 1

            # Initialize upscaler with same config as main window
            models_dir = get_models_dir()
            self.logger.debug(f"Models directory: {models_dir}")
            self.logger.debug(f"Initializing MediaUpscaler with debug={self.debug}")

            upscaler = MediaUpscaler(
                models_dir=models_dir, device="auto", tile_size=512, debug=self.debug
            )

            # Check if models are available, download if needed
            if not upscaler.models_available:
                self.logger.info("Models not found, downloading...")
                self._update_task_status(UpscaleStatus.DOWNLOADING_MODELS)
                self._update_progress(0)

                def download_progress_callback(message: str, progress: float) -> None:
                    self.logger.info(f"Model download: {message} - {progress:.0f}%")
                    if self.debug:
                        self.logger.debug(
                            f"Download callback - Message: {message}, Progress: {progress}"
                        )
                    self._update_progress(progress)

                success = upscaler.setup_models(download_progress_callback)
                if not success:
                    self.logger.error("Failed to download models")
                    self._update_task_status(UpscaleStatus.FAILED)
                    return 1

                # Re-check models after download
                upscaler._check_models()
                if not upscaler.models_available:
                    self.logger.error("Models still not available after download")
                    self._update_task_status(UpscaleStatus.FAILED)
                    return 1

            # Perform upscaling with progress callback
            def progress_callback(progress: float) -> bool:
                if self.debug:
                    self.logger.debug(f"Upscaling progress: {progress:.1f}%")
                self._update_progress(progress)
                # Check if cancelled and return False to stop processing
                if self._check_cancelled():
                    self.logger.info("Upscaling cancelled by user")
                    return False
                return True  # Continue processing

            input_path = Path(task_data["file_path"])
            self.logger.debug(f"Input path: {input_path}")
            self.logger.debug(f"Input file exists: {input_path.exists()}")
            if input_path.exists():
                self.logger.debug(f"Input file size: {input_path.stat().st_size} bytes")

            # Generate output path if not provided
            output_path = None
            if task_data.get("output_path"):
                output_path = Path(task_data["output_path"])
                self.logger.debug(f"Using provided output path: {output_path}")
            else:
                # Generate output path based on input path and scale
                stem = input_path.stem
                suffix = input_path.suffix
                scale = task_data["scale"]
                output_path = input_path.parent / f"{stem}_upscaled_{scale}x{suffix}"
                self.logger.debug(f"Generated output path: {output_path}")

            # Call appropriate method based on file type
            if task_data["file_type"] == "video":
                self.logger.info(
                    f"Processing video with scale={task_data['scale']}, model={task_data.get('model', 'general')}"
                )
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
                self.logger.info(
                    f"Processing image with scale={task_data['scale']}, model={task_data.get('model', 'general')}"
                )
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

                # When replacing original, the final file is at the input path location
                # The MediaUpscaler moves the upscaled file to replace the original
                if task_data.get("replace_original", True):
                    final_output_path = str(input_path)
                else:
                    final_output_path = str(output_path)

                self.logger.debug(
                    f"Upscaling successful, final output: {final_output_path}"
                )
                if Path(final_output_path).exists():
                    self.logger.debug(
                        f"Output file size: {Path(final_output_path).stat().st_size} bytes"
                    )

                self._update_task_status(
                    UpscaleStatus.COMPLETED, output_path=final_output_path, progress=100
                )
                self.logger.info(
                    f"Task completed successfully, output at: {final_output_path}"
                )
                return 0
            else:
                self._update_task_status(
                    UpscaleStatus.FAILED, error_message="Upscaling failed"
                )
                self.logger.error("Task failed")
                return 1

        except Exception as e:
            error_msg = f"Task failed with exception: {str(e)}"
            self.logger.error(f"{error_msg}")
            if self.debug:
                self.logger.debug(f"Full traceback:\n{traceback.format_exc()}")
            else:
                self.logger.error(f"Traceback:\n{traceback.format_exc()}")
            self._update_task_status(UpscaleStatus.FAILED, error_message=error_msg)
            return 1

        finally:
            # Clean up progress file
            try:
                if self.progress_file.exists():
                    self.logger.debug(
                        f"Cleaning up progress file: {self.progress_file}"
                    )
                    self.progress_file.unlink()
            except Exception as e:
                self.logger.debug(f"Failed to clean up progress file: {e}")


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print(
            "Usage: python upscale_worker.py <task_id> <queue_dir> [--debug]",
            file=sys.stderr,
        )
        sys.exit(1)

    task_id = sys.argv[1]
    queue_dir = Path(sys.argv[2])
    debug = "--debug" in sys.argv or os.environ.get("UPSCALE_DEBUG", "").lower() in (
        "1",
        "true",
        "yes",
    )

    # Set up logging
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format=f"%(asctime)s - upscale_worker_{task_id} - %(levelname)s - %(message)s",
    )

    # Log startup info
    logger = logging.getLogger(f"upscale_worker_{task_id}")
    logger.info(f"Starting upscale worker for task {task_id}")
    logger.info(f"Queue directory: {queue_dir}")
    logger.info(f"Debug mode: {debug}")
    if debug:
        logger.debug(f"Python version: {sys.version}")
        logger.debug(f"Script path: {Path(__file__).absolute()}")
        logger.debug(f"Working directory: {Path.cwd()}")

    worker = UpscaleWorker(task_id, queue_dir, debug=debug)
    exit_code = worker.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
