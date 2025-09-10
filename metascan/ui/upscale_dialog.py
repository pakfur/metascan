"""
Dialog for configuring upscale options for selected media files.
"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QRadioButton,
    QButtonGroup,
    QPushButton,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialogButtonBox,
    QMessageBox,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from pathlib import Path
from typing import List, Dict, Any


class UpscaleDialog(QDialog):
    """Dialog for configuring upscale options."""

    # Signal emitted when user confirms upscaling
    upscale_requested = pyqtSignal(list)  # List of task configurations

    def __init__(self, media_files: List[Dict[str, Any]], parent=None):
        """
        Initialize the upscale dialog.

        Args:
            media_files: List of media file info dicts with keys:
                - filepath: str
                - filename: str
                - type: str ("image" or "video")
                - width: int
                - height: int
                - file_size: int (optional)
            parent: Parent widget
        """
        super().__init__(parent)
        self.media_files = media_files
        self.setWindowTitle("Upscale Media Files")
        self.setModal(True)
        self.setMinimumWidth(600)

        self._init_ui()

    def _init_ui(self):
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Files info section
        files_group = QGroupBox("Selected Files")
        files_layout = QVBoxLayout(files_group)

        # File count label
        file_count = len(self.media_files)
        image_count = sum(1 for f in self.media_files if f.get("type") == "image")
        video_count = sum(1 for f in self.media_files if f.get("type") == "video")

        count_text = f"Total: {file_count} file(s)"
        if image_count > 0:
            count_text += f" - {image_count} image(s)"
        if video_count > 0:
            count_text += f" - {video_count} video(s)"

        count_label = QLabel(count_text)
        files_layout.addWidget(count_label)

        # Files table
        if file_count <= 10:
            # Show detailed list for small selections
            self.files_table = QTableWidget()
            self.files_table.setColumnCount(3)
            self.files_table.setHorizontalHeaderLabels(
                ["File Name", "Type", "Resolution"]
            )
            self.files_table.horizontalHeader().setStretchLastSection(True)
            self.files_table.setRowCount(file_count)

            for row, file_info in enumerate(self.media_files):
                # File name
                name_item = QTableWidgetItem(file_info.get("filename", "Unknown"))
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.files_table.setItem(row, 0, name_item)

                # Type
                file_type = file_info.get("type", "unknown").capitalize()
                type_item = QTableWidgetItem(file_type)
                type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.files_table.setItem(row, 1, type_item)

                # Resolution
                width = file_info.get("width", 0)
                height = file_info.get("height", 0)
                resolution = f"{width}x{height}" if width and height else "Unknown"
                res_item = QTableWidgetItem(resolution)
                res_item.setFlags(res_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.files_table.setItem(row, 2, res_item)

            self.files_table.resizeColumnsToContents()
            self.files_table.setMaximumHeight(200)
            files_layout.addWidget(self.files_table)
        else:
            # Just show summary for large selections
            summary_label = QLabel("Files will be processed in queue order.")
            files_layout.addWidget(summary_label)

        layout.addWidget(files_group)

        # Upscale options section
        options_group = QGroupBox("Upscale Options")
        options_layout = QVBoxLayout(options_group)

        # Scale factor
        scale_layout = QHBoxLayout()
        scale_label = QLabel("Upscale Factor:")
        scale_layout.addWidget(scale_label)

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["2x", "4x"])
        self.scale_combo.setCurrentIndex(0)  # Default to 2x
        scale_layout.addWidget(self.scale_combo)
        scale_layout.addStretch()

        options_layout.addLayout(scale_layout)

        # Model type selection
        model_layout = QHBoxLayout()
        model_label = QLabel("Model Type:")
        model_layout.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["General (photo-realistic)", "Anime/Illustration"])
        self.model_combo.setCurrentIndex(0)  # Default to general
        self.model_combo.setToolTip(
            "General: Best for photographs and realistic images\n"
            "Anime: Optimized for anime, illustrations, and drawn content"
        )
        model_layout.addWidget(self.model_combo)
        model_layout.addStretch()

        options_layout.addLayout(model_layout)

        # Face enhancement option
        self.face_enhancement_checkbox = QCheckBox("Enhance faces (GFPGAN)")
        self.face_enhancement_checkbox.setToolTip(
            "Uses GFPGAN to enhance and restore faces in images/videos.\n"
            "Best for photos with people, may increase processing time."
        )
        options_layout.addWidget(self.face_enhancement_checkbox)

        # Frame interpolation options (for videos)
        has_videos = any(f.get("type") == "video" for f in self.media_files)
        if has_videos:
            self.interpolate_frames_checkbox = QCheckBox(
                "Interpolate frames (increase FPS)"
            )
            self.interpolate_frames_checkbox.setToolTip(
                "Uses RIFE to interpolate frames and increase video FPS.\n"
                "Results in smoother video motion but significantly increases processing time."
            )
            options_layout.addWidget(self.interpolate_frames_checkbox)

            # Interpolation factor
            interp_layout = QHBoxLayout()
            interp_label = QLabel("FPS Multiplier:")
            interp_layout.addWidget(interp_label)

            self.interpolation_combo = QComboBox()
            self.interpolation_combo.addItems(["2x", "4x", "8x"])
            self.interpolation_combo.setCurrentIndex(0)  # Default to 2x
            self.interpolation_combo.setEnabled(
                False
            )  # Enabled when checkbox is checked
            interp_layout.addWidget(self.interpolation_combo)
            interp_layout.addStretch()

            options_layout.addLayout(interp_layout)

            # Connect checkbox to enable/disable combo
            self.interpolate_frames_checkbox.toggled.connect(
                self.interpolation_combo.setEnabled
            )

            # FPS override (for videos)
            fps_layout = QHBoxLayout()
            self.fps_override_checkbox = QCheckBox("Custom FPS:")
            fps_layout.addWidget(self.fps_override_checkbox)

            self.fps_spinbox = QDoubleSpinBox()
            self.fps_spinbox.setRange(1.0, 120.0)
            self.fps_spinbox.setValue(30.0)
            self.fps_spinbox.setSuffix(" fps")
            self.fps_spinbox.setEnabled(False)  # Enabled when checkbox is checked
            fps_layout.addWidget(self.fps_spinbox)
            fps_layout.addStretch()

            options_layout.addLayout(fps_layout)

            # Connect checkbox to enable/disable spinbox
            self.fps_override_checkbox.toggled.connect(self.fps_spinbox.setEnabled)

        # Metadata preservation option
        self.preserve_metadata_checkbox = QCheckBox("Preserve original metadata")
        self.preserve_metadata_checkbox.setChecked(True)  # Default to True
        self.preserve_metadata_checkbox.setToolTip(
            "Preserves EXIF data for images and metadata for videos.\n"
            "Includes creation date, camera settings, and other original file information."
        )
        options_layout.addWidget(self.preserve_metadata_checkbox)

        # Output note
        output_label = QLabel(
            "Note: Original files will be moved to trash after upscaling"
        )
        output_label.setStyleSheet("QLabel { color: gray; font-style: italic; }")
        options_layout.addWidget(output_label)

        layout.addWidget(options_group)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)

        # Rename OK button to "Begin"
        ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
            ok_button.setText("Begin")

        layout.addWidget(button_box)

    def _on_accept(self):
        """Handle accept button click."""
        # Get selected options
        scale = int(self.scale_combo.currentText().replace("x", ""))
        replace_original = True
        enhance_faces = self.face_enhancement_checkbox.isChecked()

        # Model type
        model_type = "anime" if self.model_combo.currentIndex() == 1 else "general"

        # Frame interpolation options
        interpolate_frames = False
        interpolation_factor = 2
        if hasattr(self, "interpolate_frames_checkbox"):
            interpolate_frames = self.interpolate_frames_checkbox.isChecked()
            if interpolate_frames:
                interpolation_factor = int(
                    self.interpolation_combo.currentText().replace("x", "")
                )

        # FPS override
        fps_override = None
        if (
            hasattr(self, "fps_override_checkbox")
            and self.fps_override_checkbox.isChecked()
        ):
            fps_override = self.fps_spinbox.value()

        # Metadata preservation
        preserve_metadata = self.preserve_metadata_checkbox.isChecked()

        # Prepare task configurations
        tasks = []
        for file_info in self.media_files:
            task_config = {
                "file_path": file_info["filepath"],
                "file_type": file_info.get("type", "image"),
                "scale": scale,
                "replace_original": replace_original,
                "enhance_faces": enhance_faces,
                "interpolate_frames": interpolate_frames
                if file_info.get("type") == "video"
                else False,
                "interpolation_factor": interpolation_factor,
                "model_type": model_type,
                "fps_override": fps_override
                if file_info.get("type") == "video"
                else None,
                "preserve_metadata": preserve_metadata,
            }
            tasks.append(task_config)

        # Emit signal with task configurations
        self.upscale_requested.emit(tasks)
        self.accept()


class ModelSetupDialog(QDialog):
    """Dialog for setting up AI models."""

    setup_completed = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the model setup dialog."""
        super().__init__(parent)
        self.setWindowTitle("Setup AI Models")
        self.setModal(True)
        self.setMinimumWidth(500)

        self._init_ui()

    def _init_ui(self):
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Info text
        info_text = (
            "AI models are required for upscaling functionality.\n\n"
            "The following models will be downloaded:\n"
            "• RealESRGAN x2 model (~60 MB)\n"
            "• RealESRGAN x4 model (~65 MB)\n"
            "• RealESRGAN x4 anime model (~18 MB)\n"
            "• GFPGAN model for face enhancement (~350 MB)\n"
            "• RIFE binary and model for frame interpolation (~15 MB)\n\n"
            "This is a one-time setup that may take a few minutes."
        )
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Progress label
        self.progress_label = QLabel("Ready to begin setup...")
        layout.addWidget(self.progress_label)

        # Buttons
        button_box = QDialogButtonBox()

        self.begin_button = QPushButton("Begin Setup")
        self.begin_button.clicked.connect(self._begin_setup)
        button_box.addButton(self.begin_button, QDialogButtonBox.ButtonRole.AcceptRole)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)

        layout.addWidget(button_box)

    def _begin_setup(self):
        """Begin the setup process."""
        self.begin_button.setEnabled(False)
        self.progress_label.setText("Starting setup...")

        # The actual setup will be handled by the parent widget
        # This dialog just provides the UI
        self.setup_completed.emit()

    def update_progress(self, message: str, progress: float):
        """Update the progress display."""
        self.progress_label.setText(f"{message} ({progress:.0f}%)")

        if progress >= 100:
            self.progress_label.setText("Setup completed successfully!")
            self.accept()
