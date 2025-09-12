from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QGridLayout,
    QProgressBar,
    QPushButton,
    QSizeGrip,
    QMessageBox,
    QMenu,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPointF, QUrl
from PyQt6.QtGui import (
    QPixmap,
    QFont,
    QPainter,
    QPen,
    QColor,
    QBrush,
    QPolygon,
    QPolygonF,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from pathlib import Path
from typing import List, Set, Optional, Dict, Tuple
import logging
import platform
import subprocess
from metascan.core.media import Media
from metascan.cache.thumbnail import ThumbnailCache

logger = logging.getLogger(__name__)


class ThumbnailWidget(QLabel):
    """Individual thumbnail widget with selection and hover states."""

    clicked = pyqtSignal(object)  # Emits the Media object
    double_clicked = pyqtSignal(object)  # Emits the Media object for opening
    favorite_toggled = pyqtSignal(
        object
    )  # Emits the Media object when favorite is toggled
    # Context menu signals
    open_requested = pyqtSignal(object)  # Emits Media object for File|Open
    open_folder_requested = pyqtSignal(
        object
    )  # Emits Media object for File|Open Folder
    delete_requested = pyqtSignal(object)  # Emits Media object for File|Delete
    upscale_requested = pyqtSignal(object)  # Emits Media object for File|Upscale
    refresh_metadata_requested = pyqtSignal(object)  # Emits Media object for refresh

    def __init__(
        self,
        media: Media,
        thumbnail_path: Optional[Path] = None,
        parent=None,
        size: Tuple[int, int] = (200, 200),
    ):
        super().__init__(parent)
        self.media = media
        self.thumbnail_path = thumbnail_path
        self.is_selected = False
        self.is_filtered = True  # Whether this thumbnail matches current filters
        self.widget_size = size

        # Set fixed size
        self.setFixedSize(size[0], size[1])
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Let theme handle styling
        self.setFrameStyle(QLabel.Shape.Box)
        self.setLineWidth(2)

        # Set cursor to indicate clickability
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Set tooltip to indicate double-click functionality
        media_type = "video" if media.is_video else "image"
        self.setToolTip(
            f"Click to select • Double-click to open {media_type}\n{media.file_name}"
        )

        # Create favorite star button
        self.star_button = QPushButton(self)
        self.star_button.setFixedSize(24, 24)
        self.star_button.setFlat(True)
        self.star_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star_button.clicked.connect(self.on_star_clicked)
        self.update_star_icon()

        # Position star at bottom-right corner
        self.star_button.move(self.width() - 30, self.height() - 30)
        self.star_button.raise_()  # Ensure star button is above the thumbnail

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
                # Leave 10px margin for border
                scale_size = min(self.widget_size[0] - 10, self.widget_size[1] - 10)
                scaled_pixmap = pixmap.scaled(
                    scale_size,
                    scale_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

                # Add video overlay if this is a video file
                if self.media.is_video:
                    scaled_pixmap = self._add_video_overlay(scaled_pixmap)

                self.setPixmap(scaled_pixmap)
                # Ensure star button stays on top after loading thumbnail
                self.star_button.raise_()
            else:
                self.show_placeholder()
        except Exception as e:
            logger.error(f"Failed to load thumbnail {thumbnail_path}: {e}")
            self.show_placeholder()

    def update_star_icon(self):
        if self.media.is_favorite:
            self.star_button.setText("★")
            pass
            self.star_button.setToolTip("Remove from favorites")
        else:
            self.star_button.setText("☆")
            pass
            self.star_button.setToolTip("Add to favorites")

    def on_star_clicked(self):
        self.media.is_favorite = not self.media.is_favorite
        self.update_star_icon()
        self.favorite_toggled.emit(self.media)

    def show_placeholder(self):
        placeholder_text = f"Loading...\n{self.media.file_name}"
        if self.media.is_video:
            placeholder_text += "\n[VIDEO]"

        self.setText(placeholder_text)
        self.setWordWrap(True)
        self.setStyleSheet(
            self.styleSheet()
            + """
            QLabel {
                color: #666;
                font-size: 11px;
            }
        """
        )

    def _add_video_overlay(self, pixmap: QPixmap) -> QPixmap:
        overlay_pixmap = QPixmap(pixmap.size())
        overlay_pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, pixmap)

        overlay_color = QColor(0, 0, 0, 80)  # Black with 30% opacity
        painter.fillRect(overlay_pixmap.rect(), overlay_color)

        center_x = pixmap.width() // 2
        center_y = pixmap.height() // 2
        button_size = min(pixmap.width(), pixmap.height()) // 4

        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(
            center_x - button_size // 2,
            center_y - button_size // 2,
            button_size,
            button_size,
        )

        triangle_size = button_size // 3
        triangle_offset = triangle_size // 6  # Slight right offset to center visually

        triangle = QPolygonF(
            [
                QPointF(
                    center_x - triangle_size // 2 + triangle_offset,
                    center_y - triangle_size // 2,
                ),
                QPointF(
                    center_x - triangle_size // 2 + triangle_offset,
                    center_y + triangle_size // 2,
                ),
                QPointF(center_x + triangle_size // 2 + triangle_offset, center_y),
            ]
        )

        painter.setBrush(QBrush(QColor(0, 0, 0, 200)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(triangle)

        label_rect = QRect(0, pixmap.height() - 20, pixmap.width(), 20)
        painter.fillRect(label_rect, QColor(0, 0, 0, 150))

        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, "VIDEO")

        painter.end()
        return overlay_pixmap

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_style()

    def set_favorite(self, is_favorite: bool):
        self.media.is_favorite = is_favorite
        self.update_star_icon()

    def set_filtered(self, filtered: bool):
        self.is_filtered = filtered
        # Don't set visibility here - it's handled by grid layout compacting

    def update_style(self):
        if self.is_selected:
            border_color = "#2196F3"
            bg_color = "#e3f2fd"
            border_width = "5px"
        else:
            border_color = "#ddd"
            bg_color = "#f9f9f9"
            border_width = "2px"

        style = f"""
            QLabel {{
                border: {border_width} solid {border_color};
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
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.media)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.media)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """Show context menu on right-click."""
        context_menu = QMenu(self)

        # Open (same as File|Open)
        open_action = context_menu.addAction("Open")
        open_action.triggered.connect(lambda: self.open_requested.emit(self.media))

        # Open Folder (same as File|Open Folder)
        open_folder_action = context_menu.addAction("Open Folder")
        open_folder_action.triggered.connect(
            lambda: self.open_folder_requested.emit(self.media)
        )

        context_menu.addSeparator()

        # Delete file... (same as File|Delete file...)
        delete_action = context_menu.addAction("Delete file...")
        delete_action.triggered.connect(lambda: self.delete_requested.emit(self.media))

        context_menu.addSeparator()

        # Upscale... (same as File|Upscale...)
        upscale_action = context_menu.addAction("Upscale...")
        upscale_action.triggered.connect(
            lambda: self.upscale_requested.emit(self.media)
        )

        context_menu.addSeparator()

        # Refresh Metadata (new functionality)
        refresh_action = context_menu.addAction("Refresh Metadata")
        refresh_action.triggered.connect(
            lambda: self.refresh_metadata_requested.emit(self.media)
        )

        # Show the context menu at the cursor position
        context_menu.exec(event.globalPos())


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
        total = len(self.media_list)

        for i, media in enumerate(self.media_list):
            if self._stop_requested:
                break

            try:
                thumbnail_path = self.thumbnail_cache.get_or_create_thumbnail(
                    media.file_path
                )
                self.thumbnail_ready.emit(media, thumbnail_path)

                # Update progress
                self.progress_updated.emit(i + 1, total)

            except Exception as e:
                logger.error(f"Failed to load thumbnail for {media.file_path}: {e}")
                self.thumbnail_ready.emit(media, None)

    def stop(self):
        self._stop_requested = True


class ThumbnailView(QWidget):
    selection_changed = pyqtSignal(object)  # Emits selected Media object
    favorite_toggled = pyqtSignal(object)  # Emits Media object when favorite is toggled
    # Context menu action signals to forward to main window
    open_requested = pyqtSignal(object)  # Forward Media object for File|Open
    open_folder_requested = pyqtSignal(
        object
    )  # Forward Media object for File|Open Folder
    delete_requested = pyqtSignal(object)  # Forward Media object for File|Delete
    upscale_requested = pyqtSignal(object)  # Forward Media object for File|Upscale
    refresh_metadata_requested = pyqtSignal(object)  # Forward Media object for refresh

    def __init__(self, parent=None, scroll_step: int = 120):
        super().__init__(parent)
        self.container_widget = None
        self.media_list: List[Media] = []
        self.thumbnail_widgets: Dict[str, ThumbnailWidget] = {}  # file_path -> widget
        self.filtered_paths: Optional[Set[str]] = None
        self.selected_media: Optional[Media] = None
        self.selected_index: int = -1  # Track selected index for keyboard navigation
        self.thumbnail_cache: Optional[ThumbnailCache] = None
        self.loader_thread: Optional[ThumbnailLoader] = None
        self.scroll_step = scroll_step

        self.setup_ui()
        self.setup_keyboard_shortcuts()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

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
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 3px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
        """
        )
        main_layout.addWidget(self.progress_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.container_widget = QWidget()
        self.grid_layout = QGridLayout(self.container_widget)
        self.grid_layout.setSpacing(25)  # Increased spacing to prevent overlap
        self.grid_layout.setContentsMargins(10, 10, 10, 10)

        self.scroll_area.setWidget(self.container_widget)
        main_layout.addWidget(self.scroll_area)

        # Set scroll wheel sensitivity
        v_scrollbar = self.scroll_area.verticalScrollBar()
        if v_scrollbar:
            v_scrollbar.setSingleStep(self.scroll_step)

        cache_dir = Path.home() / ".metascan" / "thumbnails"
        self.thumbnail_cache = ThumbnailCache(cache_dir, (200, 200))

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def setup_keyboard_shortcuts(self):
        favorite_shortcut = QShortcut(QKeySequence("F"), self)
        favorite_shortcut.activated.connect(self.toggle_selected_favorite)

        enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        enter_shortcut.activated.connect(self.open_selected_media)

        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space_shortcut.activated.connect(self.open_selected_media)

    def keyPressEvent(self, event):
        if not self.media_list:
            return super().keyPressEvent(event)

        visible_widgets = self.get_visible_widgets()
        if not visible_widgets:
            return super().keyPressEvent(event)

        columns = self.calculate_columns()
        current_index = self.selected_index

        if current_index == -1 and visible_widgets:
            self.select_by_index(0)
            return

        key = event.key()
        new_index = current_index

        if key == Qt.Key.Key_Left:
            if current_index > 0:
                new_index = current_index - 1
        elif key == Qt.Key.Key_Right:
            if current_index < len(visible_widgets) - 1:
                new_index = current_index + 1
        elif key == Qt.Key.Key_Up:
            if current_index >= columns:
                new_index = current_index - columns
        elif key == Qt.Key.Key_Down:
            if current_index + columns < len(visible_widgets):
                new_index = current_index + columns
        elif key == Qt.Key.Key_Home:
            new_index = 0
        elif key == Qt.Key.Key_End:
            new_index = len(visible_widgets) - 1
        else:
            return super().keyPressEvent(event)

        if new_index != current_index and 0 <= new_index < len(visible_widgets):
            self.select_by_index(new_index)

    def get_visible_widgets(self) -> List[ThumbnailWidget]:
        visible = []
        for media in self.media_list:
            widget = self.thumbnail_widgets.get(str(media.file_path))
            if widget and widget.isVisible():
                visible.append(widget)
        return visible

    def select_by_index(self, index: int):
        visible_widgets = self.get_visible_widgets()
        if 0 <= index < len(visible_widgets):
            widget = visible_widgets[index]
            self.selected_index = index
            self.on_thumbnail_clicked(widget.media)

            # Ensure the selected widget is visible in scroll area
            self.scroll_area.ensureWidgetVisible(widget)

    def toggle_selected_favorite(self):
        if self.selected_media:
            widget = self.thumbnail_widgets.get(str(self.selected_media.file_path))
            if widget:
                widget.on_star_clicked()

    def open_selected_media(self):
        if self.selected_media:
            self.on_thumbnail_double_clicked(self.selected_media)

    def set_media_list(self, media_list: List[Media]):
        self.media_list = media_list
        self.clear_thumbnails()

        if media_list:
            self.load_thumbnails()

        self.update_info_label()

    def clear_thumbnails(self):
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.stop()
            self.loader_thread.wait()

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)

        for widget in self.thumbnail_widgets.values():
            widget.setVisible(False)  # Hide before deletion
            widget.setParent(None)  # Remove parent to ensure proper cleanup
            widget.deleteLater()

        self.thumbnail_widgets.clear()
        self.selected_media = None

    def load_thumbnails(self):
        if not self.media_list:
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(self.media_list))
        self.progress_bar.setValue(0)

        self.create_thumbnail_widgets()

        self.loader_thread = ThumbnailLoader(self.media_list, self.thumbnail_cache)
        self.loader_thread.thumbnail_ready.connect(self.on_thumbnail_ready)
        self.loader_thread.progress_updated.connect(self.on_progress_updated)
        self.loader_thread.finished.connect(self.on_loading_finished)
        self.loader_thread.start()

    def create_thumbnail_widgets(self):
        columns = self.calculate_columns()

        self.container_widget.setUpdatesEnabled(False)

        try:
            for i in reversed(range(self.grid_layout.count())):
                child = self.grid_layout.takeAt(i)
                if child.widget():
                    child.widget().deleteLater()

            for i, media in enumerate(self.media_list):
                thumbnail_widget = ThumbnailWidget(media)
                thumbnail_widget.clicked.connect(self.on_thumbnail_clicked)
                thumbnail_widget.double_clicked.connect(
                    self.on_thumbnail_double_clicked
                )
                thumbnail_widget.favorite_toggled.connect(self.on_favorite_toggled)
                # Connect context menu signals
                thumbnail_widget.open_requested.connect(self.open_requested.emit)
                thumbnail_widget.open_folder_requested.connect(
                    self.open_folder_requested.emit
                )
                thumbnail_widget.delete_requested.connect(self.delete_requested.emit)
                thumbnail_widget.upscale_requested.connect(self.upscale_requested.emit)
                thumbnail_widget.refresh_metadata_requested.connect(
                    self.refresh_metadata_requested.emit
                )

                row = i // columns
                col = i % columns
                self.grid_layout.addWidget(thumbnail_widget, row, col)

                self.thumbnail_widgets[str(media.file_path)] = thumbnail_widget

        finally:
            self.container_widget.setUpdatesEnabled(True)

    def calculate_columns(self) -> int:
        available_width = (
            int(self.scroll_area.width()) - 40
        )  # Account for margins/scrollbar
        thumbnail_width = 225  # 200px widget + 15px spacing + border margins
        columns = max(1, available_width // thumbnail_width)
        return min(columns, 6)  # Cap at 6 columns max

    def on_thumbnail_ready(self, media: Media, thumbnail_path: Optional[Path]):
        widget = self.thumbnail_widgets.get(str(media.file_path))
        if widget and thumbnail_path:
            widget.load_thumbnail(thumbnail_path)

    def on_progress_updated(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def on_loading_finished(self):
        self.progress_bar.setVisible(False)
        logger.info(f"Finished loading {len(self.media_list)} thumbnails")

    def on_thumbnail_clicked(self, media: Media):
        if self.selected_media:
            old_widget = self.thumbnail_widgets.get(str(self.selected_media.file_path))
            if old_widget:
                old_widget.set_selected(False)

        self.selected_media = media
        widget = self.thumbnail_widgets.get(str(media.file_path))
        if widget:
            widget.set_selected(True)

            # Update selected index for keyboard navigation
            visible_widgets = self.get_visible_widgets()
            try:
                self.selected_index = visible_widgets.index(widget)
            except ValueError:
                self.selected_index = -1

        self.selection_changed.emit(media)

    def on_favorite_toggled(self, media: Media):
        self.favorite_toggled.emit(media)

    def on_thumbnail_double_clicked(self, media: Media):
        try:
            self.open_media_file(media.file_path)
        except Exception as e:
            logger.error(f"Failed to open media file {media.file_path}: {e}")
            QMessageBox.warning(
                self,
                "Error Opening File",
                f"Could not open {media.file_name}:\n{str(e)}",
            )

    def open_media_file(self, file_path: Path):
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            url = QUrl.fromLocalFile(str(file_path))
            if QDesktopServices.openUrl(url):
                logger.info(f"Opened media file: {file_path}")
                return

            system = platform.system()

            if system == "Darwin":  # macOS
                subprocess.run(["open", str(file_path)], check=True)
            elif system == "Windows":
                subprocess.run(["start", str(file_path)], shell=True, check=True)
            elif system == "Linux":
                subprocess.run(["xdg-open", str(file_path)], check=True)
            else:
                raise OSError(f"Unsupported platform: {system}")

            logger.info(f"Opened media file with system viewer: {file_path}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to open file with system viewer: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error opening file: {e}")

    def apply_filters(self, filtered_paths: Optional[Set[str]]):
        self.filtered_paths = filtered_paths

        # Determine which thumbnails should be visible
        visible_widgets = []

        # If media_list is empty, there are no thumbnails to show
        if not self.media_list:
            # Clear the grid and update info label
            self.reorganize_grid([])
            self.update_info_label(0)
            return

        for media in self.media_list:
            file_path_str = str(media.file_path)
            widget = self.thumbnail_widgets.get(file_path_str)

            if widget:
                # Show widget only if path is in filter set (when filters are active)
                # If filtered_paths is None, show all (no filters applied)
                if filtered_paths is None:
                    should_show = True
                else:
                    should_show = file_path_str in filtered_paths
                widget.set_filtered(should_show)

                if should_show:
                    visible_widgets.append(widget)

        self.reorganize_grid(visible_widgets)

        self.update_info_label(len(visible_widgets))

    def reorganize_grid(self, visible_widgets: List[ThumbnailWidget]):
        columns = self.calculate_columns()

        if self.container_widget:
            self.container_widget.setUpdatesEnabled(False)

        try:
            for widget in self.thumbnail_widgets.values():
                widget.setVisible(False)

            while self.grid_layout.count() > 0:
                item = self.grid_layout.takeAt(0)
                # Don't delete widgets, just remove from layout

            if not visible_widgets:
                logger.debug("No visible widgets to display")
            else:
                for i, widget in enumerate(visible_widgets):
                    row = i // columns
                    col = i % columns

                    if widget.parent() != self.container_widget:
                        widget.setParent(self.container_widget)

                    self.grid_layout.addWidget(widget, row, col)

                    widget.setVisible(True)

                logger.debug(
                    f"Reorganized grid: {len(visible_widgets)} widgets in {columns} columns"
                )

        finally:
            if self.container_widget:
                self.container_widget.setUpdatesEnabled(True)
                self.container_widget.repaint()

    def compact_thumbnail_grid(self, visible_widgets: List):
        self.reorganize_grid(visible_widgets)

    def restore_full_grid(self):
        columns = self.calculate_columns()

        if self.container_widget:
            self.container_widget.setUpdatesEnabled(False)

        try:
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                if item and item.widget():
                    # Hide the widget but don't delete it
                    item.widget().setVisible(False)

            self.grid_layout.invalidate()

            for i, media in enumerate(self.media_list):
                widget = self.thumbnail_widgets.get(str(media.file_path))
                if widget:
                    row = i // columns
                    col = i % columns
                    # Ensure widget isn't already in the layout
                    if widget.parent() != self.container_widget:
                        widget.setParent(self.container_widget)
                    self.grid_layout.addWidget(widget, row, col)
                    widget.setVisible(True)
                    widget.raise_()  # Ensure widget is on top

        finally:
            if self.container_widget:
                self.container_widget.setUpdatesEnabled(True)
                self.container_widget.update()  # Force immediate update

        logger.debug(
            f"Restored full grid: {len(self.media_list)} thumbnails in {columns} columns"
        )

    def update_info_label(self, visible_count: Optional[int] = None):
        if visible_count is None:
            visible_count = len(self.media_list)

        total_count = len(self.media_list)

        if visible_count == total_count:
            self.info_label.setText(f"{total_count} items")
        else:
            self.info_label.setText(f"{visible_count} of {total_count} items")

    def resizeEvent(self, event):
        super().resizeEvent(event)

        if self.thumbnail_widgets:
            QTimer.singleShot(100, self.rearrange_grid)  # Delay to avoid rapid updates

    def rearrange_grid(self):
        if not self.thumbnail_widgets:
            return

        new_columns = self.calculate_columns()
        current_columns = 0

        for i in range(self.grid_layout.columnCount()):
            if self.grid_layout.itemAtPosition(0, i):
                current_columns = i + 1

        if abs(new_columns - current_columns) >= 1:
            self.rearrange_thumbnails(new_columns)

    def rearrange_thumbnails(self, columns: int):
        if hasattr(self, "filtered_paths"):
            self.apply_filters(self.filtered_paths)
        else:
            # No filters have been applied yet - show all
            self.apply_filters(set())
