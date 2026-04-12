import sys
import enum
import time
from metascan.utils.startup_profiler import log_startup, profile_phase

log_startup("main_window.py: Module loading started")

log_startup("  Importing PyQt6.QtWidgets...")
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QLabel,
    QSplitter,
    QScrollArea,
    QGridLayout,
    QFrame,
    QPushButton,
    QToolBar,
    QMessageBox,
    QProgressBar,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
    QSizePolicy,
    QStackedWidget,
)

log_startup("  Importing PyQt6.QtCore...")
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QTimer
from typing import Tuple, Dict, List, Optional

log_startup("  Importing PyQt6.QtGui...")
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QActionGroup, QMovie

log_startup("  Importing qt_material...")
from qt_material import apply_stylesheet, list_themes

log_startup("  Importing UI components...")
from metascan.ui.config_dialog import ConfigDialog
from metascan.ui.duplicate_finder_dialog import DuplicateFinderDialog
from metascan.ui.similarity_settings_dialog import SimilaritySettingsDialog
from metascan.ui.filters_panel import FiltersPanel
from metascan.ui.thumbnail_view import ThumbnailView
from metascan.ui.virtual_thumbnail_view import VirtualThumbnailView
from metascan.ui.metadata_panel import MetadataPanel
from metascan.ui.media_viewer import MediaViewer
from metascan.ui.slideshow_viewer import SlideshowViewer
from metascan.ui.upscale_dialog import UpscaleDialog, ModelSetupDialog
from metascan.ui.upscale_queue_window import UpscaleQueueWindow

log_startup("  Importing core components...")
from metascan.core.scanner import Scanner, ThreadedScanner
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media_upscaler import MediaUpscaler
from metascan.core.upscale_queue_process import ProcessUpscaleQueue
from metascan.cache.thumbnail import ThumbnailCache
from metascan.utils.app_paths import (
    get_data_dir,
    get_config_path,
    get_thumbnail_cache_dir,
)
from metascan.utils.path_utils import to_native_path
import os
import json
from pathlib import Path
import shutil
import platform
import subprocess
import logging

logger = logging.getLogger(__name__)

log_startup("main_window.py: All imports complete")


class ScannerThread(QThread):
    progress_updated = pyqtSignal(int, int, str)  # current, total, current_file
    directory_progress_updated = pyqtSignal(
        int, int, str
    )  # current_dir, total_dirs, dir_path
    stale_cleanup_started = pyqtSignal()
    scan_complete = pyqtSignal(int, int)  # processed_count, stale_count
    scan_error = pyqtSignal(str)  # error message

    def __init__(self, scanner, directories, full_scan=False):
        super().__init__()
        self.scanner = scanner
        self.directories = directories
        self.full_scan = full_scan
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        # If using ThreadedScanner, signal it to stop
        if hasattr(self.scanner, "stop_scanning"):
            self.scanner.stop_scanning()

    def _remove_stale_entries(self) -> int:
        """Remove DB entries for files that no longer exist on disk.

        Re-discovers all media files from the configured scan directories
        and deletes any database entries whose paths are not found.
        """
        try:
            # Collect all files currently on disk across all scan directories
            all_disk_paths: set = set()
            for dir_info in self.directories:
                dir_path = dir_info["filepath"]
                recursive = dir_info["search_subfolders"]
                found = self.scanner._find_media_files(Path(dir_path), recursive)
                all_disk_paths.update(str(p) for p in found)

            # Compare against database
            db_paths = self.scanner.db_manager.get_existing_file_paths()
            stale_paths = db_paths - all_disk_paths

            if stale_paths:
                self.scanner.db_manager.delete_media_batch(
                    [Path(p) for p in stale_paths]
                )
                logger.info(f"Removed {len(stale_paths)} stale entries from database")
            return len(stale_paths)
        except Exception as e:
            logger.error(f"Failed to remove stale entries: {e}")
            return 0

    def run(self):
        total_processed = 0
        total_dirs = len(self.directories)

        try:
            for dir_index, dir_info in enumerate(self.directories, 1):
                if self._is_cancelled:
                    break

                # Emit directory progress
                dir_path = dir_info["filepath"]
                self.directory_progress_updated.emit(dir_index, total_dirs, dir_path)

                def progress_callback(current, total, file_path):
                    if self._is_cancelled:
                        return False  # Return False to stop scanning
                    self.progress_updated.emit(current, total, str(file_path))
                    return True  # Continue scanning

                processed = self.scanner.scan_directory(
                    dir_path,
                    recursive=dir_info["search_subfolders"],
                    progress_callback=progress_callback,
                    full_scan=self.full_scan,
                )
                total_processed += processed

            if not self._is_cancelled:
                self.stale_cleanup_started.emit()
                stale_count = self._remove_stale_entries()
                self.scan_complete.emit(total_processed, stale_count)
        except Exception as e:
            self.scan_error.emit(str(e))


class ScanPhase(enum.IntEnum):
    PREPARATION = 0
    CONFIRMATION = 1
    SCANNING = 2
    STALE_CLEANUP = 3
    EMBEDDING = 4
    COMPLETE = 5


# Step labels shown in the step tracker (phases map to steps)
_STEP_LABELS = ["Prepare", "Confirm", "Scan", "Embed", "Done"]
# Map each ScanPhase to its step index (STALE_CLEANUP shares step 2 with SCANNING)
_PHASE_TO_STEP = {
    ScanPhase.PREPARATION: 0,
    ScanPhase.CONFIRMATION: 1,
    ScanPhase.SCANNING: 2,
    ScanPhase.STALE_CLEANUP: 2,
    ScanPhase.EMBEDDING: 3,
    ScanPhase.COMPLETE: 4,
}


class ScanProgressDialog(QDialog):
    scan_confirmed = pyqtSignal(bool)  # full_clean requested

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Scan Media Files")
        self.setModal(True)
        self.setFixedSize(600, 420)

        self.cancel_requested = False
        self._current_phase = ScanPhase.PREPARATION
        self._completed_steps: set = set()
        self._skipped_steps: set = set()
        self._overall_start_time = time.monotonic()
        self._phase_start_time = self._overall_start_time
        self._phase_start_items = 0
        self._current_items = 0
        self._total_items = 0

        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(8)

        # --- Step tracker ---
        root_layout.addWidget(self._create_step_tracker())

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(sep)

        # --- Stacked content area ---
        self._stack = QStackedWidget()
        self._stack.addWidget(self._create_preparation_page())  # 0
        self._stack.addWidget(self._create_confirmation_page())  # 1
        self._stack.addWidget(self._create_scanning_page())  # 2
        self._stack.addWidget(self._create_stale_cleanup_page())  # 3
        self._stack.addWidget(self._create_embedding_page())  # 4
        self._stack.addWidget(self._create_complete_page())  # 5
        root_layout.addWidget(self._stack, 1)

        # --- Time display ---
        time_row = QHBoxLayout()
        self._elapsed_label = QLabel("Elapsed: 0s")
        self._eta_label = QLabel("")
        time_row.addWidget(self._elapsed_label)
        time_row.addStretch()
        time_row.addWidget(self._eta_label)
        root_layout.addLayout(time_row)

        # --- Button bar ---
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        root_layout.addWidget(self._cancel_btn)

        # Elapsed-time timer
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._update_elapsed_time)
        self._tick_timer.start(1000)

    # ------------------------------------------------------------------ #
    #  Step tracker
    # ------------------------------------------------------------------ #
    def _create_step_tracker(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 4, 10, 4)
        self._step_circles: list = []
        self._step_labels_widgets: list = []
        self._step_lines: list = []

        for i, label_text in enumerate(_STEP_LABELS):
            if i > 0:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFixedHeight(2)
                line.setStyleSheet("background-color: #555;")
                layout.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
                self._step_lines.append(line)

            step_w = QWidget()
            step_l = QVBoxLayout(step_w)
            step_l.setContentsMargins(0, 0, 0, 0)
            step_l.setSpacing(2)

            circle = QLabel(str(i + 1))
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setFixedSize(28, 28)
            circle.setStyleSheet(
                "border-radius: 14px; border: 2px solid #555; color: #888; font-weight: bold;"
            )
            step_l.addWidget(circle, 0, Qt.AlignmentFlag.AlignCenter)

            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888; font-size: 11px;")
            step_l.addWidget(lbl)

            self._step_circles.append(circle)
            self._step_labels_widgets.append(lbl)
            layout.addWidget(step_w, 0)

        return container

    def _update_step_tracker(self) -> None:
        active_step = _PHASE_TO_STEP[self._current_phase]
        for i in range(len(_STEP_LABELS)):
            circle = self._step_circles[i]
            lbl = self._step_labels_widgets[i]
            if i in self._skipped_steps:
                circle.setText("—")
                circle.setStyleSheet(
                    "border-radius: 14px; border: 2px solid #555; color: #666; font-weight: bold;"
                )
                lbl.setStyleSheet("color: #666; font-size: 11px;")
            elif i in self._completed_steps:
                circle.setText("✓")
                circle.setStyleSheet(
                    "border-radius: 14px; background-color: #4caf50; color: white; font-weight: bold;"
                )
                lbl.setStyleSheet("color: #4caf50; font-size: 11px;")
            elif i == active_step:
                circle.setStyleSheet(
                    "border-radius: 14px; background-color: #2196f3; color: white; font-weight: bold;"
                )
                lbl.setStyleSheet("color: #2196f3; font-size: 11px; font-weight: bold;")
            else:
                circle.setText(str(i + 1))
                circle.setStyleSheet(
                    "border-radius: 14px; border: 2px solid #555; color: #888; font-weight: bold;"
                )
                lbl.setStyleSheet("color: #888; font-size: 11px;")

            # Update connecting lines
            if i > 0:
                line = self._step_lines[i - 1]
                if i <= active_step or i in self._completed_steps:
                    line.setStyleSheet("background-color: #4caf50;")
                else:
                    line.setStyleSheet("background-color: #555;")

    # ------------------------------------------------------------------ #
    #  Content pages
    # ------------------------------------------------------------------ #
    def _create_preparation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setTextVisible(False)
        layout.addWidget(bar)
        lbl = QLabel("Counting media files and checking database...")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        layout.addStretch()
        return page

    def _create_confirmation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()

        self._confirm_info_label = QLabel("Preparing scan details...")
        self._confirm_info_label.setWordWrap(True)
        layout.addWidget(self._confirm_info_label)

        layout.addSpacing(10)

        self._full_clean_checkbox = QCheckBox("Full clean and scan")
        self._full_clean_checkbox.setToolTip(
            "Clear all existing data (media records, indices, and thumbnails) before scanning.\n"
            "This ensures a completely fresh start but will remove all previously scanned data."
        )
        layout.addWidget(self._full_clean_checkbox)

        layout.addSpacing(10)

        self._start_scan_btn = QPushButton("Start Scan")
        self._start_scan_btn.setFixedWidth(140)
        self._start_scan_btn.clicked.connect(self._on_start_scan_clicked)
        layout.addWidget(self._start_scan_btn, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        return page

    def _create_scanning_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # Directory progress bar
        self._scan_dir_bar = QProgressBar()
        self._scan_dir_bar.setTextVisible(True)
        self._scan_dir_bar.setFormat("Directory %v of %m")
        layout.addWidget(self._scan_dir_bar)

        # File progress bar
        self._scan_file_bar = QProgressBar()
        self._scan_file_bar.setTextVisible(True)
        self._scan_file_bar.setFormat("File %v of %m")
        layout.addWidget(self._scan_file_bar)

        layout.addSpacing(8)

        self._scan_dir_label = QLabel("Preparing to scan...")
        layout.addWidget(self._scan_dir_label)

        self._scan_file_label = QLabel("")
        layout.addWidget(self._scan_file_label)

        self._scan_progress_label = QLabel("0 / 0 files")
        layout.addWidget(self._scan_progress_label)

        layout.addStretch()
        return page

    def _create_stale_cleanup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate
        bar.setTextVisible(False)
        layout.addWidget(bar)
        lbl = QLabel("Removing stale database entries for deleted files...")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        layout.addStretch()
        return page

    def _create_embedding_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self._embed_bar = QProgressBar()
        self._embed_bar.setTextVisible(True)
        self._embed_bar.setFormat("File %v of %m")
        layout.addWidget(self._embed_bar)

        layout.addSpacing(8)

        self._embed_status_label = QLabel("Starting embedding worker...")
        layout.addWidget(self._embed_status_label)

        self._embed_progress_label = QLabel("0 / 0 files")
        layout.addWidget(self._embed_progress_label)

        layout.addStretch()
        return page

    def _create_complete_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()

        self._complete_label = QLabel("")
        self._complete_label.setWordWrap(True)
        self._complete_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._complete_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._complete_label)

        layout.addStretch()
        return page

    # ------------------------------------------------------------------ #
    #  Phase transitions
    # ------------------------------------------------------------------ #
    def set_phase(self, phase: ScanPhase) -> None:
        prev_step = _PHASE_TO_STEP[self._current_phase]
        new_step = _PHASE_TO_STEP[phase]

        # Mark prior steps as completed (unless skipped)
        for s in range(prev_step, new_step):
            if s not in self._skipped_steps:
                self._completed_steps.add(s)

        self._current_phase = phase
        self._stack.setCurrentIndex(int(phase))
        self._phase_start_time = time.monotonic()
        self._phase_start_items = 0
        self._current_items = 0
        self._total_items = 0
        self.cancel_requested = False

        if phase == ScanPhase.COMPLETE:
            self._cancel_btn.setText("Close")
            self._tick_timer.stop()
            self._eta_label.setText("")
            self._completed_steps.add(new_step)
        elif phase == ScanPhase.STALE_CLEANUP:
            self._cancel_btn.setEnabled(False)
            self._eta_label.setText("")
        else:
            self._cancel_btn.setEnabled(True)
            self._cancel_btn.setText("Cancel")

        self._update_step_tracker()

    def show_confirmation(
        self, total_dirs: int, total_files: int, unprocessed_files: int
    ) -> None:
        info = f"Directories to scan: {total_dirs}\n"
        info += f"Total media files found: {total_files:,}\n"
        info += f"New files to process: {unprocessed_files:,}\n\n"
        if unprocessed_files == 0:
            info += "All files are already in the database."
        else:
            info += "This may take several minutes depending on file count."
        self._confirm_info_label.setText(info)
        self.set_phase(ScanPhase.CONFIRMATION)

    def update_directory_progress(
        self, current_dir: int, total_dirs: int, dir_path: str
    ) -> None:
        self._scan_dir_bar.setMaximum(total_dirs)
        self._scan_dir_bar.setValue(current_dir)
        if isinstance(dir_path, str):
            p = Path(dir_path)
            display = f"{p.parent.name}/{p.name}" if p.parent.name else p.name
            self._scan_dir_label.setText(f"Directory: {display}")

    def update_file_progress(self, current: int, total: int, file_path: str) -> None:
        self._scan_file_bar.setMaximum(total)
        self._scan_file_bar.setValue(current)
        self._scan_progress_label.setText(f"{current} / {total} files")
        self._current_items = current
        self._total_items = total
        if isinstance(file_path, str):
            self._scan_file_label.setText(f"Processing: {Path(file_path).name}")

    def enter_stale_cleanup(self) -> None:
        self.set_phase(ScanPhase.STALE_CLEANUP)

    def update_embedding_progress(
        self, current: int, total: int, status_text: str
    ) -> None:
        self._embed_bar.setMaximum(max(total, 1))
        self._embed_bar.setValue(current)
        self._embed_progress_label.setText(f"{current} / {total} files")
        self._embed_status_label.setText(status_text)
        self._current_items = current
        self._total_items = total

    def skip_embedding_step(self) -> None:
        self._skipped_steps.add(3)

    def show_completion(self, summary_text: str) -> None:
        self._complete_label.setText(summary_text)
        self.set_phase(ScanPhase.COMPLETE)

    def is_full_clean_requested(self) -> bool:
        return self._full_clean_checkbox.isChecked()

    # ------------------------------------------------------------------ #
    #  Time tracking
    # ------------------------------------------------------------------ #
    def _update_elapsed_time(self) -> None:
        elapsed = time.monotonic() - self._overall_start_time
        self._elapsed_label.setText(f"Elapsed: {self._format_time(elapsed)}")

        # ETA during scanning / embedding phases
        if self._current_phase in (ScanPhase.SCANNING, ScanPhase.EMBEDDING):
            phase_elapsed = time.monotonic() - self._phase_start_time
            done = self._current_items - self._phase_start_items
            remaining = self._total_items - self._current_items
            if done >= 5 and phase_elapsed >= 3 and remaining > 0:
                rate = phase_elapsed / done
                eta = rate * remaining
                self._eta_label.setText(f"Remaining: ~{self._format_time(eta)}")
            else:
                self._eta_label.setText("")
        else:
            self._eta_label.setText("")

    @staticmethod
    def _format_time(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        return f"{m}m {s:02d}s"

    # ------------------------------------------------------------------ #
    #  Button handlers
    # ------------------------------------------------------------------ #
    def _on_start_scan_clicked(self) -> None:
        self.scan_confirmed.emit(self._full_clean_checkbox.isChecked())

    def _on_cancel_clicked(self) -> None:
        if self._current_phase == ScanPhase.COMPLETE:
            self.accept()
            return

        if self._current_phase in (ScanPhase.PREPARATION, ScanPhase.CONFIRMATION):
            self.reject()
            return

        # Active work phases — ask for confirmation
        phase_name = (
            "embedding" if self._current_phase == ScanPhase.EMBEDDING else "scanning"
        )
        reply = QMessageBox.question(
            self,
            f"Cancel {phase_name.title()}",
            f"Are you sure you want to cancel the {phase_name} process?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.cancel_requested = True
            if self._current_phase == ScanPhase.SCANNING:
                self._scan_file_label.setText("Cancelling...")
            elif self._current_phase == ScanPhase.EMBEDDING:
                self._embed_status_label.setText("Cancelling...")


class ScanPreparationThread(QThread):
    """Background thread to count unprocessed media files."""

    preparation_complete = pyqtSignal(
        int, int, int
    )  # total_dirs, total_files, unprocessed_files

    def __init__(self, directories: List[Dict], db_manager: DatabaseManager):
        super().__init__()
        self.directories = directories
        self.db_manager = db_manager

    def run(self):
        SUPPORTED_EXTENSIONS = {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".mp4",
            ".webm",
        }

        total_dirs = len(self.directories)
        all_files: List[Path] = []

        # Collect all media files from all directories
        for dir_info in self.directories:
            dir_path = Path(dir_info["filepath"])
            if dir_path.exists():
                recursive = dir_info.get("search_subfolders", True)
                try:
                    if recursive:
                        for ext in SUPPORTED_EXTENSIONS:
                            all_files.extend(dir_path.rglob(f"*{ext}"))
                            all_files.extend(dir_path.rglob(f"*{ext.upper()}"))
                    else:
                        for ext in SUPPORTED_EXTENSIONS:
                            all_files.extend(dir_path.glob(f"*{ext}"))
                            all_files.extend(dir_path.glob(f"*{ext.upper()}"))
                except Exception as e:
                    print(f"Error counting files in {dir_path}: {e}")

        # De-duplicate files (needed for case-insensitive file systems like Windows)
        all_files = list(set(all_files))
        total_files = len(all_files)

        # Get existing paths from database
        existing_paths = self.db_manager.get_existing_file_paths()

        # Count unprocessed files
        unprocessed_files = sum(1 for f in all_files if str(f) not in existing_paths)

        self.preparation_complete.emit(total_dirs, total_files, unprocessed_files)


class SimilaritySearchWorker(QThread):
    """Background worker for FAISS similarity search.

    Loads the index (if not cached) and performs the search off the UI thread.
    """

    results_ready = pyqtSignal(list, object)  # results, faiss_mgr
    error = pyqtSignal(str)  # error message

    def __init__(
        self,
        faiss_mgr,  # Optional[FaissIndexManager]
        file_path: str,
        top_k: int,
        index_dir,  # Path
        parent=None,
    ):
        super().__init__(parent)
        self.faiss_mgr = faiss_mgr
        self.file_path = file_path
        self.top_k = top_k
        self.index_dir = index_dir

    def run(self):
        try:
            from metascan.core.embedding_manager import FaissIndexManager

            # Load index if not cached
            if self.faiss_mgr is None:
                self.faiss_mgr = FaissIndexManager(self.index_dir)
                if not self.faiss_mgr.load():
                    self.error.emit(
                        "No embedding index found. Please build the similarity "
                        "index first via Tools > Similarity Settings."
                    )
                    return

            # Get embedding for query file
            embedding = self.faiss_mgr.get_embedding(self.file_path)
            if embedding is None:
                self.error.emit(
                    "This file hasn't been indexed yet. Please rebuild the "
                    "similarity index via Tools > Similarity Settings to include it."
                )
                return

            # Search
            results = self.faiss_mgr.search(embedding, top_k=self.top_k)
            self.results_ready.emit(results, self.faiss_mgr)

        except Exception as e:
            self.error.emit(f"Similarity search failed: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        log_startup("MainWindow.__init__: Starting")
        super().__init__()
        self.setWindowTitle("Metascan - AI Media Browser")

        # Initialize logger
        self.logger = logging.getLogger(__name__)

        # Initialize theme before other components
        log_startup("  Listing available themes...")
        self.available_themes = list_themes()
        self.current_theme = None

        # Initialize components
        log_startup("  Loading config...")
        self.config_file = str(get_config_path())
        self.config = self.load_config()

        # Sort order state
        self.current_sort_order = self.config.get("sort_order", "file_name")

        # Initialize save timer for debouncing geometry saves
        self.geometry_save_timer = QTimer()
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self.save_window_geometry)

        # Restore window geometry from config or use defaults
        window_geometry = self.config.get("window_geometry", {})
        if window_geometry:
            self.setGeometry(
                window_geometry.get("x", 100),
                window_geometry.get("y", 100),
                window_geometry.get("width", 1200),
                window_geometry.get("height", 800),
            )
        else:
            # Set window size based on thumbnail size from config
            thumbnail_size = tuple(self.config.get("thumbnail_size", [200, 200]))
            # Window needs to fit: filter panel (250) + thumbnails (2 cols minimum) + metadata (350)
            min_window_width = 250 + ((thumbnail_size[0] + 10) * 2 + 40) + 350
            self.setGeometry(100, 100, max(1200, min_window_width), 800)

        with profile_phase("Applying theme"):
            self.load_and_apply_theme()

        with profile_phase("Initializing DatabaseManager"):
            db_path = get_data_dir()
            self.db_manager = DatabaseManager(db_path)

        # Embedding queue — singleton, survives dialog close/reopen
        from metascan.core.embedding_queue import EmbeddingQueue

        self.embedding_queue = EmbeddingQueue(parent=self)

        # Similarity search cache — loaded on first use, invalidated on index rebuild
        self._faiss_mgr = None  # Optional[FaissIndexManager]
        self._similarity_config = None  # Optional[Dict]
        self._similarity_worker = None  # Optional[SimilaritySearchWorker]

        # Current filter state
        self.current_filters = {}
        self.filtered_media_paths = (
            None  # None means no filters, empty set means filtered to nothing
        )
        self.favorites_active = False  # Track if favorites filter is active
        self.all_media = []  # Cache of all media for filtering
        self._processed_upscale_tasks = (
            set()
        )  # Track processed upscale task IDs to avoid duplicates

        # Initialize thumbnail cache for metadata panel
        with profile_phase("Initializing ThumbnailCache"):
            cache_dir = get_thumbnail_cache_dir()
            # Get thumbnail size from config, default to (200, 200)
            thumbnail_size = tuple(self.config.get("thumbnail_size", [200, 200]))
            self.thumbnail_cache = ThumbnailCache(cache_dir, thumbnail_size)

        # Initialize scanner with thumbnail cache
        with profile_phase("Initializing ThreadedScanner"):
            self.scanner = ThreadedScanner(
                self.db_manager,
                num_workers=4,
                batch_size=10,
                thumbnail_cache=self.thumbnail_cache,
            )

        # Initialize upscale components
        with profile_phase("Initializing MediaUpscaler"):
            models_dir = get_data_dir() / "models"
            self.media_upscaler = MediaUpscaler(
                models_dir=models_dir, device="auto", tile_size=512, debug=False
            )

        # Initialize process-based upscale queue
        with profile_phase("Initializing ProcessUpscaleQueue"):
            queue_dir = get_data_dir() / "queue"
            self.upscale_queue = ProcessUpscaleQueue(queue_dir)

        # Connect queue signals to show/hide spinner
        self.upscale_queue.task_added.connect(self._on_task_added)
        self.upscale_queue.task_updated.connect(self._on_task_updated)
        self.upscale_queue.task_removed.connect(self._on_task_removed)

        # Set up polling timer for queue updates
        self.queue_poll_timer = QTimer()
        self.queue_poll_timer.timeout.connect(self._poll_queue_updates)
        self.queue_poll_timer.start(500)  # Poll every 500ms

        # Start processing if there are pending tasks (but don't show spinner yet - toolbar not created)
        if self.upscale_queue.get_next_pending():
            self.upscale_queue.start_processing()

        # Initialize upscale queue window (hidden initially)
        self.upscale_queue_window = None

        # Create media viewer (initially hidden)
        with profile_phase("Creating MediaViewer"):
            self.media_viewer = MediaViewer(
                db_manager=self.db_manager, thumbnail_cache=self.thumbnail_cache
            )  # Create without parent to control positioning
            self.media_viewer.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            )
            self.media_viewer.closed.connect(self.on_media_viewer_closed)
            self.media_viewer.media_changed.connect(self.on_viewer_media_changed)
            self.media_viewer.delete_requested.connect(
                lambda media: self._confirm_and_delete_media(media, from_viewer=True)
            )
            self.media_viewer.delete_with_undo_requested.connect(self._delete_with_undo)
            self.media_viewer.restore_requested.connect(self._restore_deleted_media)
            self.media_viewer.favorite_toggled.connect(self.on_viewer_favorite_toggled)

        # Create slideshow viewer (initially hidden)
        with profile_phase("Creating SlideshowViewer"):
            self.slideshow_viewer = SlideshowViewer(db_manager=self.db_manager)
            self.slideshow_viewer.closed.connect(self.on_slideshow_closed)
            self.slideshow_viewer.media_changed.connect(self.on_viewer_media_changed)
            self.slideshow_viewer.favorite_toggled.connect(
                self.on_viewer_favorite_toggled
            )

        # Create central widget and main layout
        log_startup("  Creating central widget and layout...")
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Create splitter for three panels
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        # Left panel - Filters
        with profile_phase("Creating filter panel"):
            filter_panel = self._create_filter_panel()
            self.main_splitter.addWidget(filter_panel)

        # Middle panel - Thumbnails
        with profile_phase("Creating thumbnail panel"):
            thumbnail_panel = self._create_thumbnail_panel()
            self.main_splitter.addWidget(thumbnail_panel)

        # Right panel - Metadata
        with profile_phase("Creating metadata panel"):
            metadata_panel = self._create_metadata_panel()
            self.main_splitter.addWidget(metadata_panel)

        # Restore splitter sizes from config or use defaults
        splitter_sizes = self.config.get("splitter_sizes", [])
        if splitter_sizes and len(splitter_sizes) == 3:
            self.main_splitter.setSizes(splitter_sizes)
        else:
            # Set initial splitter sizes (proportional)
            # Adjust middle panel size based on thumbnail size
            thumbnail_size = tuple(self.config.get("thumbnail_size", [200, 200]))
            # Calculate width needed for at least 2 columns of thumbnails + some extra
            min_thumbnail_panel_width = (
                thumbnail_size[0] + 10
            ) * 2 + 60  # 2 thumbnails + spacing + margins + scrollbar
            self.main_splitter.setSizes([250, min_thumbnail_panel_width, 350])

        # Force the splitter to honor our sizes by setting stretch factors
        self.main_splitter.setStretchFactor(0, 0)  # Filter panel - don't stretch
        self.main_splitter.setStretchFactor(1, 1)  # Thumbnail panel - can stretch
        self.main_splitter.setStretchFactor(2, 0)  # Metadata panel - don't stretch

        # Connect signal to save splitter sizes when moved
        self.main_splitter.splitterMoved.connect(self.on_splitter_moved)

        # Create menu bar
        with profile_phase("Creating menu bar"):
            self._create_menu_bar()

        # Create toolbar
        with profile_phase("Creating toolbar"):
            self._create_toolbar()

        # Show spinner if there are pending tasks from previous session
        pending_task = self.upscale_queue.get_next_pending()
        if pending_task:
            # Show spinner for existing pending tasks
            self._show_spinner()

        # Also check for PROCESSING tasks and show spinner if found
        all_tasks = self.upscale_queue.get_all_tasks()
        processing_tasks = [
            task for task in all_tasks if task.status.value == "processing"
        ]
        if processing_tasks:
            self._show_spinner()

        # Setup keyboard shortcuts
        self._setup_shortcuts()

        # Initialize Open action states
        self._update_open_actions_state()
        log_startup("MainWindow.__init__: Complete")

    def _create_filter_panel(self) -> QWidget:
        log_startup("    Creating FiltersPanel widget...")
        self.filters_panel = FiltersPanel()

        # Set maximum width constraint
        self.filters_panel.setMaximumWidth(395)

        # Connect signals
        self.filters_panel.filters_changed.connect(self.on_filters_changed)
        self.filters_panel.sort_changed.connect(self.on_sort_order_changed)
        self.filters_panel.favorites_toggled.connect(self.on_favorites_toggled)
        self.filters_panel.content_search_requested.connect(self._on_content_search)
        self.filters_panel.content_search_cleared.connect(
            self._on_content_search_cleared
        )
        self.filters_panel.set_refresh_callback(self.refresh_filters)

        # Load initial filter data
        log_startup("    Loading initial filter data from database...")
        self.refresh_filters()

        return self.filters_panel

    def on_splitter_moved(self):
        """Handle splitter movement and save the new sizes."""
        self.save_splitter_sizes()

    def _create_thumbnail_panel(self) -> QWidget:
        # Create the thumbnail view with thumbnail size and scroll step from config
        thumbnail_size = tuple(self.config.get("thumbnail_size", [200, 200]))
        scroll_step = self.config.get("scroll_wheel_step", 120)
        self.thumbnail_view = VirtualThumbnailView(
            thumbnail_size=thumbnail_size, scroll_step=scroll_step
        )

        # Connect selection signal
        self.thumbnail_view.selection_changed.connect(self.on_thumbnail_selected)
        self.thumbnail_view.favorite_toggled.connect(self.on_favorite_toggled)
        self.thumbnail_view.multi_selection_changed.connect(
            self.on_multi_selection_changed
        )

        # Connect thumbnail size change signal
        self.thumbnail_view.thumbnail_size_changed.connect(
            self.on_thumbnail_size_changed
        )

        # Connect double-click to open media viewer
        self.thumbnail_view.scroll_area.item_double_clicked.connect(
            self.on_thumbnail_double_clicked
        )

        # Connect context menu signals
        self.thumbnail_view.open_requested.connect(self.on_context_open_requested)
        self.thumbnail_view.open_folder_requested.connect(
            self.on_context_open_folder_requested
        )
        self.thumbnail_view.delete_requested.connect(self.on_context_delete_requested)
        self.thumbnail_view.upscale_requested.connect(self.on_context_upscale_requested)
        self.thumbnail_view.find_similar_requested.connect(self._on_find_similar)

        # Load initial media if any exists
        self.load_all_media()

        return self.thumbnail_view

    def _create_metadata_panel(self) -> QWidget:
        # Create the enhanced metadata panel
        self.metadata_panel = MetadataPanel()

        # Set maximum width constraint
        self.metadata_panel.setMaximumWidth(380)

        # Set the thumbnail cache for preview images
        self.metadata_panel.set_thumbnail_cache(self.thumbnail_cache)

        return self.metadata_panel

    def _create_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        # Open action - opens the selected media file
        self.open_action = QAction("Open", self)
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self._open_selected_media)
        file_menu.addAction(self.open_action)

        # Open Folder action - opens the folder containing the selected media
        self.open_folder_action = QAction("Open Folder...", self)
        self.open_folder_action.setShortcut(QKeySequence("Ctrl+Alt+O"))
        self.open_folder_action.triggered.connect(self._open_selected_folder)
        file_menu.addAction(self.open_folder_action)

        file_menu.addSeparator()

        self.delete_action = QAction("Delete file...", self)
        self.delete_action.setShortcut("Ctrl+D")
        self.delete_action.triggered.connect(self._handle_delete_shortcut)
        file_menu.addAction(self.delete_action)

        file_menu.addSeparator()

        # Upscale action
        self.upscale_action = QAction("Upscale...", self)
        self.upscale_action.setShortcut("Ctrl+U")
        self.upscale_action.triggered.connect(self._handle_upscale)
        file_menu.addAction(self.upscale_action)

        file_menu.addSeparator()

        config_action = QAction("Configuration...", self)
        config_action.triggered.connect(self._open_config)
        file_menu.addAction(config_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("View")

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.refresh_view)
        view_menu.addAction(refresh_action)

        # Slideshow action
        slideshow_action = QAction("Slideshow", self)
        slideshow_action.setShortcut("Ctrl+Shift+S")
        slideshow_action.triggered.connect(self.show_slideshow)
        view_menu.addAction(slideshow_action)

        # Upscale Queue Window action
        upscale_queue_action = QAction("Upscale Queue Window", self)
        upscale_queue_action.triggered.connect(self._show_upscale_queue)
        view_menu.addAction(upscale_queue_action)

        view_menu.addSeparator()

        # Sort by submenu
        sort_menu = view_menu.addMenu("Sort by")

        # Create action group for exclusive selection
        self.sort_action_group = QActionGroup(self)

        # File Name sort
        self.sort_filename_action = QAction("File Name", self)
        self.sort_filename_action.setCheckable(True)
        self.sort_filename_action.setActionGroup(self.sort_action_group)
        self.sort_filename_action.triggered.connect(
            lambda: self._on_sort_changed("file_name")
        )
        sort_menu.addAction(self.sort_filename_action)

        # Date Added sort
        self.sort_date_added_action = QAction("Date Added", self)
        self.sort_date_added_action.setCheckable(True)
        self.sort_date_added_action.setActionGroup(self.sort_action_group)
        self.sort_date_added_action.triggered.connect(
            lambda: self._on_sort_changed("date_added")
        )
        sort_menu.addAction(self.sort_date_added_action)

        # Date Modified sort
        self.sort_date_modified_action = QAction("Date Modified", self)
        self.sort_date_modified_action.setCheckable(True)
        self.sort_date_modified_action.setActionGroup(self.sort_action_group)
        self.sort_date_modified_action.triggered.connect(
            lambda: self._on_sort_changed("date_modified")
        )
        sort_menu.addAction(self.sort_date_modified_action)

        # Set initial checked state based on config
        if self.current_sort_order == "file_name":
            self.sort_filename_action.setChecked(True)
        elif self.current_sort_order == "date_added":
            self.sort_date_added_action.setChecked(True)
        elif self.current_sort_order == "date_modified":
            self.sort_date_modified_action.setChecked(True)
        else:
            # Default to file name if unknown sort order
            self.sort_filename_action.setChecked(True)
            self.current_sort_order = "file_name"

        # Tools menu
        tools_menu = menubar.addMenu("Tools")

        # Scan action
        scan_action = QAction("Scan...", self)
        scan_action.setShortcut(QKeySequence("Ctrl+S"))
        scan_action.triggered.connect(self._scan_directories)
        tools_menu.addAction(scan_action)

        # Find Duplicates action
        find_duplicates_action = QAction("Find Duplicates...", self)
        find_duplicates_action.setShortcut(QKeySequence("Ctrl+Shift+D"))
        find_duplicates_action.triggered.connect(self._open_duplicate_finder)
        tools_menu.addAction(find_duplicates_action)

        # Similarity Settings action
        similarity_settings_action = QAction("Similarity Settings...", self)
        similarity_settings_action.triggered.connect(self._open_similarity_settings)
        tools_menu.addAction(similarity_settings_action)

        # Separator
        tools_menu.addSeparator()

        # Themes submenu
        themes_menu = tools_menu.addMenu("Themes")

        # Create action group for exclusive theme selection
        self.theme_action_group = QActionGroup(self)

        # Add theme actions
        for theme in self.available_themes:
            theme_action = QAction(theme, self)
            theme_action.setCheckable(True)
            theme_action.setActionGroup(self.theme_action_group)
            theme_action.triggered.connect(
                lambda checked, t=theme: self.on_theme_changed(t)
            )
            themes_menu.addAction(theme_action)

            # Check current theme
            if theme == self.current_theme:
                theme_action.setChecked(True)

    def _create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Add flexible spacer to center the upscaling indicator
        left_spacer = QWidget()
        left_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(left_spacer)

        # Add upscaling progress indicator (initially hidden)
        self._create_upscaling_indicator(toolbar)

        # Add another spacer for centering
        right_spacer = QWidget()
        right_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(right_spacer)

        # # Create theme selector and add to right side of toolbar
        # self._add_theme_selector(toolbar)

    def _add_theme_selector(self, toolbar):
        """Add theme selector dropdown to the toolbar."""
        # No need for spacer here since we already have spacers in the main toolbar

        # Theme label - no custom styling, use theme
        theme_label = QLabel("Theme:")
        toolbar.addWidget(theme_label)

        # Theme selector dropdown
        self.theme_selector = QComboBox()
        self.theme_selector.setMinimumWidth(200)
        self.theme_selector.addItems(self.available_themes)

        # Set current theme in dropdown
        if self.current_theme and self.current_theme in self.available_themes:
            self.theme_selector.setCurrentText(self.current_theme)

        # Connect change signal
        self.theme_selector.currentTextChanged.connect(self.on_theme_changed)
        toolbar.addWidget(self.theme_selector)

        # Add some padding on the right
        padding = QWidget()
        padding.setFixedWidth(10)
        toolbar.addWidget(padding)

    def _create_upscaling_indicator(self, toolbar):
        """Create the upscaling progress indicator widget."""
        self.upscaling_widget = QLabel("")  # Start with empty text
        # Use default styling - no custom stylesheet
        self.upscaling_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upscaling_widget.setVisible(True)  # Always visible

        # Set size policy and constraints - wider to fit progress text
        self.upscaling_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.upscaling_widget.setFixedSize(400, 24)  # Wider to fit progress text

        # Add click handler
        self.upscaling_widget.mousePressEvent = lambda event: self._show_upscale_queue()

        # Add to toolbar
        toolbar.addWidget(self.upscaling_widget)

        # Create animation timer (stopped initially)
        self.spinner_timer = QTimer()
        self.spinner_timer.timeout.connect(self._animate_spinner)
        self.spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_frame_index = 0

    def _animate_spinner(self):
        """Animate the spinner icon."""
        if hasattr(self, "upscaling_widget") and self.upscaling_widget:
            self.spinner_frame_index = (self.spinner_frame_index + 1) % len(
                self.spinner_frames
            )

            # Get current queue status
            if hasattr(self, "upscale_queue"):
                try:
                    tasks = self.upscale_queue.get_all_tasks()
                    # Safely get status value
                    processing = [
                        t
                        for t in tasks
                        if (
                            t.status.value
                            if hasattr(t.status, "value")
                            else str(t.status)
                        )
                        == "processing"
                    ]
                    pending = [
                        t
                        for t in tasks
                        if (
                            t.status.value
                            if hasattr(t.status, "value")
                            else str(t.status)
                        )
                        == "pending"
                    ]
                    completed = [
                        t
                        for t in tasks
                        if (
                            t.status.value
                            if hasattr(t.status, "value")
                            else str(t.status)
                        )
                        == "completed"
                    ]

                    total = len(processing) + len(pending)
                    current = len(completed) + 1 if processing else len(completed)

                    if processing and processing[0].progress is not None:
                        percent = int(processing[0].progress)
                    else:
                        percent = 0

                    # Always show text when spinner is active
                    spinner = self.spinner_frames[self.spinner_frame_index]
                    if total > 0:
                        self.upscaling_widget.setText(
                            f"{spinner} Upscaling... Processing: {len(processing)} | Pending: {len(pending)} | Completed: {len(completed)}"
                        )
                    else:
                        # Fallback when total is 0 but spinner is active
                        self.upscaling_widget.setText(f"{spinner} Upscaling...")
                except Exception as e:
                    # Fallback on error
                    self.upscaling_widget.setText(
                        f"{self.spinner_frames[self.spinner_frame_index]} Upscaling..."
                    )
            else:
                self.upscaling_widget.setText(
                    f"{self.spinner_frames[self.spinner_frame_index]} Processing..."
                )

    def _show_spinner(self):
        """Show the upscaling progress indicator."""
        if hasattr(self, "upscaling_widget") and self.upscaling_widget:
            # Set initial text with progress info
            if hasattr(self, "upscale_queue"):
                tasks = self.upscale_queue.get_all_tasks()
                processing = [t for t in tasks if t.status.value == "processing"]
                pending = [t for t in tasks if t.status.value == "pending"]
                completed = [t for t in tasks if t.status.value == "completed"]

                total = len(processing) + len(pending)
                current = len(completed) + 1 if processing else len(completed)

                spinner = self.spinner_frames[self.spinner_frame_index]
                self.upscaling_widget.setText(
                    f"{spinner} Upscaling in progress... Processing: {len(processing)} Pending: {len(pending)} Completed: {len(completed)}"
                )
            else:
                self.upscaling_widget.setText(
                    self.spinner_frames[self.spinner_frame_index]
                )

            if not self.spinner_timer.isActive():
                self.spinner_timer.start(100)

    def _hide_spinner(self):
        """Hide the upscaling progress indicator."""
        self.spinner_timer.stop()

        if hasattr(self, "upscaling_widget") and self.upscaling_widget:
            self.upscaling_widget.setText("")  # Clear the text instead of hiding

    def _show_paused_message(self):
        """Show the paused message when queue is paused with no active tasks."""
        self.spinner_timer.stop()

        if hasattr(self, "upscaling_widget") and self.upscaling_widget:
            self.upscaling_widget.setText(
                "-- Upscaling Paused. Click here to view queue. --"
            )

    def _on_task_added(self, task):
        """Handle when a task is added to the queue."""
        self._show_spinner()
        # Start processing immediately if no tasks are currently running
        self.upscale_queue.start_processing()

    def _on_task_updated(self, task):
        """Handle when a task is updated."""
        # Check if we need to hide spinner based on current queue state
        self._update_spinner_visibility()

        # If this task completed successfully, update the database with new technical metadata
        if task.status.value == "completed":
            # Guard against duplicate signals - only process each completed task once
            if task.id in self._processed_upscale_tasks:
                return
            self._processed_upscale_tasks.add(task.id)
            self._update_upscaled_media(task)

    def _update_upscaled_media(self, task):
        """
        Update the database after a file has been upscaled.
        Updates only file information metadata (dimensions, file size, timestamps, video properties)
        while preserving all AI generation metadata (prompts, models, seeds, etc.) from the database.
        Does NOT attempt to extract AI metadata from the upscaled file.
        """
        try:
            from PIL import Image
            from datetime import datetime
            import cv2

            file_path = Path(task.file_path)

            if not file_path.exists():
                self.logger.warning(f"Upscaled file not found: {file_path}")
                return

            # Get file timestamps
            file_stat = file_path.stat()
            file_size = file_stat.st_size
            modified_at = datetime.fromtimestamp(file_stat.st_mtime)
            created_at = datetime.fromtimestamp(file_stat.st_ctime)

            # Initialize video-specific properties
            frame_rate = None
            duration = None

            # Get dimensions and video-specific properties
            if task.file_type == "video":
                # For videos, use cv2 to get dimensions and video properties
                cap = cv2.VideoCapture(str(file_path))
                if cap.isOpened():
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

                    # Get video-specific properties
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

                    if fps > 0:
                        frame_rate = fps
                        if frame_count > 0:
                            duration = frame_count / fps

                    cap.release()

                    self.logger.debug(
                        f"Video info: {width}x{height}, {frame_rate:.2f} fps, "
                        f"{duration:.2f}s ({frame_count} frames)"
                    )
                else:
                    self.logger.warning(f"Could not open video: {file_path}")
                    # Can't get video info - skip update, data in DB is still valid
                    return
            else:
                # For images, use PIL to get dimensions
                with Image.open(file_path) as img:
                    width, height = img.size

            # Update database with new file information (preserves AI metadata from database)
            success = self.db_manager.update_media_technical_metadata(
                file_path=file_path,
                width=width,
                height=height,
                file_size=file_size,
                modified_at=modified_at,
                created_at=created_at,
                frame_rate=frame_rate,
                duration=duration,
            )

            if success:
                self.logger.info(
                    f"Updated file information for upscaled {task.file_type}: {file_path.name}"
                )
                # Update in-memory list instead of full reload
                self._update_single_media_item(file_path)
            else:
                self.logger.warning(
                    f"Failed to update database for {file_path.name}, rescanning..."
                )
                # Fallback to full rescan if update failed
                self._update_single_media_item(file_path)

        except Exception as e:
            self.logger.error(f"Error updating upscaled media: {e}")
            import traceback

            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            # Fallback to full rescan on error - only if we really need it
            self._update_single_media_item(file_path)

    def _update_single_media_item(self, file_path: Path) -> None:
        """
        Update a single media item in the in-memory list without reloading everything.
        This is much faster than load_all_media() for single-item updates.
        """
        try:
            # Get the updated media from database
            updated_media = self.db_manager.get_media_with_details(file_path)
            if not updated_media:
                self.logger.warning(f"Could not get updated media for {file_path}")
                return

            # Find and update in all_media list
            file_path_str = str(file_path)
            updated = False
            for i, media in enumerate(self.all_media):
                if str(media.file_path) == file_path_str:
                    self.all_media[i] = updated_media
                    updated = True
                    break

            if not updated:
                self.logger.debug(f"Media not found in list, adding: {file_path}")
                self.all_media.append(updated_media)

            # Update thumbnail view's internal lists
            self._update_thumbnail_view_item(file_path_str, updated_media)

            # Note: Thumbnail cache uses file mtime/size in hash, so it will
            # automatically regenerate the thumbnail when the file is accessed

            self.logger.debug(f"Updated single media item: {file_path.name}")

        except Exception as e:
            self.logger.error(f"Error updating single media item: {e}")
            # Don't fall back to full reload - the data is already in DB

    def _update_thumbnail_view_item(self, file_path_str: str, updated_media) -> None:
        """Update a single item in the thumbnail view's internal lists."""
        try:
            # Update in thumbnail view's media_list
            scroll_area = self.thumbnail_view.scroll_area
            for i, media in enumerate(scroll_area.media_list):
                if str(media.file_path) == file_path_str:
                    scroll_area.media_list[i] = updated_media
                    break

            # Update in filtered_media if present
            for i, media in enumerate(scroll_area.filtered_media):
                if str(media.file_path) == file_path_str:
                    scroll_area.filtered_media[i] = updated_media
                    break

        except Exception as e:
            self.logger.error(f"Error updating thumbnail view item: {e}")

    def _on_task_removed(self, task_id):
        """Handle when a task is removed from the queue."""
        self._update_spinner_visibility()

    def _update_spinner_visibility(self):
        """Update spinner visibility based on current queue state."""
        all_tasks = self.upscale_queue.get_all_tasks()

        # Check for different task states
        has_processing = any(task.status.value == "processing" for task in all_tasks)
        has_pending = any(task.status.value == "pending" for task in all_tasks)
        has_paused = any(task.status.value == "paused" for task in all_tasks)

        # Show spinner if there are processing or pending tasks
        if has_processing or has_pending:
            self._show_spinner()
        # Show paused message if queue is paused and nothing is processing
        elif has_paused and not has_processing:
            self._show_paused_message()
        else:
            self._hide_spinner()

    def _poll_queue_updates(self):
        """Poll for queue updates (called by timer)."""
        try:
            # Tell the queue to poll for process updates
            self.upscale_queue.poll_updates()
            # Update spinner visibility
            self._update_spinner_visibility()
        except Exception as e:
            # Don't let polling errors crash the app
            print(f"Error during queue polling: {e}")

    def load_config(self):
        """Load configuration from file."""
        try:
            # If config.json doesn't exist, copy from config_example.json
            if not os.path.exists(self.config_file):
                config_example_file = (
                    Path(self.config_file).parent / "config_example.json"
                )
                if config_example_file.exists():
                    print(f"Creating config.json from config_example.json")
                    shutil.copy(str(config_example_file), self.config_file)
                else:
                    print(f"Warning: Neither config.json nor config_example.json found")
                    return {}

            # Load the config file
            with open(self.config_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
        return {}  # Return empty config if file doesn't exist or error occurs

    def load_and_apply_theme(self):
        """Load theme from config and apply it."""
        try:
            # Get theme from already loaded config
            self.current_theme = self.config.get("theme", "dark_teal.xml")

            # Apply the theme
            if self.current_theme in self.available_themes:
                app = QApplication.instance()
                if app:
                    apply_stylesheet(app, theme=self.current_theme)
        except Exception as e:
            print(f"Error loading theme: {e}")
            # Fall back to default theme
            self.current_theme = "dark_teal.xml"
            app = QApplication.instance()
            if app:
                apply_stylesheet(app, theme=self.current_theme)

    def on_theme_changed(self, theme_name):
        """Handle theme selection change."""
        if theme_name and theme_name != self.current_theme:
            self.current_theme = theme_name

            # Apply the new theme
            app = QApplication.instance()
            if app:
                apply_stylesheet(app, theme=theme_name)

            # Save to config
            self.save_theme_to_config()

    def save_theme_to_config(self):
        """Save current theme selection to config file."""
        try:
            # Load existing config or create new one
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)

            # Update theme
            config["theme"] = self.current_theme

            # Save config
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving theme to config: {e}")

    def save_window_geometry(self):
        """Save window geometry to config file."""
        try:
            # Load existing config or create new one
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)

            # Update window geometry
            geometry = self.geometry()
            config["window_geometry"] = {
                "x": geometry.x(),
                "y": geometry.y(),
                "width": geometry.width(),
                "height": geometry.height(),
            }

            # Save config
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving window geometry to config: {e}")

    def save_splitter_sizes(self):
        """Save splitter sizes to config file."""
        try:
            # Load existing config or create new one
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)

            # Update splitter sizes
            config["splitter_sizes"] = self.main_splitter.sizes()

            # Save config
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving splitter sizes to config: {e}")

    def _on_sort_changed(self, sort_order: str):
        """Handle sort order change from menu."""
        if sort_order != self.current_sort_order:
            self.current_sort_order = sort_order
            self._save_sort_order_to_config()
            self._apply_sorting()

    def _save_sort_order_to_config(self):
        """Save sort order to config file."""
        try:
            # Load existing config or create new one
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)

            # Update sort order
            config["sort_order"] = self.current_sort_order

            # Save config
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving sort order to config: {e}")

    def _apply_sorting(self):
        """Apply current sorting to media list and refresh view."""
        if not self.all_media:
            return

        # Sort the media list based on current sort order
        if self.current_sort_order == "file_name":
            self.all_media.sort(key=lambda m: m.file_name.lower())
        elif self.current_sort_order == "date_added":
            # Use created_at for date added
            self.all_media.sort(key=lambda m: m.created_at)
        elif self.current_sort_order == "date_modified":
            self.all_media.sort(key=lambda m: m.modified_at)

        # Update the thumbnail view with sorted media
        self.thumbnail_view.set_media_list(self.all_media)

        # Reapply current filters to maintain filtered state
        self.apply_all_filters()

    def resizeEvent(self, event):
        """Handle window resize events."""
        super().resizeEvent(event)
        # Start/restart the timer to debounce saves
        self.geometry_save_timer.stop()
        self.geometry_save_timer.start(500)  # Save after 500ms of no resize activity

    def moveEvent(self, event):
        """Handle window move events."""
        super().moveEvent(event)
        # Start/restart the timer to debounce saves
        self.geometry_save_timer.stop()
        self.geometry_save_timer.start(500)  # Save after 500ms of no move activity

    def _on_content_search(self, query):
        """Handle CLIP-based content search."""
        from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager
        from metascan.utils.app_paths import get_data_dir

        index_dir = get_data_dir() / "similarity"
        faiss_mgr = FaissIndexManager(index_dir)

        if not faiss_mgr.load():
            QMessageBox.information(
                self,
                "No Index",
                "No embedding index found. Please build the similarity index first\n"
                "via Tools > Similarity Settings.",
            )
            return

        # Load config for model selection
        config = self._load_similarity_config()
        model_key = config.get("clip_model", "small")
        device = config.get("device", "auto")

        embedding_mgr = EmbeddingManager(model_key=model_key, device=device)
        text_embedding = embedding_mgr.compute_text_embedding(query)

        if text_embedding is None:
            QMessageBox.warning(self, "Error", "Failed to compute text embedding.")
            return

        top_k = config.get("search_results_count", 100)
        results = faiss_mgr.search(text_embedding, top_k=top_k)

        if not results:
            self.statusBar().showMessage("No content search results found.")
            return

        # Filter the view to show only matching files
        matching_paths = {r[0] for r in results}
        # Create a sorted list based on similarity score
        score_map = {r[0]: r[1] for r in results}

        filtered_media = [
            m for m in self.all_media if str(m.file_path) in matching_paths
        ]
        # Sort by similarity score (highest first)
        filtered_media.sort(
            key=lambda m: score_map.get(str(m.file_path), 0), reverse=True
        )

        self.thumbnail_view.set_media_list(filtered_media)
        self.statusBar().showMessage(
            f"Content search: {len(filtered_media)} results for '{query}'"
        )

        # Unload model after search to free memory
        embedding_mgr.unload_model()

    def _on_find_similar(self, media):
        """Find media similar to the selected item using CLIP embeddings."""
        from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager
        from metascan.utils.app_paths import get_data_dir

        index_dir = get_data_dir() / "similarity"
        faiss_mgr = FaissIndexManager(index_dir)

        if not faiss_mgr.load():
            QMessageBox.information(
                self,
                "No Index",
                "No embedding index found. Please build the similarity index first\n"
                "via Tools > Similarity Settings.",
            )
            return

        file_path_str = str(media.file_path)

        # Try to get embedding from the index first
        embedding = faiss_mgr.get_embedding(file_path_str)

        if embedding is None:
            # Compute on-the-fly
            config = self._load_similarity_config()
            model_key = config.get("clip_model", "small")
            device = config.get("device", "auto")
            embedding_mgr = EmbeddingManager(model_key=model_key, device=device)

            if media.is_video:
                embedding = embedding_mgr.compute_video_embedding(file_path_str)
            else:
                embedding = embedding_mgr.compute_image_embedding(file_path_str)

            embedding_mgr.unload_model()

            if embedding is None:
                QMessageBox.warning(
                    self, "Error", "Failed to compute embedding for this file."
                )
                return

        config = self._load_similarity_config()
        top_k = config.get("search_results_count", 50)
        results = faiss_mgr.search(embedding, top_k=top_k)

        if not results:
            self.statusBar().showMessage("No similar items found.")
            return

        # Filter the view to show matching files
        score_map = {r[0]: r[1] for r in results}
        matching_paths = set(score_map.keys())

        filtered_media = [
            m for m in self.all_media if str(m.file_path) in matching_paths
        ]
        filtered_media.sort(
            key=lambda m: score_map.get(str(m.file_path), 0), reverse=True
        )

        self.thumbnail_view.set_media_list(filtered_media)
        self.statusBar().showMessage(
            f"Showing {len(filtered_media)} items similar to '{media.file_name}'"
        )

    def _on_content_search_cleared(self):
        """Clear content search and restore normal view."""
        self.thumbnail_view.set_media_list(self.all_media)
        self.statusBar().showMessage("")

    def _load_similarity_config(self):
        """Load similarity config from config file."""
        try:
            import json

            with open(self.config_file, "r") as f:
                config = json.load(f)
            return config.get("similarity", {})
        except Exception:
            return {}

    def _invalidate_similarity_cache(self):
        """Clear cached FAISS index and similarity config.

        Called after the similarity index is rebuilt or the CLIP model is changed.
        """
        self._faiss_mgr = None
        self._similarity_config = None

    def _open_similarity_settings(self):
        dialog = SimilaritySettingsDialog(
            self.db_manager, self.embedding_queue, parent=self
        )
        dialog.show()

    def _open_duplicate_finder(self):
        dialog = DuplicateFinderDialog(self.db_manager, parent=self)
        dialog.delete_requested.connect(self._handle_duplicate_delete)
        dialog.show()

    def _handle_duplicate_delete(self, paths):
        """Handle deletion of duplicate files from the duplicate finder."""
        from send2trash import send2trash

        deleted = 0
        failed = 0
        path_objects = []

        for fp in paths:
            try:
                # Remove cached thumbnail
                if self.thumbnail_cache:
                    thumb_path = self.thumbnail_cache.get_thumbnail_path(Path(fp))
                    if thumb_path and thumb_path.exists():
                        try:
                            thumb_path.unlink()
                        except Exception as e:
                            self.logger.debug(
                                f"Failed to remove thumbnail for {fp}: {e}"
                            )

                # Move media file to trash
                send2trash(fp)
                path_objects.append(Path(fp))
                deleted += 1
            except Exception as e:
                self.logger.error(f"Failed to delete {fp}: {e}")
                failed += 1

        # Batch delete from database
        if path_objects:
            self.db_manager.delete_media_batch(path_objects)

        if deleted:
            self.logger.info(
                f"Duplicate delete: {deleted} files trashed"
                + (f", {failed} failed" if failed else "")
            )
            self.refresh_view()

    def _open_config(self):
        dialog = ConfigDialog(self)
        if dialog.exec():
            # Configuration saved
            pass

    def _scan_directories(self):
        # Load configuration
        if not os.path.exists(self.config_file):
            print("No configuration found. Please configure directories first.")
            self._open_config()
            return

        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)
                directories = config.get("directories", [])

            if not directories:
                print("No directories configured. Please configure directories first.")
                self._open_config()
                return

            # Convert paths from POSIX storage format to native format
            for dir_info in directories:
                dir_info["filepath"] = to_native_path(dir_info["filepath"])

            # Store directories for later use
            self._scan_directories_list = directories

            # Create unified progress dialog (starts in PREPARATION phase)
            self.progress_dialog = ScanProgressDialog(self)
            self.progress_dialog.scan_confirmed.connect(self._on_scan_confirmed)

            # Start preparation thread to count files in background
            self._preparation_thread = ScanPreparationThread(
                directories, self.db_manager
            )
            self._preparation_thread.preparation_complete.connect(
                self._on_scan_preparation_complete
            )
            self._preparation_thread.start()

            # Show dialog — stays open across all phases
            self.progress_dialog.exec()

        except Exception as e:
            print(f"Error during scanning: {e}")
            QMessageBox.critical(self, "Scan Error", f"Failed to start scanning: {e}")

    def _on_scan_preparation_complete(
        self, total_dirs: int, total_files: int, unprocessed_files: int
    ):
        """Preparation done — transition dialog to confirmation phase."""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.show_confirmation(
                total_dirs, total_files, unprocessed_files
            )

    def _on_scan_confirmed(self, full_clean: bool):
        """User confirmed scan — start scanning phase."""
        if full_clean:
            print("Full clean and scan requested")
            saved_favorites = self._save_favorites_before_cleanup()
            self._perform_full_cleanup()
            self._favorites_to_restore = saved_favorites
        else:
            self._favorites_to_restore = {}

        self.progress_dialog.set_phase(ScanPhase.SCANNING)

        # Create and configure scanner thread
        self.scanner_thread = ScannerThread(
            self.scanner,
            self._scan_directories_list,
            full_scan=full_clean,
        )
        self.scanner_thread.progress_updated.connect(self._on_scan_file_progress)
        self.scanner_thread.directory_progress_updated.connect(
            self._on_scan_directory_progress
        )
        self.scanner_thread.stale_cleanup_started.connect(
            self._on_stale_cleanup_started
        )
        self.scanner_thread.scan_complete.connect(self._on_scan_complete)
        self.scanner_thread.scan_error.connect(self._on_scan_error)

        # Start scanning
        self.scanner_thread.start()

    def _on_scan_file_progress(self, current, total, file_path):
        """Handle file scan progress updates."""
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.update_file_progress(current, total, file_path)

            # Check if cancellation was requested
            if self.progress_dialog.cancel_requested:
                self.scanner_thread.cancel()

    def _on_scan_directory_progress(self, current_dir, total_dirs, dir_path):
        """Handle directory scan progress updates."""
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.update_directory_progress(
                current_dir, total_dirs, dir_path
            )

    def _on_stale_cleanup_started(self):
        """Scanner entering stale cleanup sub-phase."""
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.enter_stale_cleanup()

    def _on_scan_complete(self, processed_count, stale_count=0):
        """Handle scan completion."""
        print(
            f"Scanning completed. Processed {processed_count} files, removed {stale_count} stale entries."
        )
        self._scan_processed_count = processed_count
        self._scan_stale_count = stale_count

        # Restore favorites if we had saved them before a full clean
        if hasattr(self, "_favorites_to_restore") and self._favorites_to_restore:
            self._restore_favorites_after_scan()
            self._favorites_to_restore = {}

        # Refresh filters after scanning
        self.refresh_filters()

        # Reload media after scanning
        self.load_all_media()

        # Try to transition to embedding phase instead of closing
        if self._auto_trigger_embeddings():
            return  # Dialog stays open for embedding progress

        # No embeddings needed — show completion in dialog
        self._show_scan_completion()

    def _auto_trigger_embeddings(self) -> bool:
        """Auto-start CLIP embedding computation for unembedded files after scan.

        Returns True if embeddings were started (dialog stays open), False otherwise.
        """
        try:
            if self.embedding_queue.is_indexing():
                return False

            config_path = get_config_path()
            if not config_path.exists():
                return False
            with open(config_path, "r") as f:
                full_config = json.load(f)
            sim_config = full_config.get("similarity", {})
            if not sim_config:
                return False

            unembedded = self.db_manager.get_unembedded_file_paths()
            if not unembedded:
                return False

            model_key = sim_config.get("clip_model", "small")
            device = sim_config.get("device", "auto")
            db_path = str(self.db_manager.db_path)
            video_keyframes = sim_config.get("video_keyframes", 4)

            # Transition dialog to embedding phase
            if hasattr(self, "progress_dialog"):
                self.progress_dialog.set_phase(ScanPhase.EMBEDDING)

            # Connect embedding signals to dialog and completion handler
            self.embedding_queue.progress_updated.connect(self._on_embedding_progress)
            self.embedding_queue.indexing_complete.connect(self._on_embedding_complete)
            self.embedding_queue.indexing_error.connect(self._on_embedding_error)

            self.logger.info(
                f"Auto-triggering CLIP embeddings for {len(unembedded)} files"
            )
            self.embedding_queue.start_indexing(
                file_paths=unembedded,
                clip_model_key=model_key,
                device=device,
                db_path=db_path,
                compute_phash=False,
                video_keyframes=video_keyframes,
            )
            return True
        except Exception as e:
            self.logger.warning(f"Failed to auto-trigger embeddings: {e}")
            return False

    def _on_embedding_progress(self, current: int, total: int, status_text: str):
        """Update progress dialog with embedding progress."""
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.update_embedding_progress(current, total, status_text)
            # Check if user cancelled via dialog
            if self.progress_dialog.cancel_requested:
                self.embedding_queue.cancel_indexing()

    def _on_embedding_complete(self, total: int):
        """Handle embedding completion — show summary in dialog."""
        self._disconnect_embedding_signals()
        self._show_scan_completion(embedding_count=total)

    def _on_embedding_error(self, error: str):
        """Handle embedding error — show error in completion summary."""
        self._disconnect_embedding_signals()
        self._show_scan_completion(embedding_error=error)

    def _show_scan_completion(
        self, embedding_count: int = 0, embedding_error: str = ""
    ):
        """Build summary and transition dialog to COMPLETE phase."""
        scan_count = getattr(self, "_scan_processed_count", 0)
        stale_count = getattr(self, "_scan_stale_count", 0)

        lines = [f"Successfully processed {scan_count} media files."]
        if stale_count > 0:
            lines.append(
                f"Removed {stale_count} stale entries (files no longer on disk)."
            )
        if embedding_count > 0:
            lines.append(f"CLIP embeddings computed for {embedding_count} files.")
        if embedding_error:
            lines.append(f"\nCLIP embedding failed: {embedding_error}")
            lines.append("Check logs/embedding_worker.log for details.")

        # Mark embedding step as skipped if it was never started
        if embedding_count == 0 and not embedding_error:
            if hasattr(self, "progress_dialog"):
                self.progress_dialog.skip_embedding_step()

        if hasattr(self, "progress_dialog"):
            self.progress_dialog.show_completion("\n".join(lines))

    def _disconnect_embedding_signals(self):
        """Safely disconnect embedding queue signals from scan handlers."""
        try:
            self.embedding_queue.progress_updated.disconnect(
                self._on_embedding_progress
            )
            self.embedding_queue.indexing_complete.disconnect(
                self._on_embedding_complete
            )
            self.embedding_queue.indexing_error.disconnect(self._on_embedding_error)
        except TypeError:
            pass  # Already disconnected

    def _on_scan_error(self, error_message):
        """Handle scan errors."""
        print(f"Scan error: {error_message}")

        # Show error in the dialog completion page
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.skip_embedding_step()
            self.progress_dialog.show_completion(
                f"An error occurred during scanning:\n{error_message}"
            )

    def load_all_media(self):
        """Load all media from database."""
        try:
            # Use optimized single-query method that fetches data, is_favorite,
            # and playback_speed together (eliminates N+1 query problem)
            self.all_media = self.db_manager.get_all_media_with_details()
            # Apply current sorting
            self._apply_sorting()
            print(f"Loaded {len(self.all_media)} media items")
        except Exception as e:
            print(f"Error loading media: {e}")

    def refresh_view(self):
        """Refresh the entire view - reload media and filters."""
        try:
            # Reload all media from database
            self.load_all_media()
            # Refresh filters to ensure they're up to date
            self.refresh_filters()
            self.logger.info("View refreshed successfully")
        except Exception as e:
            self.logger.error(f"Error refreshing view: {e}")
            self.all_media = []

    def refresh_filters(self):
        """Refresh the filters panel with current database data."""
        try:
            sort_order = self.filters_panel.get_sort_order()
            filter_data = self.db_manager.get_filter_data(sort_order)
            self.filters_panel.update_filters(filter_data)

            # Update path filter tree with indexed paths
            indexed_paths = self.db_manager.get_existing_file_paths()
            self.filters_panel.update_path_filter_data(indexed_paths)
        except Exception as e:
            print(f"Error refreshing filters: {e}")

    def on_sort_order_changed(self, sort_order: str):
        """Handle when filter sort order is changed (for filter panel sorting)."""
        self.refresh_filters()

    def on_filters_changed(self, filters: dict):
        """Handle when filter selections change."""
        self.current_filters = filters
        self.apply_all_filters()

    def on_favorites_toggled(self, is_active: bool):
        """Handle when favorites filter is toggled."""
        self.favorites_active = is_active
        self.apply_all_filters()

    def on_favorite_toggled(self, media):
        """Handle when a media item's favorite status is toggled."""
        try:
            # Update database
            success = self.db_manager.set_favorite(media.file_path, media.is_favorite)
            if success:
                # If favorites filter is active, reapply filters
                if self.favorites_active:
                    self.apply_all_filters()
            else:
                # Revert the change in the UI
                media.is_favorite = not media.is_favorite
                widget = self.thumbnail_view.thumbnail_widgets.get(str(media.file_path))
                if widget:
                    widget.set_favorite(media.is_favorite)
        except Exception as e:
            print(f"Error toggling favorite: {e}")

    def apply_all_filters(self):
        """Apply both regular filters and favorites filter."""
        try:
            # Start with all media paths or filtered paths
            if self.current_filters:
                # Get media matching current filters
                filtered_paths = self.db_manager.get_filtered_media_paths(
                    self.current_filters
                )
                print(f"Filters applied: {self.current_filters}")
                print(f"Found {len(filtered_paths)} matching media files")

                # Apply favorites filter if active
                if self.favorites_active:
                    favorite_paths = self.db_manager.get_favorite_media_paths()
                    # Intersect with current filtered paths
                    self.filtered_media_paths = filtered_paths & favorite_paths
                    print(
                        f"Favorites filter applied: {len(self.filtered_media_paths)} items after favorites filter"
                    )
                else:
                    self.filtered_media_paths = filtered_paths
            elif self.favorites_active:
                # Only favorites filter is active
                self.filtered_media_paths = self.db_manager.get_favorite_media_paths()
                print(
                    f"Only favorites filter active: {len(self.filtered_media_paths)} favorite items"
                )
            else:
                # No filters at all - pass None to show all
                self.filtered_media_paths = None
                print("No filters applied - showing all media")

            # Update thumbnail view
            self.thumbnail_view.apply_filters(self.filtered_media_paths)

            # Update Open action states after filter changes
            self._update_open_actions_state()
        except Exception as e:
            print(f"Error applying filters: {e}")

    def on_thumbnail_selected(self, media):
        """Handle when a thumbnail is selected."""
        try:
            # Update metadata panel - None will clear it
            if media:
                self.metadata_panel.display_metadata(media)
            else:
                self.metadata_panel.clear_content()

            # Update Open action states
            self._update_open_actions_state()
        except Exception as e:
            print(f"Error handling thumbnail selection: {e}")

    def on_multi_selection_changed(self, count: int):
        """Handle when the number of selected items changes in multi-select mode."""
        # Update delete menu item text based on selection count
        if count > 1:
            self.delete_action.setText("Delete files...")
        else:
            self.delete_action.setText("Delete file...")

        # Update Open action states (disabled in multi-select mode)
        self._update_open_actions_state()

    def on_thumbnail_double_clicked(self, media):
        """Handle when a thumbnail is double-clicked."""
        try:
            # Get the currently filtered media list
            if self.filtered_media_paths:
                # Use filtered list
                filtered_media = [
                    m
                    for m in self.all_media
                    if str(m.file_path) in self.filtered_media_paths
                ]
            else:
                # Use all media
                filtered_media = self.all_media

            # Set media list and show the viewer
            self.media_viewer.set_media_list(filtered_media, media)

            # Position the viewer to exactly overlay the main window
            main_geometry = self.geometry()
            self.media_viewer.move(main_geometry.topLeft())
            self.media_viewer.resize(main_geometry.size())

            # Show the media
            self.media_viewer.show_media(media)
            self.media_viewer.show()
            self.media_viewer.raise_()
            self.media_viewer.activateWindow()

        except Exception as e:
            print(f"Error opening media viewer: {e}")

    def on_media_viewer_closed(self):
        """Handle when media viewer is closed."""
        # Return focus to main window
        self.activateWindow()
        self.thumbnail_view.setFocus()
        # Do not refresh filters here - they should maintain their state

    def show_slideshow(self):
        """Show the slideshow viewer with currently filtered media."""
        try:
            # Get currently filtered media
            if self.filtered_media_paths:
                filtered_media = [
                    m
                    for m in self.all_media
                    if str(m.file_path) in self.filtered_media_paths
                ]
            else:
                filtered_media = self.all_media

            if not filtered_media:
                QMessageBox.information(
                    self,
                    "No Media",
                    "No media files to display in slideshow.",
                )
                return

            # Get currently selected media (if any)
            current_media = None
            if hasattr(self.thumbnail_view, "get_selected_media"):
                current_media = self.thumbnail_view.get_selected_media()

            # Set media list and show slideshow
            self.slideshow_viewer.set_media_list(filtered_media, current_media)
            self.slideshow_viewer.showFullScreen()

        except Exception as e:
            print(f"Error opening slideshow viewer: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open slideshow viewer: {e}",
            )

    def on_slideshow_closed(self):
        """Handle when slideshow viewer is closed."""
        # Return focus to main window
        self.activateWindow()
        self.thumbnail_view.setFocus()

    def on_viewer_media_changed(self, media):
        """Handle when media changes in the viewer."""
        try:
            # Update metadata panel to show current viewed media
            self.metadata_panel.display_metadata(media)
        except Exception as e:
            print(f"Error updating metadata for viewer: {e}")

    def on_thumbnail_size_changed(self, new_size: Tuple[int, int]) -> None:
        """Handle thumbnail size change from the thumbnail view."""
        try:
            # Update config
            self.config["thumbnail_size"] = list(new_size)

            # Save to file
            with open(self.config_file, "w") as f:
                import json

                json.dump(self.config, f, indent=2)

            # Update thumbnail cache to new size
            cache_dir = get_thumbnail_cache_dir()
            self.thumbnail_cache = ThumbnailCache(cache_dir, new_size)

            # Update metadata panel's thumbnail cache
            self.metadata_panel.set_thumbnail_cache(self.thumbnail_cache)

            # Recreate the thumbnail view with new size
            self._recreate_thumbnail_view(new_size)

            print(f"Thumbnail size changed to: {new_size}")

        except Exception as e:
            print(f"Error changing thumbnail size: {e}")

    # Context menu handlers
    def on_context_open_requested(self, media):
        """Handle Open request from thumbnail context menu."""
        try:
            # Use the same logic as double-click but more directly
            self.on_thumbnail_double_clicked(media)
        except Exception as e:
            self.logger.error(f"Error opening media from context menu: {e}")
            # Try alternative approach on Windows
            if platform.system() == "Windows":
                try:
                    import os
                    import subprocess

                    subprocess.run(
                        ["start", "", str(media.file_path)], shell=True, check=True
                    )
                except Exception as e2:
                    self.logger.error(f"Alternative open method also failed: {e2}")

    def on_context_open_folder_requested(self, media):
        """Handle Open Folder request from thumbnail context menu."""
        try:
            # Temporarily set selected media for existing open folder functionality
            old_selected = self.thumbnail_view.selected_media
            self.thumbnail_view.selected_media = media
            self._open_selected_folder()
            self.thumbnail_view.selected_media = old_selected
        except Exception as e:
            self.logger.error(f"Error opening folder from context menu: {e}")

    def on_context_delete_requested(self, media):
        """Handle Delete request from thumbnail context menu."""
        try:
            # Use existing delete functionality
            self._confirm_and_delete_media(media)
        except Exception as e:
            self.logger.error(f"Error deleting media from context menu: {e}")

    def on_context_upscale_requested(self, media):
        """Handle Upscale request from thumbnail context menu."""
        try:
            # Create a single-item list and use existing upscale functionality
            selected_media_list = [media]
            self._show_upscale_dialog(selected_media_list)
        except Exception as e:
            self.logger.error(f"Error starting upscale from context menu: {e}")

    def _recreate_thumbnail_view(self, new_size: Tuple[int, int]) -> None:
        """Recreate the thumbnail view with new size."""
        # Get current media list and filters
        current_media = self.all_media
        current_filters = self.current_filters
        current_filtered_paths = self.filtered_media_paths

        # Preserve selection state
        is_multi_select = self.thumbnail_view.is_multi_select_mode()
        selected_media_paths = set()
        if is_multi_select:
            # Get all selected media paths
            selected_media_list = self.thumbnail_view.get_all_selected_media()
            selected_media_paths = {str(m.file_path) for m in selected_media_list}
        else:
            # Get single selected media
            selected_media = self.thumbnail_view.get_selected_media()
            if selected_media:
                selected_media_paths.add(str(selected_media.file_path))

        # Disconnect old signals
        self.thumbnail_view.selection_changed.disconnect()
        self.thumbnail_view.favorite_toggled.disconnect()
        self.thumbnail_view.multi_selection_changed.disconnect()
        self.thumbnail_view.thumbnail_size_changed.disconnect()
        self.thumbnail_view.scroll_area.item_double_clicked.disconnect()

        # Disconnect context menu signals
        self.thumbnail_view.open_requested.disconnect()
        self.thumbnail_view.open_folder_requested.disconnect()
        self.thumbnail_view.delete_requested.disconnect()
        self.thumbnail_view.upscale_requested.disconnect()

        # Create new thumbnail view
        scroll_step = self.config.get("scroll_wheel_step", 120)
        new_thumbnail_view = VirtualThumbnailView(
            thumbnail_size=new_size, scroll_step=scroll_step
        )

        # Connect signals
        new_thumbnail_view.selection_changed.connect(self.on_thumbnail_selected)
        new_thumbnail_view.favorite_toggled.connect(self.on_favorite_toggled)
        new_thumbnail_view.multi_selection_changed.connect(
            self.on_multi_selection_changed
        )
        new_thumbnail_view.thumbnail_size_changed.connect(
            self.on_thumbnail_size_changed
        )
        new_thumbnail_view.scroll_area.item_double_clicked.connect(
            self.on_thumbnail_double_clicked
        )

        # Connect context menu signals
        new_thumbnail_view.open_requested.connect(self.on_context_open_requested)
        new_thumbnail_view.open_folder_requested.connect(
            self.on_context_open_folder_requested
        )
        new_thumbnail_view.delete_requested.connect(self.on_context_delete_requested)
        new_thumbnail_view.upscale_requested.connect(self.on_context_upscale_requested)
        new_thumbnail_view.find_similar_requested.connect(self._on_find_similar)

        # Replace in splitter using saved reference
        old_widget = self.main_splitter.widget(1)  # Middle widget (thumbnail panel)
        if old_widget is not None:
            self.main_splitter.replaceWidget(1, new_thumbnail_view)
            old_widget.deleteLater()

        # Update reference
        self.thumbnail_view = new_thumbnail_view

        # Restore media and filters
        if current_media:
            self.thumbnail_view.set_media_list(current_media)
            if current_filtered_paths:
                self.thumbnail_view.apply_filters(
                    current_filtered_paths, preserve_selection=True
                )

        # Restore selection state
        if selected_media_paths:
            # Restore multi-select mode if it was active
            if is_multi_select:
                self.thumbnail_view.select_button.setChecked(True)
                self.thumbnail_view.scroll_area.set_multi_select_mode(True)

            # Restore selections using the new method that properly updates widgets
            self.thumbnail_view.scroll_area.restore_selections(selected_media_paths)

        # Update Open action states after view recreation
        self._update_open_actions_state()

    def on_viewer_favorite_toggled(self, media, is_favorite):
        """Handle favorite toggle from media viewer."""
        import logging

        logger = logging.getLogger(__name__)

        if self.db_manager:
            success = self.db_manager.set_favorite(media.file_path, is_favorite)
            if success:
                logger.info(
                    f"Updated favorite status for {media.file_name}: {is_favorite}"
                )
                # Update the media in all_media list
                for m in self.all_media:
                    if m.file_path == media.file_path:
                        m.is_favorite = is_favorite
                        break
                # Refresh thumbnails to show updated favorite status if virtualization is enabled
                if hasattr(self, "virtual_view") and self.virtual_view:
                    if hasattr(self.virtual_view, "refresh_thumbnails"):
                        self.virtual_view.refresh_thumbnails()
            else:
                logger.error(f"Failed to update favorite status for {media.file_name}")

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts for the main window."""
        # Command-D (or Ctrl-D on non-Mac) for delete
        delete_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        delete_shortcut.activated.connect(self._handle_delete_shortcut)

    def _handle_delete_shortcut(self):
        """Handle the delete shortcut from the main window."""
        # Check if media viewer is visible
        if self.media_viewer.isVisible():
            # Delete from media viewer
            self._delete_from_viewer()
        else:
            # Check if in multi-select mode and multiple items are selected
            if self.thumbnail_view.is_multi_select_mode():
                selected_media_list = self.thumbnail_view.get_all_selected_media()
                if len(selected_media_list) > 1:
                    self._confirm_and_delete_multiple_media(selected_media_list)
                elif len(selected_media_list) == 1:
                    self._confirm_and_delete_media(
                        selected_media_list[0], from_viewer=False
                    )
            else:
                # Single selection mode
                selected_media = self.thumbnail_view.get_selected_media()
                if selected_media:
                    self._confirm_and_delete_media(selected_media, from_viewer=False)

    def _delete_from_viewer(self):
        """Handle delete from the media viewer."""
        if self.media_viewer.current_media:
            self._confirm_and_delete_media(
                self.media_viewer.current_media, from_viewer=True
            )

    def _confirm_and_delete_media(self, media, from_viewer=False):
        """Show confirmation dialog and delete media if confirmed."""
        # Create confirmation dialog
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Media")
        msg_box.setText("Delete this file?")
        msg_box.setInformativeText(f"File: {media.file_name}")
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg_box.setIcon(QMessageBox.Icon.Warning)

        # Focus on OK button
        ok_button = msg_box.button(QMessageBox.StandardButton.Ok)
        if ok_button:
            ok_button.setFocus()

        # Show dialog and handle response
        if msg_box.exec() == QMessageBox.StandardButton.Ok:
            self._delete_media(media, from_viewer)

    def _confirm_and_delete_multiple_media(self, media_list):
        """Show confirmation dialog and delete multiple media files if confirmed."""
        count = len(media_list)

        # Create confirmation dialog
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Multiple Files")
        msg_box.setText(f"Delete {count} selected files?")
        msg_box.setInformativeText("This action cannot be undone.")

        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg_box.setIcon(QMessageBox.Icon.Warning)

        # Focus on OK button
        ok_button = msg_box.button(QMessageBox.StandardButton.Ok)
        if ok_button:
            ok_button.setFocus()

        # Show dialog and handle response
        if msg_box.exec() == QMessageBox.StandardButton.Ok:
            self._delete_multiple_media(media_list)

    def _delete_media(self, media, from_viewer=False):
        """Delete media file and update all related components."""
        try:
            file_path = media.file_path

            # 0. Release video file handle if this is a video being played
            if media.is_video and self.media_viewer.video_player:
                self.media_viewer.video_player.clear_video()

            # 1. Delete from database
            db_success = self.db_manager.delete_media(file_path)
            if not db_success:
                print(f"Warning: Media not found in database: {file_path}")

            # 2. Move thumbnail to trash
            if self.thumbnail_cache:
                thumbnail_path = self.thumbnail_cache.get_thumbnail_path(file_path)
                if thumbnail_path and thumbnail_path.exists():
                    try:
                        self._move_to_trash(thumbnail_path)
                        print(f"Moved thumbnail to trash: {thumbnail_path}")
                    except Exception as e:
                        print(f"Failed to move thumbnail to trash: {e}")

            # 3. Move media file to trash
            if file_path.exists():
                try:
                    self._move_to_trash(file_path)
                    print(f"Moved media file to trash: {file_path}")
                except Exception as e:
                    print(f"Failed to move media file to trash: {e}")
                    # Show error message
                    QMessageBox.critical(
                        self, "Delete Error", f"Failed to move file to trash: {e}"
                    )
                    return

            # 4. Remove from all_media list
            self.all_media = [m for m in self.all_media if m.file_path != file_path]

            # 5. Update filters
            self.refresh_filters()

            # 6. Handle view updates based on where delete came from
            if from_viewer:
                # Update media viewer to show next/previous file
                current_index = self.media_viewer.current_index
                media_list = self.media_viewer.media_list

                # Remove from viewer's media list
                media_list = [m for m in media_list if m.file_path != file_path]
                self.media_viewer.media_list = media_list

                if not media_list:
                    # No more media, close viewer
                    self.media_viewer.close_viewer()
                elif current_index >= len(media_list):
                    # Was at the end, show previous
                    self.media_viewer.current_index = len(media_list) - 1
                    self.media_viewer._display_current_media()
                else:
                    # Show next (same index)
                    self.media_viewer.current_index = max(
                        0, min(current_index, len(media_list) - 1)
                    )
                    self.media_viewer._display_current_media()

            # 7. Update thumbnail view (remove from display)
            # First update the media list in the thumbnail view, then apply filters
            self.thumbnail_view.set_media_list(self.all_media)
            self.apply_all_filters()

            print(f"Successfully deleted: {file_path.name}")

        except Exception as e:
            print(f"Error deleting media: {e}")
            QMessageBox.critical(self, "Delete Error", f"Failed to delete media: {e}")

    def _delete_multiple_media(self, media_list):
        """Delete multiple media files and update all related components."""
        deleted_count = 0
        failed_files = []

        try:
            # 1. Batch delete from database (single transaction - much faster)
            file_paths = [media.file_path for media in media_list]
            self.db_manager.delete_media_batch(file_paths)

            # 2. Process file operations for each media
            deleted_paths = set()
            for media in media_list:
                try:
                    file_path = media.file_path

                    # Move thumbnail to trash
                    if self.thumbnail_cache:
                        thumbnail_path = self.thumbnail_cache.get_thumbnail_path(
                            file_path
                        )
                        if thumbnail_path and thumbnail_path.exists():
                            try:
                                self._move_to_trash(thumbnail_path)
                                print(f"Moved thumbnail to trash: {thumbnail_path}")
                            except Exception as e:
                                print(f"Failed to move thumbnail to trash: {e}")

                    # Move media file to trash
                    if file_path.exists():
                        try:
                            self._move_to_trash(file_path)
                            print(f"Moved media file to trash: {file_path}")
                            deleted_count += 1
                            deleted_paths.add(file_path)
                        except Exception as e:
                            print(f"Failed to move media file to trash: {e}")
                            failed_files.append(file_path.name)
                            continue
                    else:
                        # File already gone, still count as deleted from DB
                        deleted_paths.add(file_path)

                except Exception as e:
                    print(f"Error deleting {media.file_name}: {e}")
                    failed_files.append(media.file_name)

            # 3. Batch update all_media list (single list comprehension)
            self.all_media = [
                m for m in self.all_media if m.file_path not in deleted_paths
            ]

            # 5. Update filters and view once after all deletions
            self.refresh_filters()
            # First update the media list in the thumbnail view, then apply filters
            self.thumbnail_view.set_media_list(self.all_media)
            self.apply_all_filters()

            # 6. Clear multi-selection after deletion
            if self.thumbnail_view.is_multi_select_mode():
                self.thumbnail_view.scroll_area.clear_all_selections()

            # Show summary message
            if failed_files:
                QMessageBox.warning(
                    self,
                    "Partial Deletion",
                    f"Successfully deleted {deleted_count} file(s).\n\n"
                    f"Failed to delete {len(failed_files)} file(s):\n"
                    + "\n".join(failed_files[:5])
                    + (
                        f"\n... and {len(failed_files) - 5} more"
                        if len(failed_files) > 5
                        else ""
                    ),
                )
            else:
                print(f"Successfully deleted {deleted_count} files")

        except Exception as e:
            print(f"Error during batch deletion: {e}")
            QMessageBox.critical(self, "Delete Error", f"Failed to delete files: {e}")

    def _move_to_trash(self, file_path: Path):
        """Move a file to the system trash/recycle bin."""
        system = platform.system()

        if system == "Darwin":  # macOS
            # Use macOS Trash
            trash_dir = Path.home() / ".Trash"
            trash_dir.mkdir(exist_ok=True)

            # Generate unique name if file already exists in trash
            dest_path = trash_dir / file_path.name
            counter = 1
            while dest_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_path = trash_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.move(str(file_path), str(dest_path))

        elif system == "Windows":
            # Use Windows Recycle Bin via shell
            import subprocess

            # Use PowerShell to move to recycle bin
            ps_command = f'Remove-Item -Path "{file_path}" -Recurse -Force'
            subprocess.run(["powershell", "-Command", ps_command], check=True)

        elif system == "Linux":
            # Use XDG trash
            trash_dir = Path.home() / ".local" / "share" / "Trash" / "files"
            trash_dir.mkdir(parents=True, exist_ok=True)

            # Generate unique name if file already exists in trash
            dest_path = trash_dir / file_path.name
            counter = 1
            while dest_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_path = trash_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.move(str(file_path), str(dest_path))

        else:
            # Fallback: just delete the file
            file_path.unlink()

    def _move_to_trash_with_path(self, file_path: Path) -> Optional[Path]:
        """Move a file to the system trash and return the destination path.

        Returns the path where the file was moved, or None if it couldn't be determined.
        """
        system = platform.system()

        if system == "Darwin":  # macOS
            trash_dir = Path.home() / ".Trash"
            trash_dir.mkdir(exist_ok=True)

            dest_path = trash_dir / file_path.name
            counter = 1
            while dest_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_path = trash_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.move(str(file_path), str(dest_path))
            return dest_path

        elif system == "Windows":
            # On Windows, move to a temp trash location for undo capability
            trash_dir = Path.home() / ".metascan_trash"
            trash_dir.mkdir(exist_ok=True)

            dest_path = trash_dir / file_path.name
            counter = 1
            while dest_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_path = trash_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.move(str(file_path), str(dest_path))
            return dest_path

        elif system == "Linux":
            trash_dir = Path.home() / ".local" / "share" / "Trash" / "files"
            trash_dir.mkdir(parents=True, exist_ok=True)

            dest_path = trash_dir / file_path.name
            counter = 1
            while dest_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                dest_path = trash_dir / f"{stem}_{counter}{suffix}"
                counter += 1

            shutil.move(str(file_path), str(dest_path))
            return dest_path

        else:
            # Fallback: can't track the file after deletion
            file_path.unlink()
            return None

    def _delete_with_undo(self, media):
        """Delete media file with undo capability (no confirmation dialog)."""
        try:
            file_path = media.file_path
            original_file_path = file_path

            # 0. Release video file handle if this is a video being played
            if media.is_video and self.media_viewer.video_player:
                self.media_viewer.video_player.clear_video()

            # Get thumbnail path before deletion
            original_thumbnail_path = None
            if self.thumbnail_cache:
                original_thumbnail_path = self.thumbnail_cache.get_thumbnail_path(
                    file_path
                )

            # 1. Delete from database
            db_success = self.db_manager.delete_media(file_path)
            if not db_success:
                print(f"Warning: Media not found in database: {file_path}")

            # 2. Move thumbnail to trash
            thumbnail_trash_path = None
            if self.thumbnail_cache and original_thumbnail_path:
                if original_thumbnail_path.exists():
                    try:
                        thumbnail_trash_path = self._move_to_trash_with_path(
                            original_thumbnail_path
                        )
                        print(f"Moved thumbnail to trash: {thumbnail_trash_path}")
                    except Exception as e:
                        print(f"Failed to move thumbnail to trash: {e}")

            # 3. Move media file to trash
            file_trash_path = None
            if file_path.exists():
                try:
                    file_trash_path = self._move_to_trash_with_path(file_path)
                    print(f"Moved media file to trash: {file_trash_path}")
                except Exception as e:
                    print(f"Failed to move media file to trash: {e}")
                    QMessageBox.critical(
                        self, "Delete Error", f"Failed to move file to trash: {e}"
                    )
                    return

            # 4. Remove from all_media list
            self.all_media = [m for m in self.all_media if m.file_path != file_path]

            # 5. Update filters
            self.refresh_filters()

            # 6. Update media viewer's media list
            current_index = self.media_viewer.current_index
            media_list = self.media_viewer.media_list
            media_list = [m for m in media_list if m.file_path != file_path]
            self.media_viewer.media_list = media_list

            # 7. Update thumbnail view
            self.thumbnail_view.set_media_list(self.all_media)
            self.apply_all_filters()

            # 8. Prepare undo data
            undo_data = {
                "media": media,
                "file_trash_path": file_trash_path,
                "thumbnail_trash_path": thumbnail_trash_path,
                "original_file_path": original_file_path,
                "original_thumbnail_path": original_thumbnail_path,
            }

            # 9. Send undo data to media viewer and navigate to next file
            self.media_viewer.receive_undo_data(undo_data)
            self.media_viewer.navigate_after_delete()

            print(f"Successfully deleted with undo: {file_path.name}")

        except Exception as e:
            print(f"Error deleting media: {e}")
            QMessageBox.critical(self, "Delete Error", f"Failed to delete media: {e}")

    def _restore_deleted_media(self, undo_data: dict):
        """Restore a deleted media file from trash."""
        try:
            media = undo_data["media"]
            file_trash_path = undo_data["file_trash_path"]
            thumbnail_trash_path = undo_data["thumbnail_trash_path"]
            original_file_path = undo_data["original_file_path"]
            original_thumbnail_path = undo_data["original_thumbnail_path"]

            # 1. Restore media file from trash
            if file_trash_path and file_trash_path.exists():
                shutil.move(str(file_trash_path), str(original_file_path))
                print(f"Restored media file: {original_file_path}")
            else:
                print(f"Warning: Cannot restore - file not found in trash")
                QMessageBox.warning(
                    self,
                    "Restore Failed",
                    "Could not restore file - it may have been permanently deleted.",
                )
                return

            # 2. Restore thumbnail from trash
            if thumbnail_trash_path and thumbnail_trash_path.exists():
                if original_thumbnail_path:
                    shutil.move(str(thumbnail_trash_path), str(original_thumbnail_path))
                    print(f"Restored thumbnail: {original_thumbnail_path}")

            # 3. Re-add to database
            self.db_manager.save_media(media)
            print(f"Re-added media to database: {media.file_name}")

            # 4. Add back to all_media list
            self.all_media.append(media)

            # 5. Update filters
            self.refresh_filters()

            # 6. Add back to media viewer's list
            self.media_viewer.media_list.append(media)

            # 7. Update thumbnail view
            self.thumbnail_view.set_media_list(self.all_media)
            self.apply_all_filters()

            # 8. Navigate to restored media in viewer
            if media in self.media_viewer.media_list:
                self.media_viewer.current_index = self.media_viewer.media_list.index(
                    media
                )
                self.media_viewer._display_current_media()

            print(f"Successfully restored: {media.file_name}")

        except Exception as e:
            print(f"Error restoring media: {e}")
            QMessageBox.critical(self, "Restore Error", f"Failed to restore media: {e}")

    def _perform_full_cleanup(self):
        """Perform full cleanup: truncate database and move thumbnail cache to trash"""
        try:
            print("Starting full cleanup...")

            # 1. Truncate database
            print("Cleaning database...")
            db_success = self.db_manager.truncate_all_data()
            if not db_success:
                print("Warning: Database cleanup failed")
            else:
                print("Database cleaned successfully")

            # 2. Move thumbnail cache to trash
            print("Moving thumbnail cache to trash...")
            cache_success = self.thumbnail_cache.move_cache_to_trash()
            if not cache_success:
                print("Warning: Thumbnail cache cleanup failed")
            else:
                print("Thumbnail cache moved to trash successfully")

            # 3. Clear in-memory data
            print("Clearing in-memory data...")
            self.all_media.clear()
            self.filtered_media_paths = None  # Reset to None (no filters)
            self.current_filters.clear()
            self.favorites_active = False

            # 4. Update UI
            self.thumbnail_view.set_media_list([])
            self.metadata_panel.clear_content()
            self.refresh_filters()  # Clear filters panel

            print("Full cleanup completed successfully")

        except Exception as e:
            print(f"Error during full cleanup: {e}")
            QMessageBox.warning(
                self,
                "Cleanup Warning",
                f"Some cleanup operations failed: {e}\n\nScanning will continue, but some old data may remain.",
            )

    def _save_favorites_before_cleanup(self) -> Dict[str, bool]:
        """Save favorites from database before cleanup. Returns dict of file_path -> is_favorite"""
        saved_favorites = {}
        try:
            # Get all favorite media paths from database
            favorite_paths = self.db_manager.get_favorite_media_paths()
            # Store them in a dict (all favorites have value True)
            for path in favorite_paths:
                saved_favorites[path] = True
            if saved_favorites:
                print(f"Saved {len(saved_favorites)} favorite(s) before cleanup")
        except Exception as e:
            print(f"Error saving favorites before cleanup: {e}")
        return saved_favorites

    def _restore_favorites_after_scan(self):
        """Restore favorites after scan completion"""
        if not hasattr(self, "_favorites_to_restore") or not self._favorites_to_restore:
            return

        restored_count = 0
        try:
            # Iterate through saved favorites and restore them if the media still exists
            for file_path, was_favorite in self._favorites_to_restore.items():
                if was_favorite:
                    # Try to set the favorite status in the database
                    success = self.db_manager.set_favorite(Path(file_path), True)
                    if success:
                        restored_count += 1

            if restored_count > 0:
                print(f"Restored {restored_count} favorite(s) after scan")
        except Exception as e:
            print(f"Error restoring favorites after scan: {e}")

    def _open_selected_media(self):
        """Open the currently selected media file in the platform's default media viewer."""
        try:
            selected_media = self.thumbnail_view.get_selected_media()
            if not selected_media:
                return

            file_path = selected_media.file_path
            if not file_path.exists():
                QMessageBox.warning(
                    self, "File Not Found", f"The file no longer exists:\n{file_path}"
                )
                return

            # Open the media file in the platform's default viewer
            self._open_media_file_in_default_viewer(file_path)

        except Exception as e:
            print(f"Error opening selected media: {e}")
            QMessageBox.critical(
                self, "Open Media Error", f"Failed to open media file: {e}"
            )

    def _open_media_file_in_default_viewer(self, file_path: Path):
        """Open a media file in the platform's default media viewer."""
        try:
            system = platform.system()

            if system == "Darwin":  # macOS
                subprocess.run(["open", str(file_path)], check=True)
            elif system == "Windows":
                # Use start command to open with default application
                subprocess.run(["start", str(file_path)], shell=True, check=True)
            elif system == "Linux":
                # Check if running in WSL (Windows Subsystem for Linux)
                is_wsl = self._is_wsl()

                if is_wsl:
                    # In WSL, convert path to Windows format and use Windows commands
                    windows_path = self._convert_wsl_path_to_windows(file_path)
                    subprocess.run(
                        ["cmd.exe", "/c", "start", "", windows_path], check=True
                    )
                else:
                    # Use xdg-open to open with default application
                    subprocess.run(["xdg-open", str(file_path)], check=True)
            else:
                raise OSError(f"Unsupported platform: {system}")

            print(f"Opened media file in default viewer: {file_path}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to open media file with default viewer: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error opening media file: {e}")

    def _open_selected_folder(self):
        """Open the folder containing the currently selected media file."""
        try:
            selected_media = self.thumbnail_view.get_selected_media()
            if not selected_media:
                return

            file_path = selected_media.file_path
            if not file_path.exists():
                QMessageBox.warning(
                    self, "File Not Found", f"The file no longer exists:\n{file_path}"
                )
                return

            # Get the directory containing the file
            directory = file_path.parent
            self._open_directory_in_file_manager(directory)

        except Exception as e:
            print(f"Error opening selected folder: {e}")
            QMessageBox.critical(
                self, "Open Folder Error", f"Failed to open folder: {e}"
            )

    def _open_directory_in_file_manager(self, directory_path: Path):
        """Open a directory in the platform's default file manager."""
        try:
            system = platform.system()

            if system == "Darwin":  # macOS
                subprocess.run(["open", str(directory_path)], check=True)
            elif system == "Windows":
                # Use explorer to open the folder
                # Don't use check=True as Windows explorer can return non-zero even on success
                subprocess.run(["explorer", str(directory_path)])
            elif system == "Linux":
                # Check if running in WSL (Windows Subsystem for Linux)
                is_wsl = self._is_wsl()

                if is_wsl:
                    # In WSL, convert path to Windows format and use explorer.exe
                    windows_path = self._convert_wsl_path_to_windows(directory_path)
                    subprocess.run(["explorer.exe", windows_path])
                else:
                    # Try common Linux file managers
                    try:
                        subprocess.run(["xdg-open", str(directory_path)], check=True)
                    except subprocess.CalledProcessError:
                        # Fallback to common file managers
                        for fm in ["nautilus", "dolphin", "thunar", "pcmanfm"]:
                            try:
                                subprocess.run([fm, str(directory_path)], check=True)
                                break
                            except (subprocess.CalledProcessError, FileNotFoundError):
                                continue
                        else:
                            raise RuntimeError(
                                "No suitable file manager found on Linux"
                            )
            else:
                raise OSError(f"Unsupported platform: {system}")

            print(f"Opened directory in file manager: {directory_path}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to open directory with file manager: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error opening directory: {e}")

    def _is_wsl(self) -> bool:
        """Check if running in Windows Subsystem for Linux (WSL)."""
        try:
            # Check for WSL by looking at /proc/version
            with open("/proc/version", "r") as f:
                version_info = f.read().lower()
                return "microsoft" in version_info or "wsl" in version_info
        except Exception:
            return False

    def _convert_wsl_path_to_windows(self, path: Path) -> str:
        """Convert a WSL path to Windows format using wslpath."""
        try:
            result = subprocess.run(
                ["wslpath", "-w", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            windows_path = result.stdout.strip()
            return windows_path
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to convert WSL path to Windows: {e}")
            raise RuntimeError(f"Failed to convert WSL path: {path}")

    def _update_open_actions_state(self):
        """Update the Open and Open Folder action enabled/disabled states."""
        try:
            # Disable both if in multi-select mode
            if self.thumbnail_view.is_multi_select_mode():
                if hasattr(self, "open_action"):
                    self.open_action.setEnabled(False)
                if hasattr(self, "open_folder_action"):
                    self.open_folder_action.setEnabled(False)
                return

            # Enable both only if there's a selected media item
            selected_media = self.thumbnail_view.get_selected_media()
            has_selection = selected_media is not None

            if hasattr(self, "open_action"):
                self.open_action.setEnabled(has_selection)
            if hasattr(self, "open_folder_action"):
                self.open_folder_action.setEnabled(has_selection)

        except Exception as e:
            print(f"Error updating open action states: {e}")
            if hasattr(self, "open_action"):
                self.open_action.setEnabled(False)
            if hasattr(self, "open_folder_action"):
                self.open_folder_action.setEnabled(False)

    def _handle_upscale(self):
        """Handle upscale action for selected media."""
        try:
            # Get selected media
            selected_media_list = []

            if self.thumbnail_view.is_multi_select_mode():
                selected_media_list = self.thumbnail_view.get_all_selected_media()
            else:
                selected_media = self.thumbnail_view.get_selected_media()
                if selected_media:
                    selected_media_list = [selected_media]

            if not selected_media_list:
                QMessageBox.information(
                    self,
                    "No Selection",
                    "Please select one or more media files to upscale.",
                )
                return

            # Check if models are available
            if not self.media_upscaler.models_available:
                # Show setup dialog
                setup_dialog = ModelSetupDialog(self)
                setup_dialog.setup_completed.connect(self._setup_models)

                if setup_dialog.exec() == QDialog.DialogCode.Accepted:
                    # Models were set up, continue with upscaling
                    self._show_upscale_dialog(selected_media_list)
                return

            # Show upscale dialog
            self._show_upscale_dialog(selected_media_list)

        except Exception as e:
            print(f"Error handling upscale: {e}")
            QMessageBox.critical(self, "Error", f"Failed to start upscaling: {str(e)}")

    def _show_upscale_dialog(self, selected_media_list):
        """Show the upscale configuration dialog."""
        # Prepare media info for dialog
        media_files = []
        for media in selected_media_list:
            # Determine file type
            file_ext = Path(media.file_path).suffix.lower()
            if file_ext in [
                ".mp4",
                ".avi",
                ".mov",
                ".mkv",
                ".webm",
                ".flv",
                ".wmv",
                ".m4v",
            ]:
                file_type = "video"
            else:
                file_type = "image"

            media_info = {
                "filepath": str(media.file_path),
                "filename": media.file_name,
                "type": file_type,
                "width": media.width or 0,
                "height": media.height or 0,
                "file_size": media.file_size,
            }
            media_files.append(media_info)

        # Show dialog
        dialog = UpscaleDialog(media_files, self)
        dialog.upscale_requested.connect(self._add_upscale_tasks)
        dialog.exec()

    def _add_upscale_tasks(self, task_configs):
        """Add upscale tasks to the queue."""
        try:
            # Get worker count from first task config (all tasks use same worker count)
            worker_count = 1
            if task_configs and "worker_count" in task_configs[0]:
                worker_count = task_configs[0]["worker_count"]

            # Update queue's max_workers setting
            self.upscale_queue.max_workers = max(1, min(worker_count, 4))

            for config in task_configs:
                self.upscale_queue.add_task(
                    file_path=config["file_path"],
                    file_type=config["file_type"],
                    scale=config["scale"],
                    replace_original=config["replace_original"],
                    enhance_faces=config.get("enhance_faces", False),
                    interpolate_frames=config.get("interpolate_frames", False),
                    interpolation_factor=config.get("interpolation_factor", 2),
                    model_type=config.get("model_type", "general"),
                    fps_override=config.get("fps_override"),
                    preserve_metadata=config.get("preserve_metadata", True),
                )

            # Start processing (ProcessUpscaleQueue handles worker management)
            self.upscale_queue.start_processing()

        except Exception as e:
            print(f"Error adding upscale tasks: {e}")
            QMessageBox.critical(
                self, "Error", f"Failed to add tasks to queue: {str(e)}"
            )

    def _show_upscale_queue(self):
        """Show the upscale queue window."""
        if self.upscale_queue_window is None:
            self.upscale_queue_window = UpscaleQueueWindow(self.upscale_queue, self)

        self.upscale_queue_window.show()
        self.upscale_queue_window.raise_()
        self.upscale_queue_window.activateWindow()

    def _setup_models(self):
        """Setup AI models for upscaling."""
        setup_dialog = self.sender()

        def progress_callback(message, progress):
            setup_dialog.update_progress(message, progress)
            QApplication.processEvents()

        # Run setup in background
        success = self.media_upscaler.setup_models(progress_callback)

        if not success:
            QMessageBox.critical(
                self,
                "Setup Failed",
                "Failed to setup AI models. Please check your internet connection and try again.",
            )
        else:
            # Re-check models after successful setup to update the models_available flag
            self.media_upscaler._check_models()

    def closeEvent(self, event):
        """Handle application close event."""
        # Stop the polling timer
        if hasattr(self, "queue_poll_timer"):
            self.queue_poll_timer.stop()

        # Shutdown the upscale queue and any running processes
        if hasattr(self, "upscale_queue"):
            self.upscale_queue.shutdown()

        # Close queue window if open
        if hasattr(self, "upscale_queue_window") and self.upscale_queue_window:
            self.upscale_queue_window.close()

        # Continue with normal close
        event.accept()

    def on_media_database_updated(self, file_path: str, width: int, height: int):
        """Handle when media dimensions are updated in the database."""
        try:
            # Find and update the media in our cache
            for media in self.all_media:
                if str(media.file_path) == file_path:
                    media.width = width
                    media.height = height
                    break

            # If this media is currently selected, refresh the metadata panel
            selected_media = self.thumbnail_view.get_selected_media()
            if selected_media and str(selected_media.file_path) == file_path:
                self.metadata_panel.display_metadata(selected_media)

            # If this media is currently in the viewer, update it
            if self.media_viewer.isVisible() and hasattr(
                self.media_viewer, "current_media"
            ):
                if (
                    self.media_viewer.current_media
                    and str(self.media_viewer.current_media.file_path) == file_path
                ):
                    self.media_viewer.current_media.width = width
                    self.media_viewer.current_media.height = height

            self.logger.info(f"Updated UI for media {file_path}: {width}x{height}")

        except Exception as e:
            self.logger.error(f"Error updating UI after database update: {e}")


def _setup_logging() -> None:
    """Configure logging for the main application process.

    Sets up a rotating file handler (logs/metascan.log) and a stderr
    handler so that INFO+ messages are captured to both file and console.
    """
    import logging
    import logging.handlers
    from metascan.utils.app_paths import get_data_dir

    log_dir = get_data_dir().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "metascan.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger(__name__).info(f"Logging initialized — log file: {log_file}")


def main():
    from metascan.utils.startup_profiler import log_startup_complete

    _setup_logging()

    log_startup("main(): Creating QApplication")
    app = QApplication(sys.argv)

    # Fix for Windows DirectWrite font issues
    if platform.system() == "Windows":
        # Set a reliable font that exists on Windows
        from PyQt6.QtGui import QFont

        default_font = QFont("Segoe UI", 9)  # Standard Windows font
        app.setFont(default_font)

    log_startup("main(): Creating MainWindow")
    window = MainWindow()
    log_startup("main(): Showing MainWindow")
    window.show()
    log_startup_complete()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
