#!/usr/bin/env python3
"""
Test script for upscaler process

This script allows testing the upscaler worker process directly by:
1. Reading a task from queue.json by ID
2. Running the upscaler with debug logging
3. Monitoring progress and results

Usage:
    python test_upscaler.py <task_id> [--create-test-task <image_or_video_path>]
    python test_upscaler.py --list
"""

import sys
import json
import time
import logging
import argparse
import subprocess
import uuid
import shutil
import tempfile
import os
from pathlib import Path
from typing import Dict, Optional, Any, Tuple
from PIL import Image
import cv2
from glob import glob


class UpscalerTester:
    """Test harness for the upscaler process."""

    def __init__(self, debug: bool = True, validate: bool = True):
        """Initialize the tester."""
        self.debug = debug
        self.validate = validate

        # Set up logging
        log_level = logging.DEBUG if debug else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('upscaler_tester')

        # Queue paths
        self.queue_dir = Path("/Users/jk/gws/metascan/data/queue")
        self.queue_file = self.queue_dir / "queue.json"

        # Alternative queue file location (local data directory)
        self.alt_queue_file = Path("data") / "upscale_queue.json"

        self.logger.info(f"Primary queue file: {self.queue_file}")
        self.logger.info(f"Alternative queue file: {self.alt_queue_file}")

    def find_queue_file(self) -> Path:
        """Find the queue file to use."""
        if self.queue_file.exists():
            self.logger.info(f"Using primary queue file: {self.queue_file}")
            return self.queue_file
        elif self.alt_queue_file.exists():
            self.logger.info(f"Using alternative queue file: {self.alt_queue_file}")
            # Note: The alternative queue file has a different format, so we'll use it directly
            return self.alt_queue_file
        else:
            # Create default queue structure
            self.logger.info("No queue file found, creating new one")
            self.queue_dir.mkdir(parents=True, exist_ok=True)
            self._create_empty_queue()
            return self.queue_file

    def _create_empty_queue(self):
        """Create an empty queue file."""
        queue_data = {
            "tasks": {},
            "created_at": time.time(),
            "last_updated": time.time()
        }
        with open(self.queue_file, 'w') as f:
            json.dump(queue_data, f, indent=2)
        self.logger.info(f"Created empty queue file at {self.queue_file}")

    def list_tasks(self) -> Dict[str, Any]:
        """List all tasks in the queue."""
        queue_file = self.find_queue_file()

        try:
            with open(queue_file, 'r') as f:
                queue_data = json.load(f)

            tasks = queue_data.get("tasks", {})

            if not tasks:
                self.logger.info("No tasks found in queue")
                return {}

            self.logger.info(f"Found {len(tasks)} task(s) in queue:")
            for task_id, task_data in tasks.items():
                status = task_data.get("status", "unknown")
                file_path = task_data.get("file_path", "unknown")
                progress = task_data.get("progress", 0)
                self.logger.info(f"  - {task_id}: {file_path}")
                self.logger.info(f"    Status: {status}, Progress: {progress}%")

                if self.debug:
                    self.logger.debug(f"    Full task data: {json.dumps(task_data, indent=4)}")

            return tasks

        except Exception as e:
            self.logger.error(f"Failed to read queue file: {e}")
            return {}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific task by ID."""
        queue_file = self.find_queue_file()

        try:
            with open(queue_file, 'r') as f:
                queue_data = json.load(f)

            tasks = queue_data.get("tasks", {})

            if task_id not in tasks:
                self.logger.error(f"Task {task_id} not found in queue")
                return None

            task_data = tasks[task_id]
            self.logger.info(f"Found task {task_id}:")
            self.logger.info(f"  File: {task_data.get('file_path')}")
            self.logger.info(f"  Status: {task_data.get('status')}")
            self.logger.info(f"  Progress: {task_data.get('progress')}%")

            if self.debug:
                self.logger.debug(f"Full task data:\n{json.dumps(task_data, indent=2)}")

            return task_data

        except Exception as e:
            self.logger.error(f"Failed to get task: {e}")
            return None

    def create_test_task(self, file_path: str, **kwargs) -> str:
        """Create a test task for the given file."""
        queue_file = self.find_queue_file()

        # Determine file type
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            self.logger.error(f"File does not exist: {file_path}")
            return ""

        # Detect file type by extension
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

        suffix = file_path_obj.suffix.lower()
        if suffix in video_extensions:
            file_type = "video"
        elif suffix in image_extensions:
            file_type = "image"
        else:
            self.logger.warning(f"Unknown file type for {suffix}, assuming image")
            file_type = "image"

        # Generate task ID
        task_id = f"test_{uuid.uuid4().hex[:8]}"

        # Create task data
        task_data = {
            "id": task_id,
            "file_path": str(file_path_obj.absolute()),
            "output_path": None,
            "file_type": file_type,
            "scale": kwargs.get("scale", 2),
            "face_enhance": kwargs.get("face_enhance", False),
            "model": kwargs.get("model", "general"),
            "preserve_metadata": kwargs.get("preserve_metadata", True),
            "status": "pending",
            "progress": 0.0,
            "error_message": None,
            "created_at": time.time(),
            "last_updated": time.time(),
            "process_id": None,
            "replace_original": kwargs.get("replace_original", False),
            "interpolate_frames": kwargs.get("interpolate_frames", False),
            "interpolation_factor": kwargs.get("interpolation_factor", 2),
            "fps_override": kwargs.get("fps_override", None)
        }

        # Add to queue
        try:
            with open(queue_file, 'r') as f:
                queue_data = json.load(f)

            if "tasks" not in queue_data:
                queue_data["tasks"] = {}

            queue_data["tasks"][task_id] = task_data
            queue_data["last_updated"] = time.time()

            # Write back
            with open(queue_file, 'w') as f:
                json.dump(queue_data, f, indent=2)

            self.logger.info(f"Created test task {task_id} for {file_path}")
            self.logger.info(f"  Type: {file_type}")
            self.logger.info(f"  Scale: {task_data['scale']}x")
            self.logger.info(f"  Model: {task_data['model']}")

            return task_id

        except Exception as e:
            self.logger.error(f"Failed to create test task: {e}")
            return ""

    def get_image_info(self, file_path: Path) -> Dict[str, Any]:
        """Get detailed information about an image/video file."""
        info = {}

        if not file_path.exists():
            return {"exists": False}

        info["exists"] = True
        info["size_bytes"] = file_path.stat().st_size
        info["size_mb"] = info["size_bytes"] / (1024 * 1024)

        # Detect if it's a video or image
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}

        if file_path.suffix.lower() in video_extensions:
            # Video file - use cv2
            try:
                cap = cv2.VideoCapture(str(file_path))
                info["type"] = "video"
                info["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                info["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                info["fps"] = cap.get(cv2.CAP_PROP_FPS)
                info["frame_count"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.release()
            except Exception as e:
                self.logger.error(f"Failed to read video info: {e}")
        else:
            # Image file - use PIL
            try:
                with Image.open(file_path) as img:
                    info["type"] = "image"
                    info["width"], info["height"] = img.size
                    info["format"] = img.format
                    info["mode"] = img.mode
            except Exception as e:
                self.logger.error(f"Failed to read image info: {e}")

        return info

    def get_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Extract metadata from file using exiftool."""
        try:
            result = subprocess.run(
                ['exiftool', '-j', str(file_path)],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                metadata_list = json.loads(result.stdout)
                if metadata_list:
                    return metadata_list[0]
            else:
                self.logger.warning(f"exiftool failed: {result.stderr}")
        except FileNotFoundError:
            self.logger.warning("exiftool not found, skipping metadata extraction")
        except Exception as e:
            self.logger.error(f"Failed to extract metadata: {e}")

        return {}

    def compare_metadata(self, original_meta: Dict, upscaled_meta: Dict) -> Dict[str, Any]:
        """Compare metadata between original and upscaled files."""
        # Keys to ignore in comparison (these are expected to change)
        ignore_keys = {
            'FileName', 'Directory', 'FileSize', 'FileModifyDate',
            'FileAccessDate', 'FileInodeChangeDate', 'FilePermissions',
            'ImageWidth', 'ImageHeight', 'ImageSize', 'Megapixels',
            'ExifImageWidth', 'ExifImageHeight', 'SourceFile',
            'ExifToolVersion', 'FileTypeExtension', 'FileType',
            'ThumbnailImage', 'ThumbnailOffset', 'ThumbnailLength'
        }

        # Keys that should be preserved
        important_keys = {
            'Make', 'Model', 'DateTimeOriginal', 'CreateDate',
            'ModifyDate', 'GPSLatitude', 'GPSLongitude', 'GPSAltitude',
            'LensModel', 'FocalLength', 'ISO', 'ExposureTime',
            'FNumber', 'Artist', 'Copyright', 'Description'
        }

        comparison = {
            "total_original": len(original_meta),
            "total_upscaled": len(upscaled_meta),
            "preserved": [],
            "lost": [],
            "modified": [],
            "added": []
        }

        # Check preserved and modified keys
        for key, value in original_meta.items():
            if key in ignore_keys:
                continue

            if key in upscaled_meta:
                if str(value) == str(upscaled_meta[key]):
                    comparison["preserved"].append(key)
                else:
                    comparison["modified"].append({
                        "key": key,
                        "original": value,
                        "upscaled": upscaled_meta[key]
                    })
            else:
                comparison["lost"].append(key)

        # Check added keys
        for key in upscaled_meta:
            if key not in original_meta and key not in ignore_keys:
                comparison["added"].append(key)

        # Calculate preservation rate for important metadata
        important_preserved = 0
        important_total = 0
        for key in important_keys:
            if key in original_meta:
                important_total += 1
                if key in upscaled_meta and str(original_meta[key]) == str(upscaled_meta[key]):
                    important_preserved += 1

        if important_total > 0:
            comparison["important_preservation_rate"] = (important_preserved / important_total) * 100
        else:
            comparison["important_preservation_rate"] = 100.0

        return comparison

    def check_trash_for_file(self, original_file_path: Path) -> Dict[str, Any]:
        """Check if the original file was moved to Trash."""
        trash_info = {
            "found": False,
            "trash_path": None,
            "locations_checked": []
        }

        # Common trash locations on macOS
        home = Path.home()
        trash_locations = [
            home / ".Trash",  # User trash
        ]

        # Add the specific volume's trash if file is on external volume
        if str(original_file_path).startswith("/Volumes/"):
            volume_parts = str(original_file_path).split("/")
            if len(volume_parts) > 2:
                volume_name = volume_parts[2]
                # External volumes use .Trashes/uid
                volume_trash = Path(f"/Volumes/{volume_name}/.Trashes/{os.getuid()}")
                trash_locations.append(volume_trash)
                # Also check root .Trashes
                volume_trash_root = Path(f"/Volumes/{volume_name}/.Trashes")
                if volume_trash_root.exists():
                    try:
                        for uid_dir in volume_trash_root.iterdir():
                            if uid_dir.is_dir():
                                trash_locations.append(uid_dir)
                    except PermissionError:
                        pass

        original_name = original_file_path.name
        self.logger.debug(f"Checking trash for file: {original_name}")
        self.logger.debug(f"Trash locations to check: {trash_locations}")

        for trash_location in trash_locations:
            if trash_location.exists():
                trash_info["locations_checked"].append(str(trash_location))
                # Look for the file in trash
                try:
                    for item in trash_location.iterdir():
                        if original_name in item.name:
                            trash_info["found"] = True
                            trash_info["trash_path"] = str(item)
                            self.logger.debug(f"Found in trash: {item}")
                            return trash_info
                except PermissionError:
                    self.logger.debug(f"Permission denied accessing: {trash_location}")

        return trash_info

    def validate_upscaling(self, task_data: Dict, original_path: Path, upscaled_path: Path) -> Dict[str, Any]:
        """Validate that upscaling was successful."""
        validation = {
            "success": True,
            "checks": {},
            "warnings": [],
            "errors": []
        }

        # Check file existence
        if not original_path.exists():
            validation["errors"].append(f"Original file not found: {original_path}")
            validation["success"] = False
            return validation

        if not upscaled_path.exists():
            validation["errors"].append(f"Upscaled file not found: {upscaled_path}")
            validation["success"] = False
            return validation

        # Get file information
        original_info = self.get_image_info(original_path)
        upscaled_info = self.get_image_info(upscaled_path)

        validation["checks"]["original_info"] = original_info
        validation["checks"]["upscaled_info"] = upscaled_info

        # Validate resolution
        expected_scale = task_data.get("scale", 2)
        if "width" in original_info and "width" in upscaled_info:
            actual_scale_w = upscaled_info["width"] / original_info["width"]
            actual_scale_h = upscaled_info["height"] / original_info["height"]

            validation["checks"]["resolution"] = {
                "original": f"{original_info['width']}x{original_info['height']}",
                "upscaled": f"{upscaled_info['width']}x{upscaled_info['height']}",
                "expected_scale": expected_scale,
                "actual_scale_width": round(actual_scale_w, 2),
                "actual_scale_height": round(actual_scale_h, 2)
            }

            # Check if scale is correct (with small tolerance for rounding)
            if abs(actual_scale_w - expected_scale) > 0.01 or abs(actual_scale_h - expected_scale) > 0.01:
                validation["warnings"].append(
                    f"Scale mismatch: expected {expected_scale}x, got {actual_scale_w:.2f}x{actual_scale_h:.2f}"
                )

        # Validate file size (upscaled should be larger for images)
        if original_info.get("type") == "image":
            if upscaled_info["size_bytes"] <= original_info["size_bytes"]:
                validation["warnings"].append(
                    f"Upscaled file is not larger than original "
                    f"({upscaled_info['size_mb']:.2f}MB vs {original_info['size_mb']:.2f}MB)"
                )

        validation["checks"]["file_sizes"] = {
            "original_mb": round(original_info["size_mb"], 2),
            "upscaled_mb": round(upscaled_info["size_mb"], 2),
            "size_increase_factor": round(upscaled_info["size_bytes"] / original_info["size_bytes"], 2)
        }

        # Validate metadata preservation if requested
        if task_data.get("preserve_metadata", True):
            self.logger.info("Checking metadata preservation...")
            original_meta = self.get_metadata(original_path)
            upscaled_meta = self.get_metadata(upscaled_path)

            if original_meta and upscaled_meta:
                meta_comparison = self.compare_metadata(original_meta, upscaled_meta)
                validation["checks"]["metadata"] = meta_comparison

                # Check if important metadata was preserved
                if meta_comparison["important_preservation_rate"] < 90:
                    validation["warnings"].append(
                        f"Low metadata preservation rate: {meta_comparison['important_preservation_rate']:.1f}%"
                    )

                if meta_comparison["lost"]:
                    self.logger.debug(f"Lost metadata keys: {meta_comparison['lost']}")
            else:
                validation["warnings"].append("Could not verify metadata preservation")

        # Check if original was moved to trash (if replace_original is True)
        if task_data.get("replace_original", False):
            self.logger.info("Checking if original file was moved to Trash...")

            # The original file path in the task data
            original_task_path = Path(task_data["file_path"])

            # Check if there's a backup file that should be in trash
            trash_check = self.check_trash_for_file(original_task_path)
            validation["checks"]["trash"] = trash_check

            if trash_check["found"]:
                self.logger.info(f"✓ Original file found in Trash: {trash_check['trash_path']}")
            else:
                # Check for backup files that might indicate the original was preserved
                possible_backups = [
                    original_task_path.parent / f"{original_task_path.stem}_upscaled_{task_data.get('scale', 2)}x.original_metadata{original_task_path.suffix}",
                    original_task_path.parent / f"{original_task_path.stem}.original{original_task_path.suffix}",
                    original_task_path.parent / f"{original_task_path.stem}_original{original_task_path.suffix}",
                    original_task_path.parent / f"{original_task_path.stem}_backup{original_task_path.suffix}"
                ]

                backup_found = None
                for backup_path in possible_backups:
                    if backup_path.exists():
                        backup_found = backup_path
                        break

                if backup_found:
                    validation["checks"]["trash"]["backup_file"] = str(backup_found)
                    self.logger.info(f"Original preserved as backup: {backup_found}")
                else:
                    validation["warnings"].append("Original file not found in Trash (may have been permanently replaced)")
                    self.logger.debug(f"Trash locations checked: {trash_check['locations_checked']}")

        return validation

    def run_upscaler(self, task_id: str) -> bool:
        """Run the upscaler for a specific task."""
        # Verify task exists
        task_data = self.get_task(task_id)
        if not task_data:
            return False

        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"Starting upscaler for task {task_id}")
        self.logger.info(f"{'='*60}\n")

        # Build command
        worker_script = Path(__file__).parent / "metascan" / "workers" / "upscale_worker.py"

        if not worker_script.exists():
            self.logger.error(f"Worker script not found at {worker_script}")
            return False

        cmd = [sys.executable, str(worker_script), task_id, str(self.queue_dir)]

        if self.debug:
            cmd.append("--debug")

        # Set environment variable for debug mode
        env = dict(os.environ)
        if self.debug:
            env["UPSCALE_DEBUG"] = "1"

        self.logger.info(f"Command: {' '.join(cmd)}")
        self.logger.info(f"Debug mode: {self.debug}")

        # Progress monitoring
        progress_file = self.queue_dir / f"progress_{task_id}.json"
        last_progress = -1

        try:
            # Start the process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )

            self.logger.info(f"Started worker process with PID {process.pid}")

            # Monitor output and progress
            while True:
                # Check if process is still running
                poll_result = process.poll()

                # Read any available output
                if process.stdout:
                    line = process.stdout.readline()
                    if line:
                        print(f"[WORKER] {line.rstrip()}")

                # Check progress file
                if progress_file.exists():
                    try:
                        with open(progress_file, 'r') as f:
                            progress_data = json.load(f)

                        current_progress = progress_data.get("progress", 0)
                        if current_progress != last_progress:
                            last_progress = current_progress
                            status = progress_data.get("status", "")
                            self.logger.info(f"Progress: {current_progress:.1f}% {f'({status})' if status else ''}")
                    except:
                        pass

                # Check if process has finished
                if poll_result is not None:
                    # Read any remaining output
                    remaining_output = process.stdout.read() if process.stdout else ""
                    if remaining_output:
                        for line in remaining_output.splitlines():
                            print(f"[WORKER] {line}")

                    break

                time.sleep(0.1)

            # Get final status
            exit_code = process.returncode

            self.logger.info(f"\n{'='*60}")

            # Get final task status
            final_task = self.get_task(task_id)

            if exit_code == 0 or (final_task and final_task.get("status") == "completed"):
                self.logger.info(f"✓ Upscaler process completed")

                if final_task:
                    # Determine paths for validation
                    original_path = Path(task_data["file_path"])

                    # Check if we're replacing original or creating new file
                    if task_data.get("replace_original", False):
                        # When replacing, the upscaled file is at the original path
                        upscaled_path = original_path
                        # We need to check if there's a backup of the original
                        original_backup = original_path.parent / f"{original_path.stem}_original{original_path.suffix}"
                        if original_backup.exists():
                            original_path = original_backup
                    else:
                        # When not replacing, output path should be specified
                        output_path = final_task.get("output_path")
                        if output_path:
                            upscaled_path = Path(output_path)
                        else:
                            # Generate expected output path
                            scale = task_data.get("scale", 2)
                            upscaled_path = original_path.parent / f"{original_path.stem}_upscaled_{scale}x{original_path.suffix}"

                    self.logger.info(f"Output file: {upscaled_path}")

                    # Run validation if enabled
                    if self.validate and upscaled_path.exists():
                        self.logger.info(f"\n{'='*60}")
                        self.logger.info("VALIDATION RESULTS")
                        self.logger.info(f"{'='*60}")

                        # Create a temporary copy of the original if it was replaced
                        temp_original = None
                        if task_data.get("replace_original", False) and original_path == upscaled_path:
                            # We need the original for comparison, but it might have been replaced
                            # Check for backup files created during upscaling
                            possible_backups = [
                                original_path.parent / f"{original_path.stem}_upscaled_{task_data.get('scale', 2)}x.original_metadata{original_path.suffix}",
                                original_path.parent / f"{original_path.stem}.original{original_path.suffix}",
                                original_path.parent / f"{original_path.stem}_backup{original_path.suffix}"
                            ]

                            for backup in possible_backups:
                                if backup.exists():
                                    original_path = backup
                                    self.logger.info(f"Using backup for comparison: {backup}")
                                    break

                        validation = self.validate_upscaling(task_data, original_path, upscaled_path)

                        # Print validation results
                        if "resolution" in validation["checks"]:
                            res = validation["checks"]["resolution"]
                            self.logger.info(f"\n📐 Resolution Check:")
                            self.logger.info(f"  Original: {res['original']}")
                            self.logger.info(f"  Upscaled: {res['upscaled']}")
                            self.logger.info(f"  Scale: {res['actual_scale_width']}x (expected {res['expected_scale']}x)")

                        if "file_sizes" in validation["checks"]:
                            sizes = validation["checks"]["file_sizes"]
                            self.logger.info(f"\n💾 File Size Check:")
                            self.logger.info(f"  Original: {sizes['original_mb']} MB")
                            self.logger.info(f"  Upscaled: {sizes['upscaled_mb']} MB")
                            self.logger.info(f"  Increase: {sizes['size_increase_factor']}x")

                        if "metadata" in validation["checks"]:
                            meta = validation["checks"]["metadata"]
                            self.logger.info(f"\n🏷️  Metadata Check:")
                            self.logger.info(f"  Original tags: {meta['total_original']}")
                            self.logger.info(f"  Upscaled tags: {meta['total_upscaled']}")
                            self.logger.info(f"  Preserved: {len(meta['preserved'])} tags")
                            self.logger.info(f"  Lost: {len(meta['lost'])} tags")
                            self.logger.info(f"  Modified: {len(meta['modified'])} tags")
                            self.logger.info(f"  Important metadata preservation: {meta.get('important_preservation_rate', 0):.1f}%")

                            if meta["lost"] and self.debug:
                                self.logger.debug(f"  Lost tags: {', '.join(meta['lost'][:10])}{'...' if len(meta['lost']) > 10 else ''}")

                        if "trash" in validation["checks"]:
                            trash = validation["checks"]["trash"]
                            self.logger.info(f"\n🗑️  Trash Check:")
                            if trash["found"]:
                                self.logger.info(f"  ✓ Original file found in Trash")
                                self.logger.info(f"  Location: {trash['trash_path']}")
                            elif trash.get("backup_file"):
                                self.logger.info(f"  ✓ Original preserved as backup")
                                self.logger.info(f"  Location: {trash['backup_file']}")
                            else:
                                self.logger.info(f"  ⚠️  Original file not found in Trash")
                                if self.debug:
                                    self.logger.debug(f"  Checked: {', '.join(trash['locations_checked'])}")

                        # Overall status
                        self.logger.info(f"\n✅ Validation Summary:")
                        if validation["success"]:
                            self.logger.info(f"  Status: PASSED")
                        else:
                            self.logger.error(f"  Status: FAILED")

                        if validation["warnings"]:
                            self.logger.warning(f"  Warnings ({len(validation['warnings'])}):")
                            for warning in validation["warnings"]:
                                self.logger.warning(f"    - {warning}")

                        if validation["errors"]:
                            self.logger.error(f"  Errors ({len(validation['errors'])}):")
                            for error in validation["errors"]:
                                self.logger.error(f"    - {error}")

                        self.logger.info(f"{'='*60}")

                        # Clean up temp files
                        if temp_original and temp_original.exists():
                            temp_original.unlink()

                    elif upscaled_path.exists():
                        size_mb = upscaled_path.stat().st_size / (1024 * 1024)
                        self.logger.info(f"Output size: {size_mb:.2f} MB")
                    else:
                        self.logger.warning(f"Output file not found: {upscaled_path}")
            else:
                self.logger.error(f"✗ Upscaler failed with exit code {exit_code}")

                # Get error message from task
                if final_task and final_task.get("error_message"):
                    self.logger.error(f"Error: {final_task['error_message']}")

            self.logger.info(f"{'='*60}\n")

            return exit_code == 0 or (final_task and final_task.get("status") == "completed")

        except Exception as e:
            self.logger.error(f"Failed to run upscaler: {e}")
            if self.debug:
                import traceback
                self.logger.debug(traceback.format_exc())
            return False

        finally:
            # Clean up progress file
            if progress_file.exists():
                try:
                    progress_file.unlink()
                except:
                    pass

    def clean_task(self, task_id: str):
        """Remove a task from the queue."""
        queue_file = self.find_queue_file()

        try:
            with open(queue_file, 'r') as f:
                queue_data = json.load(f)

            if task_id in queue_data.get("tasks", {}):
                del queue_data["tasks"][task_id]
                queue_data["last_updated"] = time.time()

                with open(queue_file, 'w') as f:
                    json.dump(queue_data, f, indent=2)

                self.logger.info(f"Removed task {task_id} from queue")
            else:
                self.logger.warning(f"Task {task_id} not found in queue")

        except Exception as e:
            self.logger.error(f"Failed to clean task: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Test upscaler process")
    parser.add_argument("task_id", nargs="?", help="Task ID to process")
    parser.add_argument("--list", action="store_true", help="List all tasks in queue")
    parser.add_argument("--create-test-task", metavar="FILE", help="Create a test task for the given file")
    parser.add_argument("--scale", type=int, default=2, help="Upscale factor (default: 2)")
    parser.add_argument("--model", default="general", help="Model type (default: general)")
    parser.add_argument("--face-enhance", action="store_true", help="Enable face enhancement")
    parser.add_argument("--replace-original", action="store_true", help="Replace original file")
    parser.add_argument("--clean", action="store_true", help="Remove task after processing")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug logging")
    parser.add_argument("--no-validate", action="store_true", help="Skip validation checks")

    args = parser.parse_args()

    # Determine debug mode (default is True for test script)
    debug = True
    if args.no_debug:
        debug = False
    elif args.debug:
        debug = True

    # Determine validation mode
    validate = not args.no_validate

    # Set environment for debug
    if debug:
        os.environ["UPSCALE_DEBUG"] = "1"

    tester = UpscalerTester(debug=debug, validate=validate)

    # Handle list command
    if args.list:
        tester.list_tasks()
        return 0

    # Handle create test task
    if args.create_test_task:
        task_id = tester.create_test_task(
            args.create_test_task,
            scale=args.scale,
            model=args.model,
            face_enhance=args.face_enhance,
            replace_original=args.replace_original
        )

        if task_id:
            print(f"\nCreated task: {task_id}")
            print(f"Run with: python {sys.argv[0]} {task_id}")

            # Optionally run immediately
            response = input("\nRun upscaler now? (y/n): ")
            if response.lower() == 'y':
                success = tester.run_upscaler(task_id)

                if args.clean:
                    tester.clean_task(task_id)

                return 0 if success else 1

        return 0

    # Process specific task
    if not args.task_id:
        print("Error: task_id is required (or use --list to see available tasks)")
        parser.print_help()
        return 1

    # Run upscaler for the task
    success = tester.run_upscaler(args.task_id)

    # Clean up if requested
    if args.clean:
        tester.clean_task(args.task_id)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())