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

        # Output options
        output_label = QLabel("Output Location:")
        options_layout.addWidget(output_label)

        self.output_group = QButtonGroup(self)

        self.suffix_radio = QRadioButton("Save with suffix (original_x2.ext)")
        self.suffix_radio.setChecked(True)
        self.output_group.addButton(self.suffix_radio, 0)
        options_layout.addWidget(self.suffix_radio)

        self.replace_radio = QRadioButton("Replace original (original moved to backup)")
        self.output_group.addButton(self.replace_radio, 1)
        options_layout.addWidget(self.replace_radio)

        layout.addWidget(options_group)

        # Info section
        info_group = QGroupBox("Information")
        info_layout = QVBoxLayout(info_group)

        info_text = (
            "• Upscaling uses AI models to enhance image/video quality\n"
            "• Processing time depends on file size and resolution\n"
            "• Videos may take significantly longer than images\n"
            "• Files will be added to the processing queue"
        )
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)

        layout.addWidget(info_group)

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
        replace_original = self.replace_radio.isChecked()

        # Prepare task configurations
        tasks = []
        for file_info in self.media_files:
            task_config = {
                "file_path": file_info["filepath"],
                "file_type": file_info.get("type", "image"),
                "scale": scale,
                "replace_original": replace_original,
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
            "• RealESRGAN x4 model (~65 MB)\n\n"
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
