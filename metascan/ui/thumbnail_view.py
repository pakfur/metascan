from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QGridLayout, QProgressBar, QPushButton, QSizeGrip
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPointF
from PyQt6.QtGui import QPixmap, QFont, QPainter, QPen, QColor, QBrush, QPolygon, QPolygonF
from pathlib import Path
from typing import List, Set, Optional, Dict
import logging
from metascan.core.media import Media
from metascan.cache.thumbnail import ThumbnailCache

logger = logging.getLogger(__name__)


class ThumbnailWidget(QLabel):
    """Individual thumbnail widget with selection and hover states."""
    
    clicked = pyqtSignal(object)  # Emits the Media object
    
    def __init__(self, media: Media, thumbnail_path: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self.media = media
        self.thumbnail_path = thumbnail_path
        self.is_selected = False
        self.is_filtered = True  # Whether this thumbnail matches current filters
        
        # Set fixed size
        self.setFixedSize(200, 200)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("""
            QLabel {
                border: 2px solid #ddd;
                background-color: #f9f9f9;
                border-radius: 4px;
            }
            QLabel:hover {
                border-color: #4CAF50;
                background-color: #f0f8f0;
            }
        """)
        
        # Load thumbnail or show placeholder
        if thumbnail_path and thumbnail_path.exists():
            self.load_thumbnail(thumbnail_path)
        else:
            self.show_placeholder()
    
    def load_thumbnail(self, thumbnail_path: Path):
        """Load thumbnail image from path."""
        try:
            pixmap = QPixmap(str(thumbnail_path))
            if not pixmap.isNull():
                # Scale to fit within widget while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    190, 190,  # Leave margin for border
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                
                # Add video overlay if this is a video file
                if self.media.is_video:
                    scaled_pixmap = self._add_video_overlay(scaled_pixmap)
                
                self.setPixmap(scaled_pixmap)
            else:
                self.show_placeholder()
        except Exception as e:
            logger.error(f"Failed to load thumbnail {thumbnail_path}: {e}")
            self.show_placeholder()
    
    def show_placeholder(self):
        """Show placeholder text when thumbnail is not available."""
        placeholder_text = f"Loading...\n{self.media.file_name}"
        if self.media.is_video:
            placeholder_text += "\n[VIDEO]"
        
        self.setText(placeholder_text)
        self.setWordWrap(True)
        self.setStyleSheet(self.styleSheet() + """
            QLabel {
                color: #666;
                font-size: 11px;
            }
        """)
    
    def _add_video_overlay(self, pixmap: QPixmap) -> QPixmap:
        """Add a play button overlay to video thumbnails."""
        # Create a new pixmap to draw on
        overlay_pixmap = QPixmap(pixmap.size())
        overlay_pixmap.fill(Qt.GlobalColor.transparent)
        
        # Copy the original pixmap
        painter = QPainter(overlay_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, pixmap)
        
        # Draw semi-transparent overlay
        overlay_color = QColor(0, 0, 0, 80)  # Black with 30% opacity
        painter.fillRect(overlay_pixmap.rect(), overlay_color)
        
        # Calculate play button position and size
        center_x = pixmap.width() // 2
        center_y = pixmap.height() // 2
        button_size = min(pixmap.width(), pixmap.height()) // 4
        
        # Draw play button background (circle)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(
            center_x - button_size//2,
            center_y - button_size//2,
            button_size,
            button_size
        )
        
        # Draw play triangle
        triangle_size = button_size // 3
        triangle_offset = triangle_size // 6  # Slight right offset to center visually
        
        triangle = QPolygonF([
            QPointF(center_x - triangle_size//2 + triangle_offset, center_y - triangle_size//2),
            QPointF(center_x - triangle_size//2 + triangle_offset, center_y + triangle_size//2),
            QPointF(center_x + triangle_size//2 + triangle_offset, center_y)
        ])
        
        painter.setBrush(QBrush(QColor(0, 0, 0, 200)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(triangle)
        
        # Add "VIDEO" label at bottom
        label_rect = QRect(0, pixmap.height() - 20, pixmap.width(), 20)
        painter.fillRect(label_rect, QColor(0, 0, 0, 150))
        
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, "VIDEO")
        
        painter.end()
        return overlay_pixmap
    
    def set_selected(self, selected: bool):
        """Set the selection state of this thumbnail."""
        self.is_selected = selected
        self.update_style()
    
    def set_filtered(self, filtered: bool):
        """Set whether this thumbnail matches current filters."""
        self.is_filtered = filtered
        self.setVisible(filtered)
    
    def update_style(self):
        """Update the visual style based on state."""
        if self.is_selected:
            border_color = "#2196F3"
            bg_color = "#e3f2fd"
        else:
            border_color = "#ddd"
            bg_color = "#f9f9f9"
        
        style = f"""
            QLabel {{
                border: 2px solid {border_color};
                background-color: {bg_color};
                border-radius: 4px;
            }}
            QLabel:hover {{
                border-color: #4CAF50;
                background-color: #f0f8f0;
            }}
        """
        
        self.setStyleSheet(style)
    
    def mousePressEvent(self, event):
        """Handle mouse clicks."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.media)
        super().mousePressEvent(event)


class ThumbnailLoader(QThread):
    """Background thread for loading thumbnails."""
    
    thumbnail_ready = pyqtSignal(object, object)  # media, thumbnail_path
    progress_updated = pyqtSignal(int, int)  # current, total
    
    def __init__(self, media_list: List[Media], thumbnail_cache: ThumbnailCache):
        super().__init__()
        self.media_list = media_list
        self.thumbnail_cache = thumbnail_cache
        self._stop_requested = False
    
    def run(self):
        """Load thumbnails in background."""
        total = len(self.media_list)
        
        for i, media in enumerate(self.media_list):
            if self._stop_requested:
                break
            
            try:
                # Get or create thumbnail
                thumbnail_path = self.thumbnail_cache.get_or_create_thumbnail(media.file_path)
                self.thumbnail_ready.emit(media, thumbnail_path)
                
                # Update progress
                self.progress_updated.emit(i + 1, total)
                
            except Exception as e:
                logger.error(f"Failed to load thumbnail for {media.file_path}: {e}")
                self.thumbnail_ready.emit(media, None)
    
    def stop(self):
        """Request the thread to stop."""
        self._stop_requested = True


class ThumbnailView(QWidget):
    """Main thumbnail view widget with grid layout and filtering."""
    
    selection_changed = pyqtSignal(object)  # Emits selected Media object
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.media_list: List[Media] = []
        self.thumbnail_widgets: Dict[str, ThumbnailWidget] = {}  # file_path -> widget
        self.filtered_paths: Set[str] = set()
        self.selected_media: Optional[Media] = None
        self.thumbnail_cache: Optional[ThumbnailCache] = None
        self.loader_thread: Optional[ThumbnailLoader] = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # Header with title and info
        header_layout = QHBoxLayout()
        
        self.title_label = QLabel("Media Gallery")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        self.title_label.setFont(title_font)
        header_layout.addWidget(self.title_label)
        
        header_layout.addStretch()
        
        self.info_label = QLabel("0 items")
        self.info_label.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(self.info_label)
        
        main_layout.addLayout(header_layout)
        
        # Progress bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 3px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
        """)
        main_layout.addWidget(self.progress_bar)
        
        # Scroll area for thumbnails
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Container widget for thumbnails
        self.container_widget = QWidget()
        self.grid_layout = QGridLayout(self.container_widget)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        
        self.scroll_area.setWidget(self.container_widget)
        main_layout.addWidget(self.scroll_area)
        
        # Initialize thumbnail cache
        cache_dir = Path.home() / ".metascan" / "thumbnails"
        self.thumbnail_cache = ThumbnailCache(cache_dir, (200, 200))
    
    def set_media_list(self, media_list: List[Media]):
        """Set the list of media to display."""
        self.media_list = media_list
        self.clear_thumbnails()
        
        if media_list:
            self.load_thumbnails()
        
        self.update_info_label()
    
    def clear_thumbnails(self):
        """Clear all thumbnail widgets."""
        # Stop any running loader
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.stop()
            self.loader_thread.wait()
        
        # Remove all widgets from grid
        for widget in self.thumbnail_widgets.values():
            widget.deleteLater()
        
        self.thumbnail_widgets.clear()
        self.selected_media = None
        
        # Clear the grid layout
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def load_thumbnails(self):
        """Load thumbnails for current media list."""
        if not self.media_list:
            return
        
        # Show progress bar
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.media_list))
        self.progress_bar.setValue(0)
        
        # Create placeholder widgets first for immediate feedback
        self.create_thumbnail_widgets()
        
        # Start background loading
        self.loader_thread = ThumbnailLoader(self.media_list, self.thumbnail_cache)
        self.loader_thread.thumbnail_ready.connect(self.on_thumbnail_ready)
        self.loader_thread.progress_updated.connect(self.on_progress_updated)
        self.loader_thread.finished.connect(self.on_loading_finished)
        self.loader_thread.start()
    
    def create_thumbnail_widgets(self):
        """Create thumbnail widgets and arrange them in grid."""
        columns = self.calculate_columns()
        
        for i, media in enumerate(self.media_list):
            # Create thumbnail widget with placeholder
            thumbnail_widget = ThumbnailWidget(media)
            thumbnail_widget.clicked.connect(self.on_thumbnail_clicked)
            
            # Add to grid
            row = i // columns
            col = i % columns
            self.grid_layout.addWidget(thumbnail_widget, row, col)
            
            # Store reference
            self.thumbnail_widgets[str(media.file_path)] = thumbnail_widget
    
    def calculate_columns(self) -> int:
        """Calculate optimal number of columns based on available width."""
        available_width = self.scroll_area.width() - 40  # Account for margins/scrollbar
        thumbnail_width = 220  # 200px + margins
        columns = max(1, available_width // thumbnail_width)
        return min(columns, 6)  # Cap at 6 columns max
    
    def on_thumbnail_ready(self, media: Media, thumbnail_path: Optional[Path]):
        """Handle when a thumbnail is ready."""
        widget = self.thumbnail_widgets.get(str(media.file_path))
        if widget and thumbnail_path:
            widget.load_thumbnail(thumbnail_path)
    
    def on_progress_updated(self, current: int, total: int):
        """Handle progress updates."""
        self.progress_bar.setValue(current)
    
    def on_loading_finished(self):
        """Handle when thumbnail loading is complete."""
        self.progress_bar.setVisible(False)
        logger.info(f"Finished loading {len(self.media_list)} thumbnails")
    
    def on_thumbnail_clicked(self, media: Media):
        """Handle thumbnail selection."""
        # Clear previous selection
        if self.selected_media:
            old_widget = self.thumbnail_widgets.get(str(self.selected_media.file_path))
            if old_widget:
                old_widget.set_selected(False)
        
        # Set new selection
        self.selected_media = media
        widget = self.thumbnail_widgets.get(str(media.file_path))
        if widget:
            widget.set_selected(True)
        
        # Emit selection change
        self.selection_changed.emit(media)
    
    def apply_filters(self, filtered_paths: Set[str]):
        """Apply filters to show/hide thumbnails."""
        self.filtered_paths = filtered_paths
        
        visible_count = 0
        for file_path, widget in self.thumbnail_widgets.items():
            # Show if no filters or path matches filters
            is_visible = not filtered_paths or file_path in filtered_paths
            widget.set_filtered(is_visible)
            if is_visible:
                visible_count += 1
        
        self.update_info_label(visible_count)
    
    def update_info_label(self, visible_count: Optional[int] = None):
        """Update the info label with item counts."""
        if visible_count is None:
            visible_count = len(self.media_list)
        
        total_count = len(self.media_list)
        
        if visible_count == total_count:
            self.info_label.setText(f"{total_count} items")
        else:
            self.info_label.setText(f"{visible_count} of {total_count} items")
    
    def resizeEvent(self, event):
        """Handle resize events to adjust grid layout."""
        super().resizeEvent(event)
        
        # Recalculate columns and rearrange if needed
        if self.thumbnail_widgets:
            QTimer.singleShot(100, self.rearrange_grid)  # Delay to avoid rapid updates
    
    def rearrange_grid(self):
        """Rearrange thumbnails in grid based on current width."""
        if not self.thumbnail_widgets:
            return
        
        new_columns = self.calculate_columns()
        current_columns = 0
        
        # Count current columns by checking first row
        for i in range(self.grid_layout.columnCount()):
            if self.grid_layout.itemAtPosition(0, i):
                current_columns = i + 1
        
        # Only rearrange if columns changed significantly
        if abs(new_columns - current_columns) >= 1:
            self.rearrange_thumbnails(new_columns)
    
    def rearrange_thumbnails(self, columns: int):
        """Rearrange existing thumbnails into new grid layout."""
        # Get all widgets
        widgets = []
        for file_path in [str(media.file_path) for media in self.media_list]:
            widget = self.thumbnail_widgets.get(file_path)
            if widget:
                widgets.append(widget)
        
        # Remove all from layout
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
        
        # Re-add in new arrangement
        for i, widget in enumerate(widgets):
            row = i // columns
            col = i % columns
            self.grid_layout.addWidget(widget, row, col)