"""
Settings dialog for similarity/embedding features.

Allows configuration of CLIP model, device, thresholds, and provides
controls for building/rebuilding the embedding index.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QComboBox,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QCheckBox,
    QProgressBar,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from metascan.core.embedding_manager import CLIP_MODELS
from metascan.core.embedding_queue import EmbeddingQueue
from metascan.core.database_sqlite import DatabaseManager
from metascan.utils.app_paths import get_data_dir, get_config_path

logger = logging.getLogger(__name__)


class SimilaritySettingsDialog(QDialog):
    """Settings dialog for similarity features.

    The EmbeddingQueue is owned externally (by MainWindow) so that indexing
    survives dialog close/reopen. This dialog connects to the queue's signals
    on show and reflects the current state.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        embedding_queue: EmbeddingQueue,
        parent=None,
    ):
        super().__init__(parent)
        self.db_manager = db_manager
        self.embedding_queue = embedding_queue
        self._config = self._load_config()

        self.setWindowTitle("Similarity Settings")
        self.setMinimumWidth(500)
        self.setModal(False)

        self._init_ui()
        self._update_index_status()

        # Connect to queue signals
        self.embedding_queue.progress_updated.connect(self._on_progress)
        self.embedding_queue.indexing_complete.connect(self._on_complete)
        self.embedding_queue.indexing_error.connect(self._on_error)

        # If indexing is already running, restore the progress UI
        self._sync_ui_to_queue_state()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Model settings group
        model_group = QGroupBox("CLIP Model")
        model_layout = QFormLayout(model_group)

        self.model_combo = QComboBox()
        for key, info in CLIP_MODELS.items():
            self.model_combo.addItem(info["description"], key)
        current_model = self._config.get("clip_model", "small")
        idx = (
            list(CLIP_MODELS.keys()).index(current_model)
            if current_model in CLIP_MODELS
            else 0
        )
        self.model_combo.setCurrentIndex(idx)
        model_layout.addRow("Model:", self.model_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItem("Auto (GPU if available)", "auto")
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("CUDA (GPU)", "cuda")
        current_device = self._config.get("device", "auto")
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == current_device:
                self.device_combo.setCurrentIndex(i)
                break
        model_layout.addRow("Device:", self.device_combo)

        layout.addWidget(model_group)

        # Thresholds group
        threshold_group = QGroupBox("Thresholds")
        threshold_layout = QFormLayout(threshold_group)

        phash_row = QHBoxLayout()
        self.phash_slider = QSlider(Qt.Orientation.Horizontal)
        self.phash_slider.setMinimum(0)
        self.phash_slider.setMaximum(20)
        self.phash_slider.setValue(self._config.get("phash_threshold", 10))
        self.phash_slider.valueChanged.connect(self._on_phash_threshold_changed)
        phash_row.addWidget(self.phash_slider)
        self.phash_label = QLabel(str(self.phash_slider.value()))
        self.phash_label.setFixedWidth(30)
        phash_row.addWidget(self.phash_label)
        threshold_layout.addRow("pHash threshold:", phash_row)

        clip_row = QHBoxLayout()
        self.clip_slider = QSlider(Qt.Orientation.Horizontal)
        self.clip_slider.setMinimum(0)
        self.clip_slider.setMaximum(100)
        self.clip_slider.setValue(int(self._config.get("clip_threshold", 0.7) * 100))
        self.clip_slider.valueChanged.connect(self._on_clip_threshold_changed)
        clip_row.addWidget(self.clip_slider)
        self.clip_label = QLabel(f"{self.clip_slider.value() / 100:.2f}")
        self.clip_label.setFixedWidth(40)
        clip_row.addWidget(self.clip_label)
        threshold_layout.addRow("CLIP threshold:", clip_row)

        self.results_spin = QSpinBox()
        self.results_spin.setMinimum(10)
        self.results_spin.setMaximum(500)
        self.results_spin.setValue(self._config.get("search_results_count", 100))
        threshold_layout.addRow("Search results:", self.results_spin)

        self.keyframes_spin = QSpinBox()
        self.keyframes_spin.setMinimum(1)
        self.keyframes_spin.setMaximum(16)
        self.keyframes_spin.setValue(self._config.get("video_keyframes", 4))
        threshold_layout.addRow("Video keyframes:", self.keyframes_spin)

        self.phash_scan_check = QCheckBox("Compute pHash during scan")
        self.phash_scan_check.setChecked(
            self._config.get("compute_phash_during_scan", True)
        )
        threshold_layout.addRow(self.phash_scan_check)

        layout.addWidget(threshold_group)

        # Index status group
        index_group = QGroupBox("Embedding Index")
        index_layout = QVBoxLayout(index_group)

        self.index_status_label = QLabel("Loading...")
        index_layout.addWidget(self.index_status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        index_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        index_layout.addWidget(self.progress_label)

        index_buttons = QHBoxLayout()

        self.build_button = QPushButton("Build Index")
        self.build_button.clicked.connect(self._build_index)
        index_buttons.addWidget(self.build_button)

        self.rebuild_button = QPushButton("Rebuild All")
        self.rebuild_button.setToolTip(
            "Clear existing index and recompute all embeddings"
        )
        self.rebuild_button.clicked.connect(self._rebuild_index)
        index_buttons.addWidget(self.rebuild_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_indexing)
        index_buttons.addWidget(self.cancel_button)

        index_layout.addLayout(index_buttons)
        layout.addWidget(index_group)

        # Save/Close buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_config)
        button_row.addWidget(save_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

    def _sync_ui_to_queue_state(self) -> None:
        """Sync the dialog UI to the current queue/worker state.

        Called on dialog open to restore progress display if a worker
        is already running.
        """
        if self.embedding_queue.is_indexing():
            # Worker is running — show progress UI
            self.build_button.setEnabled(False)
            self.rebuild_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.progress_bar.setVisible(True)
            self.progress_label.setVisible(True)
            self.progress_label.setText("Indexing in progress...")

            # Read current progress from file to populate the bar
            last = self.embedding_queue.get_last_progress()
            if last:
                current = last.get("current", 0)
                total = last.get("total", 0)
                status = last.get("status", "")
                self.progress_bar.setMaximum(max(total, 1))
                self.progress_bar.setValue(current)
                if status == "processing":
                    current_file = last.get("current_file", "")
                    errors_count = last.get("errors_count", 0)
                    label = f"Indexing {current}/{total}"
                    if current_file:
                        label += f" — {current_file}"
                    if errors_count > 0:
                        label += f" ({errors_count} errors)"
                    self.progress_label.setText(label)
                elif status == "loading_model":
                    self.progress_label.setText("Loading CLIP model...")
                elif status == "downloading_model":
                    self.progress_label.setText("Downloading model...")
        else:
            # Not running — idle state
            self.build_button.setEnabled(True)
            self.rebuild_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.progress_bar.setVisible(False)
            self.progress_label.setVisible(False)

    def _on_phash_threshold_changed(self, value: int) -> None:
        self.phash_label.setText(str(value))

    def _on_clip_threshold_changed(self, value: int) -> None:
        self.clip_label.setText(f"{value / 100:.2f}")

    def _update_index_status(self) -> None:
        """Update the index status label."""
        stats = self.db_manager.get_embedding_stats()
        total = stats["total_media"]
        hashed = stats["hashed"]
        embedded = stats["embedded"]
        model = stats["clip_model"] or "none"

        # Check index file size
        index_file = get_data_dir() / "similarity" / "faiss_index.bin"
        if index_file.exists():
            size_mb = index_file.stat().st_size / (1024 * 1024)
            size_str = f"{size_mb:.1f} MB"
        else:
            size_str = "no index file"

        self.index_status_label.setText(
            f"Files: {total} total | {hashed} hashed | {embedded} embedded\n"
            f"Model: {model} | Index size: {size_str}"
        )

    def _build_index(self) -> None:
        """Build embeddings for unembedded files."""
        self._save_config()

        unembedded = self.db_manager.get_unembedded_file_paths()
        if not unembedded:
            QMessageBox.information(
                self, "Up to Date", "All files are already indexed."
            )
            return

        model_key = self.model_combo.currentData()
        device = self.device_combo.currentData()
        db_path = str(self.db_manager.db_path)

        self._start_indexing(unembedded, model_key, device, db_path)

    def _rebuild_index(self) -> None:
        """Clear and rebuild the entire index."""
        reply = QMessageBox.question(
            self,
            "Rebuild Index",
            "This will clear all existing embeddings and recompute them.\n"
            "This may take a while for large collections.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._save_config()

        # Clear existing index
        from metascan.core.embedding_manager import FaissIndexManager

        faiss_mgr = FaissIndexManager(get_data_dir() / "similarity")
        faiss_mgr.clear()
        self.db_manager.clear_embeddings()

        # Get all file paths
        all_paths = [str(p) for p in self.db_manager.get_existing_file_paths()]
        if not all_paths:
            QMessageBox.information(self, "No Files", "No media files in database.")
            return

        model_key = self.model_combo.currentData()
        device = self.device_combo.currentData()
        db_path = str(self.db_manager.db_path)

        self._start_indexing(all_paths, model_key, device, db_path)

    def _start_indexing(
        self, file_paths: list, model_key: str, device: str, db_path: str
    ) -> None:
        """Start the embedding worker."""
        self.build_button.setEnabled(False)
        self.rebuild_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(file_paths))
        self.progress_bar.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText("Starting...")

        self.embedding_queue.start_indexing(
            file_paths=file_paths,
            clip_model_key=model_key,
            device=device,
            db_path=db_path,
            compute_phash=self.phash_scan_check.isChecked(),
            video_keyframes=self.keyframes_spin.value(),
        )

    def _cancel_indexing(self) -> None:
        self.embedding_queue.cancel_indexing()
        self.progress_label.setText("Cancelling...")

    def _on_progress(self, current: int, total: int, status_text: str) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        self.progress_label.setText(status_text)

    def _on_complete(self, total: int) -> None:
        self.build_button.setEnabled(True)
        self.rebuild_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"Indexing complete: {total} files processed")
        self.progress_label.setVisible(True)
        self._update_index_status()
        logger.info(f"Indexing complete: {total} files")

    def _on_error(self, error: str) -> None:
        self.build_button.setEnabled(True)
        self.rebuild_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"Error: {error[:200]}")
        self.progress_label.setVisible(True)
        self._update_index_status()
        logger.error(f"Indexing error: {error}")
        QMessageBox.warning(
            self,
            "Indexing Error",
            f"{error}\n\nCheck logs/embedding_worker.log for details.",
        )

    def _load_config(self) -> Dict[str, Any]:
        """Load similarity config from the application config file."""
        try:
            config_path = get_config_path()
            if config_path.exists():
                with open(config_path, "r") as f:
                    full_config = json.load(f)
                return full_config.get("similarity", {})
        except Exception as e:
            logger.error(f"Failed to load similarity config: {e}")
        return {}

    def _save_config(self) -> None:
        """Save current settings to the config file."""
        try:
            config_path = get_config_path()
            full_config = {}
            if config_path.exists():
                with open(config_path, "r") as f:
                    full_config = json.load(f)

            full_config["similarity"] = {
                "clip_model": self.model_combo.currentData(),
                "device": self.device_combo.currentData(),
                "phash_threshold": self.phash_slider.value(),
                "clip_threshold": self.clip_slider.value() / 100.0,
                "search_results_count": self.results_spin.value(),
                "video_keyframes": self.keyframes_spin.value(),
                "compute_phash_during_scan": self.phash_scan_check.isChecked(),
            }

            with open(config_path, "w") as f:
                json.dump(full_config, f, indent=2)

            logger.info("Similarity settings saved")

        except Exception as e:
            logger.error(f"Failed to save similarity config: {e}")
            QMessageBox.warning(self, "Error", f"Failed to save settings: {e}")
