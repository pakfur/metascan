import json
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QTextEdit,
    QMessageBox,
    QGroupBox,
    QLineEdit,
    QApplication,
)

from metascan.core.media import Media


class MetadataField(QFrame):
    def __init__(self, label: str, value: str, is_multiline: bool = False, parent=None):
        super().__init__(parent)
        self.label_text = label
        self.value_text = value
        self.is_multiline = is_multiline

        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)
        # Use theme styling

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        header_layout = QHBoxLayout()

        # Label
        label = QLabel(self.label_text)
        label_font = QFont()
        label_font.setBold(True)
        label_font.setPointSize(8)
        label.setFont(label_font)
        header_layout.addWidget(label)

        header_layout.addStretch()

        copy_button = QPushButton("📋")
        copy_button.setFixedSize(20, 20)
        copy_button.setToolTip(f"Copy {self.label_text} to clipboard")
        copy_button.clicked.connect(self.copy_to_clipboard)
        header_layout.addWidget(copy_button)

        layout.addLayout(header_layout)

        # Value display
        if self.is_multiline:
            self.value_widget = QTextEdit()
            self.value_widget.setPlainText(self.value_text)
            self.value_widget.setReadOnly(True)
            self.value_widget.setMaximumHeight(80)
        else:
            self.value_widget = QLineEdit()
            self.value_widget.setText(self.value_text)
            self.value_widget.setReadOnly(True)
        layout.addWidget(self.value_widget)

    def copy_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.value_text)

        original_style = self.styleSheet()
        self.setStyleSheet(
            original_style
            + """
            MetadataField {
                background-color: #d4edda;
                border-color: #28a745;
            }
        """
        )

        QTimer.singleShot(300, lambda: self.setStyleSheet(original_style))

    def update_value(self, new_value: str):
        self.value_text = new_value
        if self.is_multiline:
            self.value_widget.setPlainText(new_value)
        else:
            self.value_widget.setText(new_value)


class MetadataSection(QGroupBox):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.fields: Dict[str, MetadataField] = {}
        self.setup_ui()

    def setup_ui(self):
        self.setCheckable(True)
        self.setChecked(True)  # Start expanded

        self.layout: QVBoxLayout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 12, 6, 6)
        self.layout.setSpacing(3)

        self.toggled.connect(self.on_toggled)

    def add_field(self, key: str, label: str, value: str, is_multiline: bool = False):
        if value is None:
            value = "N/A"

        field = MetadataField(label, str(value), is_multiline)
        self.layout.addWidget(field)
        self.fields[key] = field
        return field

    def update_field(self, key: str, value: str):
        if key in self.fields:
            self.fields[key].update_value(str(value) if value is not None else "N/A")

    def on_toggled(self, checked: bool):
        for field in self.fields.values():
            field.setVisible(checked)


class ThumbnailPreview(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 100)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            """
            QLabel {
                border: 2px solid #ddd;
                border-radius: 8px;
                background-color: #f9f9f9;
            }
        """
        )
        self.setText("No Image")

    def set_thumbnail(self, thumbnail_path: Optional[Path]):
        """Set the thumbnail image."""
        if thumbnail_path and thumbnail_path.exists():
            try:
                pixmap = QPixmap(str(thumbnail_path))
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(
                        116,
                        116,  # Account for border
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.setPixmap(scaled_pixmap)
                else:
                    self.setText("Invalid Image")
            except Exception:
                self.setText("Load Error")
        else:
            self.setText("No Preview")


class MetadataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_media: Optional[Media] = None
        self.sections = {}
        self.thumbnail_cache = None

        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        header_layout = QHBoxLayout()

        header_layout.addStretch()

        self.copy_all_button = QPushButton("Copy All")
        self.copy_all_button.setToolTip("Copy all metadata to clipboard as JSON")
        self.copy_all_button.clicked.connect(self.copy_all_metadata)
        self.copy_all_button.setEnabled(False)
        header_layout.addWidget(self.copy_all_button)

        main_layout.addLayout(header_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(3, 3, 3, 3)
        self.content_layout.setSpacing(6)

        self.no_selection_label = QLabel("Select a media file to view metadata")
        self.no_selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self.no_selection_label)

        self.content_layout.addStretch()

        scroll_area.setWidget(self.content_widget)
        main_layout.addWidget(scroll_area)

    def set_thumbnail_cache(self, thumbnail_cache):
        self.thumbnail_cache = thumbnail_cache

    def display_metadata(self, media: Media):
        self.current_media = media
        self.clear_content()

        self.no_selection_label.setVisible(False)

        self.create_preview_section(media)

        self.create_prompt_section(media)
        self.create_lora_section(media)
        self.create_generation_section(media)
        self.create_file_info_section(media)
        self.create_technical_section(media)

        self.copy_all_button.setEnabled(True)

    def clear_content(self):
        while self.content_layout.count() > 0:
            item = self.content_layout.takeAt(0)
            if item.widget() and item.widget() != self.no_selection_label:
                item.widget().deleteLater()

        self.sections.clear()

        self.content_layout.addWidget(self.no_selection_label)
        self.content_layout.addStretch()

        self.no_selection_label.setVisible(True)
        self.copy_all_button.setEnabled(False)

    def create_preview_section(self, media: Media):
        preview_widget = QWidget()
        preview_layout = QHBoxLayout(preview_widget)
        preview_layout.setContentsMargins(6, 6, 6, 6)

        thumbnail = ThumbnailPreview()
        if self.thumbnail_cache:
            thumbnail_path = self.thumbnail_cache.get_thumbnail_path(media.file_path)
            thumbnail.set_thumbnail(thumbnail_path)
        preview_layout.addWidget(thumbnail)

        info_layout = QVBoxLayout()

        filename_label = QLabel(media.file_name)
        filename_font = QFont()
        filename_font.setBold(True)
        filename_font.setPointSize(10)
        filename_label.setFont(filename_font)
        filename_label.setWordWrap(True)
        info_layout.addWidget(filename_label)

        size_label = QLabel(f"{media.width} × {media.height} pixels")
        # Use theme styling for size label
        info_layout.addWidget(size_label)

        # Format label with media type
        media_type = "VIDEO" if media.is_video else "IMAGE"
        format_label = QLabel(
            f"{media.format} • {media_type} • {self.format_file_size(media.file_size)}"
        )
        # Use theme styling for format label
        info_layout.addWidget(format_label)

        # Add video-specific info in preview
        if media.is_video:
            video_info_parts = []
            if media.duration:
                video_info_parts.append(f"{media.duration:.1f}s")
            if media.frame_rate:
                video_info_parts.append(f"{media.frame_rate:.1f} fps")
            if media.video_length:
                video_info_parts.append(f"{media.video_length} frames")

            if video_info_parts:
                video_info_label = QLabel(" • ".join(video_info_parts))
                info_layout.addWidget(video_info_label)

        info_layout.addStretch()
        preview_layout.addLayout(info_layout)

        preview_layout.addStretch()

        # Insert at top
        self.content_layout.insertWidget(0, preview_widget)

    def create_file_info_section(self, media: Media):
        section = MetadataSection("File Information")

        section.add_field("filename", "Filename", media.file_name)
        section.add_field(
            "filepath", "File Path", str(media.file_path), is_multiline=True
        )
        section.add_field(
            "filesize", "File Size", self.format_file_size(media.file_size)
        )
        section.add_field("dimensions", "Dimensions", f"{media.width} × {media.height}")
        section.add_field("format", "Format", media.format)
        section.add_field(
            "created", "Created", media.created_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        section.add_field(
            "modified", "Modified", media.modified_at.strftime("%Y-%m-%d %H:%M:%S")
        )

        if media.tags:
            section.add_field("tags", "Tags", ", ".join(media.tags))

        self.sections["file_info"] = section
        self.content_layout.insertWidget(-1, section)  # Before stretch

    def create_generation_section(self, media: Media):
        section_title = (
            "🎥 Video Generation Parameters"
            if media.is_video
            else "Generation Parameters"
        )
        section = MetadataSection(section_title)

        section.add_field("source", "Source", media.metadata_source or "Unknown")
        section.add_field("model", "Model", media.model or "Unknown")
        section.add_field("sampler", "Sampler", media.sampler or "Unknown")
        section.add_field("scheduler", "Scheduler", media.scheduler or "Unknown")
        section.add_field(
            "steps", "Steps", str(media.steps) if media.steps else "Unknown"
        )
        section.add_field(
            "cfg_scale",
            "CFG Scale",
            str(media.cfg_scale) if media.cfg_scale else "Unknown",
        )
        section.add_field("seed", "Seed", str(media.seed) if media.seed else "Unknown")

        if media.is_video:
            if media.frame_rate:
                section.add_field(
                    "frame_rate", "Frame Rate", f"{media.frame_rate:.1f} fps"
                )
            if media.duration:
                section.add_field(
                    "duration", "Duration", f"{media.duration:.2f} seconds"
                )
            if media.video_length:
                section.add_field(
                    "video_length", "Frame Count", str(media.video_length)
                )

        self.sections["generation"] = section
        self.content_layout.insertWidget(-1, section)

    def create_prompt_section(self, media: Media):
        section = MetadataSection("Prompts")

        if media.prompt:
            section.add_field(
                "prompt", "Positive Prompt", media.prompt, is_multiline=True
            )

        if media.negative_prompt:
            section.add_field(
                "negative_prompt",
                "Negative Prompt",
                media.negative_prompt,
                is_multiline=True,
            )

        if media.prompt or media.negative_prompt:
            self.sections["prompts"] = section
            self.content_layout.insertWidget(-1, section)

    def create_lora_section(self, media: Media):
        if not media.loras:
            return

        section = MetadataSection("LoRAs")

        lora_lines = []
        for lora in media.loras:
            lora_line = f"{lora.lora_weight}: {lora.lora_name}"
            lora_lines.append(lora_line)

        loras_text = "\n".join(lora_lines)

        section.add_field("loras", "LoRAs", loras_text, is_multiline=True)

        self.sections["loras"] = section
        self.content_layout.insertWidget(-1, section)

    def create_technical_section(self, media: Media):
        if not media.generation_data:
            return

        section = MetadataSection("Technical Data")
        section.setChecked(False)  # Start collapsed

        try:
            formatted_data = json.dumps(media.generation_data, indent=2, sort_keys=True)
            section.add_field(
                "raw_data", "Raw Generation Data", formatted_data, is_multiline=True
            )
        except Exception:
            section.add_field(
                "raw_data",
                "Raw Generation Data",
                str(media.generation_data),
                is_multiline=True,
            )

        self.sections["technical"] = section
        self.content_layout.insertWidget(-1, section)

    def format_file_size(self, size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes = int(size_bytes / 1024)
        return f"{size_bytes:.1f} TB"

    def copy_all_metadata(self):
        if not self.current_media:
            return

        try:
            metadata = {
                "file": {
                    "name": self.current_media.file_name,
                    "path": str(self.current_media.file_path),
                    "size_bytes": self.current_media.file_size,
                    "size_formatted": self.format_file_size(
                        self.current_media.file_size
                    ),
                    "dimensions": {
                        "width": self.current_media.width,
                        "height": self.current_media.height,
                    },
                    "format": self.current_media.format,
                    "created": self.current_media.created_at.isoformat(),
                    "modified": self.current_media.modified_at.isoformat(),
                    "tags": self.current_media.tags,
                },
                "generation": {
                    "source": self.current_media.metadata_source,
                    "model": self.current_media.model,
                    "sampler": self.current_media.sampler,
                    "scheduler": self.current_media.scheduler,
                    "steps": self.current_media.steps,
                    "cfg_scale": self.current_media.cfg_scale,
                    "seed": self.current_media.seed,
                    "prompt": self.current_media.prompt,
                    "negative_prompt": self.current_media.negative_prompt,
                    # LoRAs
                    "loras": [
                        {"lora_name": lora.lora_name, "lora_weight": lora.lora_weight}
                        for lora in self.current_media.loras
                    ],
                    # Video-specific metadata
                    "frame_rate": self.current_media.frame_rate,
                    "duration": self.current_media.duration,
                    "video_length": self.current_media.video_length,
                    "media_type": self.current_media.media_type,
                },
                "raw_generation_data": self.current_media.generation_data,
            }

            formatted_json = json.dumps(metadata, indent=2, ensure_ascii=False)
            clipboard = QApplication.clipboard()
            clipboard.setText(formatted_json)

            self.show_copy_feedback("All metadata copied to clipboard!")

        except Exception as e:
            QMessageBox.warning(
                self, "Copy Error", f"Failed to copy metadata: {str(e)}"
            )

    def show_copy_feedback(self, message: str):
        original_text = self.copy_all_button.text()

        self.copy_all_button.setText("✓ Copied!")

        # Reset after delay
        QTimer.singleShot(1500, lambda: self.copy_all_button.setText(original_text))
