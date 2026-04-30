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
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Pre-load PyTorch c10.dll on Windows to prevent DLL loading errors
if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec

    try:
        if (
            (spec := find_spec("torch"))
            and spec.origin
            and os.path.exists(
                dll_path := os.path.join(os.path.dirname(spec.origin), "lib", "c10.dll")
            )
        ):
            ctypes.CDLL(os.path.normpath(dll_path))
    except Exception:
        pass

# Add the parent directory to sys.path so we can import metascan modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import random  # noqa: E402

from metascan.core.embedding_manager import (
    EmbeddingManager,
    FaissIndexManager,
)  # noqa: E402
from metascan.core.database_sqlite import DatabaseManager  # noqa: E402
from metascan.core.vocabulary import (  # noqa: E402
    Vocabulary,
    build_vocabulary,
    select_tags,
)
from metascan.utils.app_paths import get_base_path, get_data_dir  # noqa: E402


# Maximum time to spend on a single file (seconds).
# If exceeded, the entire worker process exits and the queue will detect the
# crash and report the last file. The queue can then be restarted and will
# skip already-embedded files.
PER_FILE_TIMEOUT = 120

# Threshold for logging a slow-file warning
SLOW_FILE_THRESHOLD = 10.0

# CLIP-tagging parameters — user-chosen during design.
TAG_TOP_K = 20
TAG_THRESHOLD = 0.22
# Number of randomly-sampled images whose top-K tags are dumped to
# clip_tag_samples.json for visual validation. Deterministic across a run
# because we seed the RNG from the file list.
TAG_SAMPLE_COUNT = 3


def _write_progress_fatal(queue_dir: Path, error: str) -> None:
    """Last-resort progress write for critical/unrecoverable errors.

    Writes directly to the progress file without temp-file atomicity or
    retries.  Used when the normal _write_progress path may not be
    available (e.g. before EmbeddingWorker is constructed, or from the
    watchdog timer thread).
    """
    progress_file = Path(queue_dir) / "progress_embedding.json"
    try:
        with open(progress_file, "w") as f:
            json.dump(
                {
                    "current": 0,
                    "total": 0,
                    "status": "error",
                    "current_file": "",
                    "error": error,
                    "errors_count": 1,
                    "last_error": error,
                    "timestamp": time.time(),
                },
                f,
            )
    except Exception:
        pass  # Nothing more we can do


class WatchdogTimer:
    """Process-level watchdog that force-exits if a file takes too long.

    Unlike ThreadPoolExecutor.future.result(timeout=N), this actually works
    for CPU-bound PyTorch inference that holds the GIL.
    """

    def __init__(self, timeout: float, logger: logging.Logger, queue_dir: Path):
        self.timeout = timeout
        self.logger = logger
        self.queue_dir = queue_dir
        self._timer: Optional[threading.Timer] = None
        self._current_file: str = ""

    def start(self, file_path: str) -> None:
        """Start the watchdog for a new file."""
        self.cancel()
        self._current_file = file_path
        self._timer = threading.Timer(self.timeout, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        """Cancel the current watchdog (file completed in time)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timeout(self) -> None:
        """Called when a file exceeds the timeout — force-exit the process."""
        error_msg = (
            f"File exceeded {self.timeout}s timeout: {self._current_file}. "
            f"Worker process killed. The queue can be restarted to continue "
            f"(already-embedded files will be skipped)."
        )
        self.logger.error(f"WATCHDOG: {error_msg}")
        _write_progress_fatal(self.queue_dir, error_msg)
        # Flush logs before exit
        logging.shutdown()
        os._exit(99)  # Hard exit, bypasses finally blocks


class EmbeddingWorker:
    """Worker process for computing embeddings."""

    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}

    def __init__(self, queue_dir: Path):
        self.queue_dir = Path(queue_dir)
        self.task_file = self.queue_dir / "embedding_task.json"
        self.progress_file = self.queue_dir / "progress_embedding.json"
        self.cancel_file = self.queue_dir / "cancel_embedding.signal"
        self.lock_file = self.queue_dir / "embedding.lock"

        self.cancelled = False
        self.errors_count = 0
        self.last_error = ""

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
        vocab_current: int = 0,
        vocab_total: int = 0,
    ) -> None:
        """Write progress to a JSON file for the main process to read.

        ``vocab_current`` / ``vocab_total`` carry term-level counts during
        ``loading_vocab`` / ``encoding_vocab`` so the UI can render a
        determinate progress bar against the term count rather than the
        file count (which is still ``current`` / ``total``).

        Uses a retry loop to handle Windows file locking race conditions
        where the main process may have the file open for reading.
        """
        progress_data = {
            "current": current,
            "total": total,
            "status": status,
            "current_file": current_file,
            "error": error,
            "errors_count": self.errors_count,
            "last_error": self.last_error,
            "vocab_current": vocab_current,
            "vocab_total": vocab_total,
            "timestamp": time.time(),
        }
        temp_file = self.progress_file.with_suffix(".tmp")
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with open(temp_file, "w") as f:
                    json.dump(progress_data, f)
                os.replace(str(temp_file), str(self.progress_file))
                return
            except PermissionError:
                # Windows: main process likely has the file open for reading
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                else:
                    self.logger.warning(
                        f"Could not update progress file after {max_retries} retries "
                        f"(status={status}, current={current}/{total})"
                    )
            except Exception as e:
                self.logger.error(f"Failed to write progress: {e}")
                return

    def _get_file_size_mb(self, file_path: str) -> float:
        """Get file size in MB, or -1 if not accessible."""
        try:
            return os.path.getsize(file_path) / (1024 * 1024)
        except OSError:
            return -1.0

    def run(self) -> None:  # noqa: C901
        """Main worker loop."""
        # Write initial progress immediately so the queue knows we're alive
        self._write_progress(0, 0, "starting")
        self.logger.info("=" * 60)
        self.logger.info("Embedding worker starting")

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
                self.logger.info("No files to process")
                self._write_progress(0, 0, "complete")
                return

            total = len(file_paths)
            self.logger.info(
                f"Task: {total} files, model={model_key}, device={device}, "
                f"phash={compute_phash}, keyframes={num_keyframes}"
            )
            self.logger.info(f"DB path: {db_path}")
            self.logger.info(f"Index dir: {index_dir}")

            # Initialize components — model download may happen here
            self._write_progress(0, total, "loading_model")
            self.logger.info("Initializing EmbeddingManager...")
            embedding_mgr = EmbeddingManager(model_key=model_key, device=device)

            # Check if model weights need downloading before loading
            config = embedding_mgr.model_config
            needs_download = embedding_mgr._check_model_needs_download(
                config["name"], config["pretrained"]
            )
            if needs_download:
                self._write_progress(
                    0,
                    total,
                    "downloading_model",
                    current_file=f"Downloading {config['name']} weights...",
                )
                self.logger.info(
                    f"Downloading CLIP model weights for {config['name']} "
                    f"({config['pretrained']})"
                )

            # Force model load (triggers download if needed)
            self.logger.info("Loading CLIP model (this may take a while)...")
            embedding_mgr._ensure_model_loaded()

            if needs_download:
                self.logger.info("Model download complete")
            self.logger.info("CLIP model ready")
            self._write_progress(0, total, "loading_model")

            # Load + encode the CLIP tagging vocabulary. Cached to
            # data/vocabulary/vocab.<model_key>.npz so subsequent runs
            # skip the ~20-60s encode step. If the vocabulary dir is
            # missing we simply skip tagging for this run.
            vocab_dir = get_base_path() / "data" / "vocabulary"
            self.logger.info(f"Loading tagging vocabulary from {vocab_dir}")
            self._write_progress(
                0, total, "loading_vocab", current_file="Reading vocabulary files..."
            )

            def _on_vocab_progress(phase: str, cur: int, tot: int) -> None:
                # Phase → status. ``current`` / ``total`` stay tied to the
                # file count (so the file-progress fields don't briefly go
                # backwards); term counts are reported in vocab_*.
                if phase == "loading":
                    self._write_progress(
                        0,
                        total,
                        "loading_vocab",
                        current_file="Reading vocabulary files...",
                    )
                elif phase == "encoding":
                    label = (
                        f"Encoding tag vocabulary ({cur:,} / {tot:,})"
                        if tot
                        else "Encoding tag vocabulary..."
                    )
                    self._write_progress(
                        0,
                        total,
                        "encoding_vocab",
                        current_file=label,
                        vocab_current=cur,
                        vocab_total=tot,
                    )
                elif phase == "cache_hit":
                    self._write_progress(
                        0,
                        total,
                        "loading_vocab",
                        current_file="Loaded cached vocabulary",
                        vocab_current=cur,
                        vocab_total=tot,
                    )

            vocab: Optional[Vocabulary] = None
            try:
                vocab = build_vocabulary(
                    vocab_dir, embedding_mgr, progress_callback=_on_vocab_progress
                )
                if vocab is not None:
                    self.logger.info(
                        f"Tagging vocabulary ready: {len(vocab.terms)} terms "
                        f"(dim={vocab.embeddings.shape[1]})"
                    )
                else:
                    self.logger.warning(
                        "Tagging vocabulary unavailable; CLIP tagging disabled."
                    )
            except Exception as e:
                self.logger.error(f"Failed to load tagging vocabulary: {e}")
                vocab = None

            self.logger.info("Initializing FAISS index...")
            faiss_mgr = FaissIndexManager(Path(index_dir))
            db_mgr = DatabaseManager(Path(db_path))

            # Load or create FAISS index
            if not faiss_mgr.load() or not faiss_mgr.check_model_match(model_key):
                faiss_mgr.create(
                    embedding_dim=embedding_mgr.embedding_dim,
                    model_key=model_key,
                )

            self._write_progress(0, total, "processing")
            self.logger.info("Starting file processing loop")

            processed = 0
            embedded_count = 0
            skipped_count = 0
            batch_paths = []
            hash_batch = []
            skipped_paths = []
            batch_start_time = time.time()

            # Random sample of file indices whose generated tags will be
            # dumped to data/clip_tag_samples.json for visual validation.
            sample_size = min(TAG_SAMPLE_COUNT, total) if vocab is not None else 0
            sample_indices: Set[int] = (
                set(random.sample(range(total), sample_size)) if sample_size else set()
            )
            sample_records: List[Dict[str, Any]] = []
            if sample_indices:
                self.logger.info(
                    f"Will dump tags for {sample_size} sampled files to "
                    f"clip_tag_samples.json (indices={sorted(sample_indices)})"
                )

            # Watchdog kills the process if a single file takes too long.
            # This is the only reliable way to interrupt CPU-bound PyTorch
            # inference that holds the GIL.
            watchdog = WatchdogTimer(PER_FILE_TIMEOUT, self.logger, self.queue_dir)

            for i, file_path in enumerate(file_paths):
                if self._check_cancelled():
                    self.logger.info("Cancelled by user")
                    self._write_progress(processed, total, "cancelled")
                    break

                try:
                    if not os.path.exists(file_path):
                        self.logger.warning(f"Skipping missing file: {file_path}")
                        skipped_paths.append(file_path)
                        processed += 1
                        skipped_count += 1
                        continue

                    file_name = Path(file_path).name
                    ext = Path(file_path).suffix.lower()
                    is_video = ext in self.VIDEO_EXTENSIONS
                    size_mb = self._get_file_size_mb(file_path)

                    self._write_progress(i, total, "processing", current_file=file_name)

                    # Per-file log: type, size, path
                    self.logger.debug(
                        f"[{i+1}/{total}] {'VIDEO' if is_video else 'IMAGE'} "
                        f"{size_mb:.1f}MB {file_path}"
                    )

                    file_start = time.time()
                    watchdog.start(file_path)

                    # Compute pHash
                    phash = None
                    if compute_phash:
                        if is_video:
                            phash = EmbeddingManager.compute_video_phash(file_path)
                        else:
                            phash = EmbeddingManager.compute_phash(file_path)

                    # Compute CLIP embedding
                    if is_video:
                        embedding = embedding_mgr.compute_video_embedding(
                            file_path, num_keyframes=num_keyframes
                        )
                    else:
                        embedding = embedding_mgr.compute_image_embedding(file_path)

                    watchdog.cancel()
                    file_elapsed = time.time() - file_start

                    # Log slow files so we can identify problematic content
                    if file_elapsed > SLOW_FILE_THRESHOLD:
                        self.logger.warning(
                            f"Slow file ({file_elapsed:.1f}s): {file_path} "
                            f"({size_mb:.1f}MB, {'video' if is_video else 'image'})"
                        )

                    if phash:
                        hash_batch.append((Path(file_path), phash))

                    if embedding is not None:
                        faiss_mgr.add(file_path, embedding)
                        batch_paths.append(file_path)
                        embedded_count += 1

                        # CLIP tagging — one matmul against the pre-encoded
                        # vocabulary + an INSERT OR UPDATE into the tag
                        # inverted index. Roughly free compared to the
                        # embedding computation itself.
                        if vocab is not None:
                            tags = select_tags(
                                embedding,
                                vocab,
                                top_k=TAG_TOP_K,
                                threshold=TAG_THRESHOLD,
                            )
                            if tags:
                                db_mgr.add_tag_indices(
                                    Path(file_path),
                                    [term for term, _axis, _score in tags],
                                    source="clip",
                                )
                            if i in sample_indices:
                                sample_records.append(
                                    {
                                        "file_path": file_path,
                                        "tags": [
                                            {
                                                "term": term,
                                                "axis": axis,
                                                "score": round(score, 4),
                                            }
                                            for term, axis, score in tags
                                        ],
                                    }
                                )
                    else:
                        skipped_paths.append(file_path)
                        skipped_count += 1

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
                        if skipped_paths:
                            db_mgr.mark_embedding_skipped(skipped_paths)
                            skipped_paths = []

                        elapsed = time.time() - batch_start_time
                        rate = 100 / elapsed if elapsed > 0 else 0
                        eta_seconds = (total - processed) / rate if rate > 0 else 0
                        eta_min = eta_seconds / 60

                        self.logger.info(
                            f"Progress: {processed}/{total} "
                            f"({embedded_count} embedded, {skipped_count} skipped, "
                            f"{self.errors_count} errors) "
                            f"[{rate:.1f} files/sec, ETA {eta_min:.0f}m]"
                        )
                        batch_start_time = time.time()

                except Exception as e:
                    watchdog.cancel()
                    self.errors_count += 1
                    self.last_error = f"{Path(file_path).name}: {e}"
                    self.logger.error(f"Failed to process {file_path}: {e}")
                    if self.errors_count <= 10:
                        traceback.print_exc()
                    skipped_paths.append(file_path)
                    processed += 1
                    continue

            # Final save
            self.logger.info("Saving final batch...")
            faiss_mgr.save()
            if hash_batch:
                db_mgr.save_media_hash_batch(hash_batch)
            if batch_paths:
                db_mgr.mark_embedded(batch_paths, model_key)
            if skipped_paths:
                db_mgr.mark_embedding_skipped(skipped_paths)

            # Dump CLIP-tag samples for visual validation before unloading
            # the model in case we need to re-encode anything. Lives under
            # data/ (not data/vocabulary/) per the user's spec.
            if sample_records:
                samples_path = vocab_dir.parent / "clip_tag_samples.json"
                try:
                    samples_path.write_text(
                        json.dumps(
                            {
                                "model_key": model_key,
                                "top_k": TAG_TOP_K,
                                "threshold": TAG_THRESHOLD,
                                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "samples": sample_records,
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    self.logger.info(
                        f"Wrote {len(sample_records)} CLIP-tag samples to "
                        f"{samples_path}"
                    )
                except Exception as e:
                    self.logger.error(f"Failed to write tag samples: {e}")

            # Unload model to free GPU memory
            embedding_mgr.unload_model()

            if not self.cancelled:
                self._write_progress(processed, total, "complete")
                self.logger.info(
                    f"Embedding computation complete: "
                    f"{processed}/{total} processed, "
                    f"{embedded_count} embedded, "
                    f"{skipped_count} skipped, "
                    f"{self.errors_count} errors"
                )

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            # Write error to progress file FIRST — before any logging that
            # could block on a full stderr pipe (the subprocess stderr is a
            # pipe that the parent only drains after we exit).
            _write_progress_fatal(self.queue_dir, error_msg)
            self.logger.error(f"Worker failed with unhandled exception: {error_msg}")
            self.logger.error(traceback.format_exc())

        self.logger.info("Embedding worker exiting")
        self.logger.info("=" * 60)
        # Ensure all log handlers are flushed
        logging.shutdown()


def setup_logging(queue_dir: Path) -> None:
    """Set up logging for the worker process."""
    log_dir = get_data_dir().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "embedding_worker.log"

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # Log the startup information
    root_logger.info(f"Embedding worker process started (PID={os.getpid()})")
    root_logger.info(f"Log file: {log_file}")
    root_logger.info(f"Queue dir: {queue_dir}")
    root_logger.info(f"Python: {sys.executable}")
    root_logger.info(f"Platform: {platform.platform()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python embedding_worker.py <queue_dir>", file=sys.stderr)
        sys.exit(1)

    queue_dir = Path(sys.argv[1])
    setup_logging(queue_dir)

    try:
        worker = EmbeddingWorker(queue_dir)
        worker.run()
    except Exception as e:
        # Catch-all for any unhandled exception, including import errors
        # triggered lazily during construction or run().
        error_msg = f"{type(e).__name__}: {e}"
        # Write progress FIRST so the parent process can surface the error,
        # then log (logging to file is safe but must come second).
        _write_progress_fatal(queue_dir, error_msg)
        logging.getLogger("embedding_worker").error(
            f"Fatal worker error: {error_msg}\n{traceback.format_exc()}"
        )
        logging.shutdown()
        sys.exit(1)
