import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextEdit, QMessageBox,
    QGroupBox, QLineEdit, QApplication
)

from metascan.core.media import Media


class MetadataField(QFrame):
    """Individual metadata field with label, value, and copy button."""

    def __init__(self, label: str, value: str, is_multiline: bool = False, parent=None):
        super().__init__(parent)
        self.label_text = label
        self.value_text = value
        self.is_multiline = is_multiline

        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setStyleSheet("""
            MetadataField {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
                margin: 2px;
            }
            MetadataField:hover {
                background-color: #f0f0f0;
            }
        """)

        self.setup_ui()

    def setup_ui(self):
        """Set up the field UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header with label and copy button
        header_layout = QHBoxLayout()

        # Label
        label = QLabel(self.label_text)
        label_font = QFont()
        label_font.setBold(True)
        label_font.setPointSize(9)
        label.setFont(label_font)
        label.setStyleSheet("color: #333; border: none; background: none;")
        header_layout.addWidget(label)

        header_layout.addStretch()

        # Copy button
        copy_button = QPushButton("📋")
        copy_button.setFixedSize(24, 24)
        copy_button.setToolTip(f"Copy {self.label_text} to clipboard")
        copy_button.setStyleSheet("""
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 3px;
                background-color: #fff;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #e6f3ff;
                border-color: #4CAF50;
            }
            QPushButton:pressed {
                background-color: #cce7ff;
            }
        """)
        copy_button.clicked.connect(self.copy_to_clipboard)
        header_layout.addWidget(copy_button)

        layout.addLayout(header_layout)

        # Value display
        if self.is_multiline:
            self.value_widget = QTextEdit()
            self.value_widget.setPlainText(self.value_text)
            self.value_widget.setReadOnly(True)
            self.value_widget.setMaximumHeight(100)
            self.value_widget.setStyleSheet("""
                QTextEdit {
                    border: 1px solid #ddd;
                    border-radius: 3px;
                    background-color: #fff;
                    font-family: 'Courier New', 'Monaco', 'DejaVu Sans Mono', 'Cascadia Code', monospace;
                    font-size: 10px;
                    padding: 4px;
                }
            """)
        else:
            self.value_widget = QLineEdit()
            self.value_widget.setText(self.value_text)
            self.value_widget.setReadOnly(True)
            self.value_widget.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #ddd;
                    border-radius: 3px;
                    background-color: #fff;
                    font-family: 'Courier New', 'Monaco', 'DejaVu Sans Mono', 'Cascadia Code', monospace;
                    font-size: 10px;
                    padding: 4px;
                }
            """)

        layout.addWidget(self.value_widget)

    def copy_to_clipboard(self):
        """Copy the field value to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.value_text)

        # Show brief visual feedback
        original_style = self.styleSheet()
        self.setStyleSheet(original_style + """
            MetadataField {
                background-color: #d4edda;
                border-color: #28a745;
            }
        """)

        # Reset style after brief delay
        QTimer.singleShot(300, lambda: self.setStyleSheet(original_style))

    def update_value(self, new_value: str):
        """Update the field value."""
        self.value_text = new_value
        if self.is_multiline:
            self.value_widget.setPlainText(new_value)
        else:
            self.value_widget.setText(new_value)


class MetadataSection(QGroupBox):
    """Expandable section for organizing related metadata fields."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.fields = {}
        self.setup_ui()

    def setup_ui(self):
        """Set up the section UI."""
        self.setCheckable(True)
        self.setChecked(True)  # Start expanded
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 1ex;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #2c3e50;
            }
            QGroupBox::indicator {
                width: 13px;
                height: 13px;
            }
            QGroupBox::indicator:checked {
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTMiIGhlaWdodD0iMTMiIHZpZXdCb3g9IjAgMCAxMyAxMyI+PHBhdGggZD0ibTMgNiA0IDQgNC04IiBzdHJva2U9IiM0Q0FGNTAB);
            }
        """)

        # Layout for fields
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 15, 8, 8)
        self.layout.setSpacing(4)

        # Connect collapse/expand
        self.toggled.connect(self.on_toggled)

    def add_field(self, key: str, label: str, value: str, is_multiline: bool = False):
        """Add a metadata field to this section."""
        if value is None:
            value = "N/A"

        field = MetadataField(label, str(value), is_multiline)
        self.layout.addWidget(field)
        self.fields[key] = field
        return field

    def update_field(self, key: str, value: str):
        """Update a field value."""
        if key in self.fields:
            self.fields[key].update_value(str(value) if value is not None else "N/A")

    def on_toggled(self, checked: bool):
        """Handle section expand/collapse."""
        for field in self.fields.values():
            field.setVisible(checked)


class ThumbnailPreview(QLabel):
    """Small thumbnail preview in metadata panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("""
            QLabel {
                border: 2px solid #ddd;
                border-radius: 8px;
                background-color: #f9f9f9;
            }
        """)
        self.setText("No Image")

    def set_thumbnail(self, thumbnail_path: Optional[Path]):
        """Set the thumbnail image."""
        if thumbnail_path and thumbnail_path.exists():
            try:
                pixmap = QPixmap(str(thumbnail_path))
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(
                        116, 116,  # Account for border
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self.setPixmap(scaled_pixmap)
                else:
                    self.setText("Invalid Image")
            except Exception:
                self.setText("Load Error")
        else:
            self.setText("No Preview")


class MetadataPanel(QWidget):
    """Enhanced metadata panel with organized, copyable fields."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_media: Optional[Media] = None
        self.sections = {}
        self.thumbnail_cache = None

        self.setup_ui()

    def setup_ui(self):
        """Set up the metadata panel UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()

        title = QLabel("Metadata")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        title.setFont(title_font)
        title.setStyleSheet("color: #2c3e50; padding: 5px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Copy all button
        self.copy_all_button = QPushButton("📋 Copy All")
        self.copy_all_button.setToolTip("Copy all metadata to clipboard as JSON")
        self.copy_all_button.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
        """)
        self.copy_all_button.clicked.connect(self.copy_all_metadata)
        self.copy_all_button.setEnabled(False)
        header_layout.addWidget(self.copy_all_button)

        main_layout.addLayout(header_layout)

        # Scroll area for metadata content
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Content widget
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        self.content_layout.setSpacing(8)

        # No selection message
        self.no_selection_label = QLabel("Select a media file to view metadata")
        self.no_selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_selection_label.setStyleSheet("""
            QLabel {
                color: #7f8c8d;
                font-size: 12px;
                padding: 20px;
                border: 2px dashed #bdc3c7;
                border-radius: 8px;
            }
        """)
        self.content_layout.addWidget(self.no_selection_label)

        # Add stretch to push content to top
        self.content_layout.addStretch()

        scroll_area.setWidget(self.content_widget)
        main_layout.addWidget(scroll_area)

    def set_thumbnail_cache(self, thumbnail_cache):
        """Set the thumbnail cache for preview images."""
        self.thumbnail_cache = thumbnail_cache

    def display_metadata(self, media: Media):
        """Display metadata for the given media object."""
        self.current_media = media
        self.clear_content()

        # Hide no selection message
        self.no_selection_label.setVisible(False)

        # Create preview section
        self.create_preview_section(media)

        # Create metadata sections
        self.create_prompt_section(media)
        self.create_generation_section(media)
        self.create_file_info_section(media)
        self.create_technical_section(media)

        # Enable copy all button
        self.copy_all_button.setEnabled(True)

    def clear_content(self):
        """Clear all metadata content."""
        # Remove all dynamically created widgets from the layout
        while self.content_layout.count() > 0:
            item = self.content_layout.takeAt(0)
            if item.widget() and item.widget() != self.no_selection_label:
                item.widget().deleteLater()

        # Clear sections dictionary
        self.sections.clear()

        # Re-add the no selection label and stretch
        self.content_layout.addWidget(self.no_selection_label)
        self.content_layout.addStretch()

        # Show no selection message
        self.no_selection_label.setVisible(True)
        self.copy_all_button.setEnabled(False)

    def create_preview_section(self, media: Media):
        """Create the thumbnail preview section."""
        preview_widget = QWidget()
        preview_layout = QHBoxLayout(preview_widget)
        preview_layout.setContentsMargins(8, 8, 8, 8)

        # Thumbnail preview
        thumbnail = ThumbnailPreview()
        if self.thumbnail_cache:
            thumbnail_path = self.thumbnail_cache.get_thumbnail_path(media.file_path)
            thumbnail.set_thumbnail(thumbnail_path)
        preview_layout.addWidget(thumbnail)

        # Basic info
        info_layout = QVBoxLayout()

        filename_label = QLabel(media.file_name)
        filename_font = QFont()
        filename_font.setBold(True)
        filename_font.setPointSize(11)
        filename_label.setFont(filename_font)
        filename_label.setWordWrap(True)
        info_layout.addWidget(filename_label)

        size_label = QLabel(f"{media.width} × {media.height} pixels")
        size_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        info_layout.addWidget(size_label)

        # Format label with media type
        media_type = "VIDEO" if media.is_video else "IMAGE"
        format_label = QLabel(f"{media.format} • {media_type} • {self.format_file_size(media.file_size)}")
        format_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
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
                video_info_label.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")  # Red for video
                info_layout.addWidget(video_info_label)

        info_layout.addStretch()
        preview_layout.addLayout(info_layout)

        preview_layout.addStretch()

        # Insert at top
        self.content_layout.insertWidget(0, preview_widget)

    def create_file_info_section(self, media: Media):
        """Create the file information section."""
        section = MetadataSection("📁 File Information")

        section.add_field("filename", "Filename", media.file_name)
        section.add_field("filepath", "File Path", str(media.file_path), is_multiline=True)
        section.add_field("filesize", "File Size", self.format_file_size(media.file_size))
        section.add_field("dimensions", "Dimensions", f"{media.width} × {media.height}")
        section.add_field("format", "Format", media.format)
        section.add_field("created", "Created", media.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        section.add_field("modified", "Modified", media.modified_at.strftime("%Y-%m-%d %H:%M:%S"))

        if media.tags:
            section.add_field("tags", "Tags", ", ".join(media.tags))

        self.sections["file_info"] = section
        self.content_layout.insertWidget(-1, section)  # Before stretch

    def create_generation_section(self, media: Media):
        """Create the AI generation parameters section."""
        section_title = "🎥 Video Generation Parameters" if media.is_video else "🤖 Generation Parameters"
        section = MetadataSection(section_title)

        section.add_field("source", "Source", media.metadata_source or "Unknown")
        section.add_field("model", "Model", media.model or "Unknown")
        section.add_field("sampler", "Sampler", media.sampler or "Unknown")
        section.add_field("scheduler", "Scheduler", media.scheduler or "Unknown")
        section.add_field("steps", "Steps", str(media.steps) if media.steps else "Unknown")
        section.add_field("cfg_scale", "CFG Scale", str(media.cfg_scale) if media.cfg_scale else "Unknown")
        section.add_field("seed", "Seed", str(media.seed) if media.seed else "Unknown")

        # Add video-specific fields
        if media.is_video:
            if media.frame_rate:
                section.add_field("frame_rate", "Frame Rate", f"{media.frame_rate:.1f} fps")
            if media.duration:
                section.add_field("duration", "Duration", f"{media.duration:.2f} seconds")
            if media.video_length:
                section.add_field("video_length", "Frame Count", str(media.video_length))

        self.sections["generation"] = section
        self.content_layout.insertWidget(-1, section)

    def create_prompt_section(self, media: Media):
        """Create the prompt section."""
        section = MetadataSection("✨ Prompts")

        if media.prompt:
            section.add_field("prompt", "Positive Prompt", media.prompt, is_multiline=True)

        if media.negative_prompt:
            section.add_field("negative_prompt", "Negative Prompt", media.negative_prompt, is_multiline=True)

        # Only add section if there are prompts
        if media.prompt or media.negative_prompt:
            self.sections["prompts"] = section
            self.content_layout.insertWidget(-1, section)

    def create_technical_section(self, media: Media):
        """Create the technical/raw data section."""
        if not media.generation_data:
            return

        section = MetadataSection("⚙️ Technical Data")
        section.setChecked(False)  # Start collapsed

        # Format generation data as JSON
        try:
            formatted_data = json.dumps(media.generation_data, indent=2, sort_keys=True)
            section.add_field("raw_data", "Raw Generation Data", formatted_data, is_multiline=True)
        except Exception:
            section.add_field("raw_data", "Raw Generation Data", str(media.generation_data), is_multiline=True)

        self.sections["technical"] = section
        self.content_layout.insertWidget(-1, section)

    def format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def copy_all_metadata(self):
        """Copy all metadata to clipboard as structured JSON."""
        if not self.current_media:
            return

        try:
            # Create comprehensive metadata dict
            metadata = {
                "file": {
                    "name": self.current_media.file_name,
                    "path": str(self.current_media.file_path),
                    "size_bytes": self.current_media.file_size,
                    "size_formatted": self.format_file_size(self.current_media.file_size),
                    "dimensions": {
                        "width": self.current_media.width,
                        "height": self.current_media.height
                    },
                    "format": self.current_media.format,
                    "created": self.current_media.created_at.isoformat(),
                    "modified": self.current_media.modified_at.isoformat(),
                    "tags": self.current_media.tags
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
                    # Video-specific metadata
                    "frame_rate": self.current_media.frame_rate,
                    "duration": self.current_media.duration,
                    "video_length": self.current_media.video_length,
                    "media_type": self.current_media.media_type
                },
                "raw_generation_data": self.current_media.generation_data
            }

            # Copy to clipboard
            formatted_json = json.dumps(metadata, indent=2, ensure_ascii=False)
            clipboard = QApplication.clipboard()
            clipboard.setText(formatted_json)

            # Show success message
            self.show_copy_feedback("All metadata copied to clipboard!")

        except Exception as e:
            QMessageBox.warning(self, "Copy Error", f"Failed to copy metadata: {str(e)}")

    def show_copy_feedback(self, message: str):
        """Show brief feedback when copying."""
        # Change button appearance briefly
        original_text = self.copy_all_button.text()
        original_style = self.copy_all_button.styleSheet()

        self.copy_all_button.setText("✓ Copied!")
        self.copy_all_button.setStyleSheet(original_style + """
            QPushButton {
                background-color: #27ae60;
            }
        """)

        # Reset after delay
        QTimer.singleShot(1500, lambda: (
            self.copy_all_button.setText(original_text),
            self.copy_all_button.setStyleSheet(original_style)
        ))
