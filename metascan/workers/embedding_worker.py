#!/usr/bin/env python3
"""
Embedding Worker Process

Runs as a separate process to compute pHash and CLIP embeddings for media files.
Communicates with the main GUI process via JSON files.

Usage:
    python embedding_worker.py <queue_dir>
"""

import json
import logging
import logging.handlers
import os
import platform
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, IO, Optional

# Pre-load PyTorch c10.dll on Windows to prevent DLL loading errors
if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec

    try:
        if (
            (spec := find_spec("torch"))
            and spec.origin
            and os.path.exists(
                dll_path := os.path.join(
                    os.path.dirname(spec.origin), "lib", "c10.dll"
                )
            )
        ):
            ctypes.CDLL(os.path.normpath(dll_path))
    except Exception:
        pass

# Add the parent directory to sys.path so we can import metascan modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import portalocker

from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager
from metascan.core.database_sqlite import DatabaseManager
from metascan.utils.app_paths import get_data_dir


class EmbeddingWorker:
    """Worker process for computing embeddings."""

    VIDEO_EXTENSIONS = {".mp4", ".webm"}

    def __init__(self, queue_dir: Path):
        self.queue_dir = Path(queue_dir)
        self.task_file = self.queue_dir / "embedding_task.json"
        self.progress_file = self.queue_dir / "progress_embedding.json"
        self.cancel_file = self.queue_dir / "cancel_embedding.signal"
        self.lock_file = self.queue_dir / "embedding.lock"

        self.cancelled = False

        self.logger = logging.getLogger("embedding_worker")
        self.logger.setLevel(logging.INFO)

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        self.logger.info(f"Received signal {signum}, shutting down gracefully")
        self.cancelled = True

    def _check_cancelled(self) -> bool:
        if self.cancelled:
            return True
        if self.cancel_file.exists():
            self.cancelled = True
            return True
        return False

    def _write_progress(
        self,
        current: int,
        total: int,
        status: str,
        current_file: str = "",
        error: str = "",
    ) -> None:
        """Write progress to a JSON file for the main process to read."""
        progress_data = {
            "current": current,
            "total": total,
            "status": status,
            "current_file": current_file,
            "error": error,
            "timestamp": time.time(),
        }
        temp_file = self.progress_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(progress_data, f)
            os.replace(str(temp_file), str(self.progress_file))
        except Exception as e:
            self.logger.error(f"Failed to write progress: {e}")

    def run(self) -> None:
        """Main worker loop."""
        try:
            # Read task configuration
            if not self.task_file.exists():
                self.logger.error("No task file found")
                self._write_progress(0, 0, "error", error="No task file found")
                return

            with open(self.task_file, "r") as f:
                task = json.load(f)

            model_key = task.get("model_key", "small")
            device = task.get("device", "auto")
            file_paths = task.get("file_paths", [])
            db_path = task.get("db_path", "")
            index_dir = task.get("index_dir", "")
            num_keyframes = task.get("video_keyframes", 4)
            compute_phash = task.get("compute_phash", True)

            if not file_paths:
                self._write_progress(0, 0, "complete")
                return

            total = len(file_paths)
            self._write_progress(0, total, "loading_model")
            self.logger.info(
                f"Starting embedding computation: {total} files, "
                f"model={model_key}, device={device}"
            )

            # Initialize components
            embedding_mgr = EmbeddingManager(model_key=model_key, device=device)
            faiss_mgr = FaissIndexManager(Path(index_dir))
            db_mgr = DatabaseManager(Path(db_path))

            # Load or create FAISS index
            if not faiss_mgr.load() or not faiss_mgr.check_model_match(model_key):
                faiss_mgr.create(
                    embedding_dim=embedding_mgr.embedding_dim,
                    model_key=model_key,
                )

            self._write_progress(0, total, "processing")

            processed = 0
            batch_paths = []
            hash_batch = []

            for i, file_path in enumerate(file_paths):
                if self._check_cancelled():
                    self.logger.info("Cancelled by user")
                    self._write_progress(processed, total, "cancelled")
                    break

                try:
                    self._write_progress(
                        i, total, "processing", current_file=Path(file_path).name
                    )

                    ext = Path(file_path).suffix.lower()
                    is_video = ext in self.VIDEO_EXTENSIONS

                    # Compute pHash
                    if compute_phash:
                        if is_video:
                            phash = EmbeddingManager.compute_video_phash(file_path)
                        else:
                            phash = EmbeddingManager.compute_phash(file_path)
                        if phash:
                            hash_batch.append((Path(file_path), phash))

                    # Compute CLIP embedding
                    if is_video:
                        embedding = embedding_mgr.compute_video_embedding(
                            file_path, num_keyframes=num_keyframes
                        )
                    else:
                        embedding = embedding_mgr.compute_image_embedding(file_path)

                    if embedding is not None:
                        faiss_mgr.add(file_path, embedding)
                        batch_paths.append(file_path)

                    processed += 1

                    # Periodic saves every 100 files
                    if processed % 100 == 0:
                        faiss_mgr.save()
                        if hash_batch:
                            db_mgr.save_media_hash_batch(hash_batch)
                            hash_batch = []
                        if batch_paths:
                            db_mgr.mark_embedded(batch_paths, model_key)
                            batch_paths = []
                        self.logger.info(f"Progress: {processed}/{total}")

                except Exception as e:
                    self.logger.error(f"Failed to process {file_path}: {e}")
                    traceback.print_exc()
                    continue

            # Final save
            faiss_mgr.save()
            if hash_batch:
                db_mgr.save_media_hash_batch(hash_batch)
            if batch_paths:
                db_mgr.mark_embedded(batch_paths, model_key)

            # Unload model to free GPU memory
            embedding_mgr.unload_model()

            if not self.cancelled:
                self._write_progress(processed, total, "complete")
                self.logger.info(
                    f"Embedding computation complete: {processed}/{total} files"
                )

        except Exception as e:
            self.logger.error(f"Worker failed: {e}")
            traceback.print_exc()
            self._write_progress(0, 0, "error", error=str(e))


def setup_logging(queue_dir: Path) -> None:
    """Set up logging for the worker process."""
    log_dir = get_data_dir().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "embedding_worker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.addHandler(logging.StreamHandler(sys.stdout))
    root_logger.setLevel(logging.INFO)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python embedding_worker.py <queue_dir>")
        sys.exit(1)

    queue_dir = Path(sys.argv[1])
    setup_logging(queue_dir)

    worker = EmbeddingWorker(queue_dir)
    worker.run()
