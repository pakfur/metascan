"""
Virtual Thumbnail View for PyQt6

A high-performance virtualized thumbnail grid view that can efficiently handle 
thousands of thumbnails by only creating widgets for visible items and reusing 
them as the user scrolls.

This implementation provides:
- Virtual scrolling with object pooling
- Smooth scrolling with preloading buffer
- Dynamic loading/unloading of thumbnails
- Selection state management across virtual widgets
- Window resizing efficiency
- All filtering and favorite features from the original implementation
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QFrame,
    QButtonGroup,
)
from PyQt6.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    QTimer,
    QSize,
    QRect,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QObject,
)
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
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
    QMouseEvent,
)
from pathlib import Path
from typing import List, Set, Optional, Dict, Tuple, Union, Deque
import logging
import platform
import subprocess
import math
from dataclasses import dataclass, field
from collections import deque

from metascan.core.media import Media
from metascan.cache.thumbnail import ThumbnailCache
from metascan.ui.thumbnail_view import ThumbnailWidget, ThumbnailLoader

logger = logging.getLogger(__name__)


@dataclass
class ViewportInfo:
    """Information about the current viewport state."""

    scroll_y: int = 0
    visible_height: int = 0
    total_height: int = 0
    first_visible_row: int = 0
    last_visible_row: int = 0
    buffer_rows: int = 2  # Extra rows to preload above/below visible area


@dataclass
class LayoutMetrics:
    """Grid layout calculation metrics."""

    columns: int = 1
    rows: int = 0
    item_width: int = 200
    item_height: int = 200
    horizontal_spacing: int = 10
    vertical_spacing: int = 10
    margins: Tuple[int, int, int, int] = field(
        default_factory=lambda: (10, 10, 10, 10)
    )  # left, top, right, bottom

    @property
    def cell_width(self) -> int:
        """Total width per cell including spacing."""
        return self.item_width + self.horizontal_spacing

    @property
    def cell_height(self) -> int:
        """Total height per cell including spacing."""
        return self.item_height + self.vertical_spacing


class WidgetPool:
    """Object pool for reusing ThumbnailWidget instances efficiently."""

    def __init__(self, parent: QWidget, initial_size: int = 20, thumbnail_size: Tuple[int, int] = (200, 200)):
        """
        Initialize the widget pool.

        Args:
            parent: Parent widget for the pooled widgets
            initial_size: Initial number of widgets to create in the pool
            thumbnail_size: Size of thumbnails (width, height)
        """
        self.parent = parent
        self.thumbnail_size = thumbnail_size
        self._available: Deque[ThumbnailWidget] = deque()
        self._in_use: Dict[str, ThumbnailWidget] = {}  # media file_path -> widget

        # Pre-create initial widgets
        self._create_widgets(initial_size)

    def _create_widgets(self, count: int) -> None:
        """Create new widgets and add them to the available pool."""
        for _ in range(count):
            # Create a dummy media object for initialization
            from metascan.core.media import Media
            from datetime import datetime

            dummy_media = Media(
                file_path=Path("dummy"),
                file_size=0,
                width=0,
                height=0,
                format="",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )

            widget = ThumbnailWidget(dummy_media, parent=self.parent, size=self.thumbnail_size)
            widget.setVisible(False)
            self._available.append(widget)

    def acquire(self, media: Media) -> ThumbnailWidget:
        """
        Acquire a widget from the pool for the given media.

        Args:
            media: Media object to associate with the widget

        Returns:
            ThumbnailWidget ready for use
        """
        media_key = str(media.file_path)

        # If already in use, return existing widget
        if media_key in self._in_use:
            return self._in_use[media_key]

        # Get a widget from the pool
        if not self._available:
            self._create_widgets(10)  # Create more if pool is empty

        widget = self._available.popleft()

        # Update the widget for this media
        self._configure_widget(widget, media)

        # Track as in use
        self._in_use[media_key] = widget

        return widget

    def release(self, media: Media) -> None:
        """
        Release a widget back to the pool.

        Args:
            media: Media object whose widget should be released
        """
        media_key = str(media.file_path)

        if media_key in self._in_use:
            widget = self._in_use.pop(media_key)
            widget.setVisible(False)
            widget.set_selected(False)
            # Clear any thumbnail to save memory
            widget.clear()
            self._available.append(widget)

    def _configure_widget(self, widget: ThumbnailWidget, media: Media) -> None:
        """Configure a pooled widget for new media."""
        # Update the widget's media reference
        widget.media = media
        widget.thumbnail_path = None
        widget.is_selected = False
        widget.is_filtered = True

        # Update tooltip
        media_type = "video" if media.is_video else "image"
        widget.setToolTip(
            f"Click to select • Double-click to open {media_type}\n{media.file_name}"
        )

        # Update star button
        widget.update_star_icon()

        # Show placeholder initially
        widget.show_placeholder()

        # Reset selection state
        widget.set_selected(False)

    def clear_all(self) -> None:
        """Release all widgets and clear the pool."""
        # Move all in-use widgets back to available
        for widget in self._in_use.values():
            widget.setVisible(False)
            widget.set_selected(False)
            self._available.append(widget)

        self._in_use.clear()

    def get_in_use_count(self) -> int:
        """Get the number of widgets currently in use."""
        return len(self._in_use)

    def get_available_count(self) -> int:
        """Get the number of widgets available in the pool."""
        return len(self._available)


class VirtualScrollArea(QScrollArea):
    """Custom scroll area that handles virtual rendering of thumbnail grid."""

    # Signals
    item_clicked = pyqtSignal(object)  # Emits Media object
    item_double_clicked = pyqtSignal(object)  # Emits Media object
    favorite_toggled = pyqtSignal(object)  # Emits Media object
    selection_changed = pyqtSignal(object)  # Emits Media object

    def __init__(self, parent: Optional[QWidget] = None, thumbnail_size: Optional[Tuple[int, int]] = None):
        super().__init__(parent)

        # Data
        self.media_list: List[Media] = []
        self.filtered_media: List[Media] = []  # Current filtered subset
        self.selected_media: Optional[Media] = None
        self.selected_index: int = -1

        # Layout and viewport
        self.layout_metrics = LayoutMetrics()
        if thumbnail_size:
            self.layout_metrics.item_width = thumbnail_size[0]
            self.layout_metrics.item_height = thumbnail_size[1]
            # Keep spacing consistent regardless of thumbnail size for a tight grid
            self.layout_metrics.horizontal_spacing = 10
            self.layout_metrics.vertical_spacing = 10
        self.viewport_info = ViewportInfo()

        # Widget management
        viewport = self.viewport()
        if viewport is None:
            # Fallback - should not happen in normal operation
            viewport = QWidget()
        thumb_size = (self.layout_metrics.item_width, self.layout_metrics.item_height)
        self.widget_pool = WidgetPool(viewport, thumbnail_size=thumb_size)
        self.visible_widgets: Dict[int, ThumbnailWidget] = {}  # item_index -> widget

        # Thumbnail loading
        self.thumbnail_cache: Optional[ThumbnailCache] = None
        self.loader_thread: Optional[ThumbnailLoader] = None

        # Performance optimization
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._update_viewport)

        # Smooth scrolling
        self.scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value")
        self.scroll_animation.setDuration(200)
        self.scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._setup_scroll_area()

    def _setup_scroll_area(self) -> None:
        """Setup the scroll area properties."""
        # Create container widget for the scroll area
        self.container_widget = QWidget()
        self.setWidget(self.container_widget)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWidgetResizable(False)

        # Enable mouse tracking for hover effects
        viewport = self.viewport()
        if viewport:
            viewport.setMouseTracking(True)

        # Connect scroll bar signals
        vscroll = self.verticalScrollBar()
        if vscroll:
            vscroll.valueChanged.connect(self._on_scroll)

        # Initialize thumbnail cache with correct size
        cache_dir = Path.home() / ".metascan" / "thumbnails"
        thumb_size = (self.layout_metrics.item_width, self.layout_metrics.item_height)
        self.thumbnail_cache = ThumbnailCache(cache_dir, thumb_size)

    def set_media_list(self, media_list: List[Media]) -> None:
        """
        Set the media list for display.

        Args:
            media_list: List of Media objects to display
        """
        logger.info(f"Setting media list with {len(media_list)} items")

        # Clear existing state
        self._clear_viewport()

        # Set new data
        self.media_list = media_list
        self.filtered_media = media_list.copy()  # Initially show all
        self.selected_media = None
        self.selected_index = -1

        # Recalculate layout
        self._calculate_layout()
        self._update_scroll_range()

        # Update viewport
        self._update_viewport()

    def apply_filters(self, filtered_paths: Set[str]) -> None:
        """
        Apply filters to the media list.

        Args:
            filtered_paths: Set of file paths to show (empty set shows all)
        """
        logger.debug(f"Applying filters: {len(filtered_paths)} paths")

        if not filtered_paths:
            # No filter - show all
            self.filtered_media = self.media_list.copy()
        else:
            # Filter media list
            self.filtered_media = [
                media
                for media in self.media_list
                if str(media.file_path) in filtered_paths
            ]

        # Clear selection if the selected item is no longer visible
        if self.selected_media and str(self.selected_media.file_path) not in {
            str(m.file_path) for m in self.filtered_media
        }:
            self.selected_media = None
            self.selected_index = -1

        # Recalculate layout and update
        self._calculate_layout()
        self._update_scroll_range()
        self._clear_viewport()
        self._update_viewport()

    def _calculate_layout(self) -> None:
        """Calculate grid layout metrics based on current size and media count."""
        # For a QScrollArea, we want the width of the scroll area itself, not the viewport
        # The viewport is the visible area, but we want to layout based on the full width
        scroll_area_width = self.width()
        
        # Account for vertical scrollbar width if it's shown
        vscrollbar = self.verticalScrollBar()
        scrollbar_width = vscrollbar.width() if vscrollbar and vscrollbar.isVisible() else 0
        
        usable_width = scroll_area_width - scrollbar_width
        
        # Calculate optimal columns
        available_width = (
            usable_width
            - self.layout_metrics.margins[0]
            - self.layout_metrics.margins[2]
        )
        columns = max(1, available_width // self.layout_metrics.cell_width)
        

        # Calculate rows needed
        item_count = len(self.filtered_media)
        rows = math.ceil(item_count / columns) if item_count > 0 else 0

        # Update metrics
        self.layout_metrics.columns = columns
        self.layout_metrics.rows = rows

        logger.debug(f"Layout calculated: {columns}x{rows} grid for {item_count} items, width={scroll_area_width}, cell_width={self.layout_metrics.cell_width}")

    def _update_scroll_range(self) -> None:
        """Update the container widget size to enable scrolling."""
        if self.layout_metrics.rows == 0:
            total_height = 0
        else:
            total_height = (
                self.layout_metrics.margins[1]
                + (  # top margin
                    self.layout_metrics.rows * self.layout_metrics.cell_height
                )
                + self.layout_metrics.margins[3]  # bottom margin
            )

        # Set container widget size - this enables scrolling in QScrollArea
        viewport = self.viewport()
        viewport_width = viewport.width() if viewport else 800  # fallback
        viewport_height = viewport.height() if viewport else 600  # fallback
        self.container_widget.setFixedSize(viewport_width, total_height)

        # Update viewport info
        self.viewport_info.total_height = total_height
        self.viewport_info.visible_height = viewport_height

        logger.debug(f"Container size updated: {viewport_width}x{total_height}")

    def _on_scroll(self, value: int) -> None:
        """Handle scroll bar value changes."""
        vscroll = self.verticalScrollBar()
        if vscroll:
            self.viewport_info.scroll_y = vscroll.value()
        # Throttle viewport updates for performance
        if not self.update_timer.isActive():
            self.update_timer.start(16)  # ~60 FPS

    def _update_viewport(self) -> None:
        """Update the viewport to show the correct widgets."""
        if not self.filtered_media:
            self._clear_viewport()
            return

        # Calculate which rows are visible
        scroll_y = self.viewport_info.scroll_y
        visible_height = self.viewport_info.visible_height
        cell_height = self.layout_metrics.cell_height
        buffer_rows = self.viewport_info.buffer_rows

        # Determine visible row range with buffer
        first_visible_row = max(
            0, (scroll_y - self.layout_metrics.margins[1]) // cell_height - buffer_rows
        )
        last_visible_row = min(
            self.layout_metrics.rows - 1,
            (
                (scroll_y + visible_height - self.layout_metrics.margins[1])
                // cell_height
            )
            + buffer_rows,
        )

        # Update viewport info
        self.viewport_info.first_visible_row = first_visible_row
        self.viewport_info.last_visible_row = last_visible_row

        # Calculate which items should be visible
        columns = self.layout_metrics.columns
        first_item = first_visible_row * columns
        last_item = min(
            len(self.filtered_media) - 1, (last_visible_row + 1) * columns - 1
        )

        needed_indices = set(range(first_item, last_item + 1))
        current_indices = set(self.visible_widgets.keys())

        # Remove widgets that are no longer needed
        for index in current_indices - needed_indices:
            self._hide_widget_at_index(index)

        # Add widgets that are now needed
        for index in needed_indices - current_indices:
            self._show_widget_at_index(index)

        # Update widget positions
        self._position_visible_widgets()

        logger.debug(
            f"Viewport updated: rows {first_visible_row}-{last_visible_row}, "
            f"items {first_item}-{last_item}, {len(self.visible_widgets)} widgets visible"
        )

    def _show_widget_at_index(self, index: int) -> None:
        """Show a widget for the media at the given index."""
        if index < 0 or index >= len(self.filtered_media):
            return

        media = self.filtered_media[index]
        widget = self.widget_pool.acquire(media)

        # Ensure correct parent
        if widget.parent() != self.container_widget:
            widget.setParent(self.container_widget)

        # Connect signals
        widget.clicked.connect(self._on_widget_clicked)
        widget.double_clicked.connect(self._on_widget_double_clicked)
        widget.favorite_toggled.connect(self._on_favorite_toggled)

        # Set selection state
        widget.set_selected(media == self.selected_media)

        # Position widget
        self._position_widget(widget, index)

        # Show widget
        widget.setVisible(True)
        widget.raise_()

        # Store reference
        self.visible_widgets[index] = widget

        # Load thumbnail if needed
        self._load_thumbnail_for_widget(widget)

    def _hide_widget_at_index(self, index: int) -> None:
        """Hide the widget at the given index."""
        if index in self.visible_widgets:
            widget = self.visible_widgets.pop(index)
            media = (
                self.filtered_media[index] if index < len(self.filtered_media) else None
            )

            if media:
                # Disconnect signals to prevent memory leaks
                try:
                    widget.clicked.disconnect()
                    widget.double_clicked.disconnect()
                    widget.favorite_toggled.disconnect()
                except TypeError:
                    pass  # Signals were not connected

                # Release back to pool
                self.widget_pool.release(media)

    def _position_widget(self, widget: ThumbnailWidget, index: int) -> None:
        """Position a widget based on its grid index."""
        columns = self.layout_metrics.columns
        row = index // columns
        col = index % columns

        x = self.layout_metrics.margins[0] + col * self.layout_metrics.cell_width
        y = self.layout_metrics.margins[1] + row * self.layout_metrics.cell_height

        widget.setGeometry(
            x, y, self.layout_metrics.item_width, self.layout_metrics.item_height
        )

    def _position_visible_widgets(self) -> None:
        """Update positions of all visible widgets."""
        for index, widget in self.visible_widgets.items():
            self._position_widget(widget, index)

    def _load_thumbnail_for_widget(self, widget: ThumbnailWidget) -> None:
        """Load thumbnail for a widget if not already loaded."""
        if not widget.pixmap() or widget.pixmap().isNull():
            # Check if thumbnail exists in cache
            if self.thumbnail_cache:
                thumbnail_path = self.thumbnail_cache.get_thumbnail_path(
                    widget.media.file_path
                )
                if thumbnail_path and thumbnail_path.exists():
                    widget.load_thumbnail(thumbnail_path)
                else:
                    # Request thumbnail generation
                    self._request_thumbnail_generation(widget.media)

    def _request_thumbnail_generation(self, media: Media) -> None:
        """Request thumbnail generation for media (if not already in progress)."""
        if self.thumbnail_cache and not self.loader_thread:
            # Start background loader for visible items
            visible_media = [
                self.filtered_media[index]
                for index in self.visible_widgets.keys()
                if index < len(self.filtered_media)
            ]

            if visible_media:
                self._start_thumbnail_loader(visible_media)

    def _start_thumbnail_loader(self, media_list: List[Media]) -> None:
        """Start background thumbnail loading for given media list."""
        if self.loader_thread and self.loader_thread.isRunning():
            return  # Already loading

        if self.thumbnail_cache:
            self.loader_thread = ThumbnailLoader(media_list, self.thumbnail_cache)
            self.loader_thread.thumbnail_ready.connect(self._on_thumbnail_ready)
            self.loader_thread.finished.connect(self._on_loading_finished)
            self.loader_thread.start()

    def _on_thumbnail_ready(self, media: Media, thumbnail_path: Optional[Path]) -> None:
        """Handle thumbnail ready from loader."""
        # Find the widget for this media
        for index, widget in self.visible_widgets.items():
            if (
                index < len(self.filtered_media)
                and self.filtered_media[index].file_path == media.file_path
            ):
                if thumbnail_path and thumbnail_path.exists():
                    widget.load_thumbnail(thumbnail_path)
                break

    def _on_loading_finished(self) -> None:
        """Handle thumbnail loading completion."""
        self.loader_thread = None
        logger.debug("Thumbnail loading completed")

    def _clear_viewport(self) -> None:
        """Clear all visible widgets."""
        indices_to_clear = list(self.visible_widgets.keys())
        for index in indices_to_clear:
            self._hide_widget_at_index(index)

        self.widget_pool.clear_all()

    def _on_widget_clicked(self, media: Media) -> None:
        """Handle widget click."""
        # Update selection
        old_selected = self.selected_media
        self.selected_media = media

        # Find index in filtered media
        try:
            self.selected_index = next(
                i
                for i, m in enumerate(self.filtered_media)
                if m.file_path == media.file_path
            )
        except StopIteration:
            self.selected_index = -1

        # Update widget selection states
        for widget in self.visible_widgets.values():
            is_selected = widget.media.file_path == media.file_path
            widget.set_selected(is_selected)

        # Emit signals
        self.item_clicked.emit(media)
        if media != old_selected:
            self.selection_changed.emit(media)

    def _on_widget_double_clicked(self, media: Media) -> None:
        """Handle widget double click."""
        self.item_double_clicked.emit(media)

    def _on_favorite_toggled(self, media: Media) -> None:
        """Handle favorite toggle."""
        self.favorite_toggled.emit(media)

    def select_by_index(self, index: int) -> None:
        """Select media by index in the filtered list."""
        if 0 <= index < len(self.filtered_media):
            media = self.filtered_media[index]
            self._on_widget_clicked(media)

            # Ensure the item is visible
            self._ensure_index_visible(index)

    def _ensure_index_visible(self, index: int) -> None:
        """Ensure the item at the given index is visible in the viewport."""
        if index < 0 or index >= len(self.filtered_media):
            return

        columns = self.layout_metrics.columns
        row = index // columns

        # Calculate the y position of this row
        item_y = self.layout_metrics.margins[1] + row * self.layout_metrics.cell_height

        # Check if it's already visible
        scroll_y = self.viewport_info.scroll_y
        visible_height = self.viewport_info.visible_height

        if item_y < scroll_y:
            # Scroll up to show item
            target_scroll = max(0, item_y - self.layout_metrics.cell_height)
            self._smooth_scroll_to(target_scroll)
        elif item_y + self.layout_metrics.cell_height > scroll_y + visible_height:
            # Scroll down to show item
            vscroll = self.verticalScrollBar()
            max_scroll = vscroll.maximum() if vscroll else 0
            target_scroll = min(
                max_scroll,
                item_y + self.layout_metrics.cell_height - visible_height,
            )
            self._smooth_scroll_to(target_scroll)

    def _smooth_scroll_to(self, target_y: int) -> None:
        """Smoothly scroll to the target y position."""
        if self.scroll_animation.state() == QPropertyAnimation.State.Running:
            self.scroll_animation.stop()

        vscroll = self.verticalScrollBar()
        if not vscroll:
            return
        current_y = vscroll.value()
        if abs(target_y - current_y) > 5:  # Only animate if significant difference
            self.scroll_animation.setStartValue(current_y)
            self.scroll_animation.setEndValue(target_y)
            self.scroll_animation.start()
        else:
            vscroll.setValue(target_y)

    def get_visible_media_count(self) -> int:
        """Get the count of currently visible (filtered) media."""
        return len(self.filtered_media)

    def get_total_media_count(self) -> int:
        """Get the total count of all media."""
        return len(self.media_list)

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        """Handle resize events."""
        super().resizeEvent(event)
        

        # Recalculate layout
        old_columns = self.layout_metrics.columns
        self._calculate_layout()

        # Update scroll range
        self._update_scroll_range()

        # If columns changed significantly, update viewport
        if abs(self.layout_metrics.columns - old_columns) >= 1:
            self._clear_viewport()
            self.update_timer.start(50)  # Slight delay to avoid rapid updates

    def wheelEvent(self, event: Optional[QWheelEvent]) -> None:
        """Handle wheel scrolling with smooth animation."""
        if event is None:
            return
        # Calculate scroll delta
        delta = -event.angleDelta().y() // 8  # Convert from degrees to pixels
        scroll_speed = 3  # Multiplier for scroll speed

        vscroll = self.verticalScrollBar()
        if not vscroll:
            return
        current_scroll = vscroll.value()
        max_scroll = vscroll.maximum()

        # Calculate target scroll position
        target_scroll = max(0, min(max_scroll, current_scroll + delta * scroll_speed))

        # Use smooth scrolling for wheel events
        if abs(target_scroll - current_scroll) > 10:
            self._smooth_scroll_to(target_scroll)
        else:
            vscroll.setValue(target_scroll)

        event.accept()


class VirtualThumbnailView(QWidget):
    """
    Main virtual thumbnail view widget.

    This is a high-performance thumbnail grid view that virtualizes the display
    to handle thousands of thumbnails efficiently. It only creates thumbnail
    widgets for visible items and reuses them as the user scrolls.

    Features:
    - Virtual scrolling with object pooling
    - Smooth scrolling with preloading buffer
    - Dynamic loading/unloading of thumbnails
    - Selection state management across virtual widgets
    - Window resizing efficiency
    - All filtering and favorite features from the original implementation
    """

    # Signals
    selection_changed = pyqtSignal(object)  # Emits selected Media object
    favorite_toggled = pyqtSignal(object)  # Emits Media object when favorite is toggled
    thumbnail_size_changed = pyqtSignal(tuple)  # Emits new thumbnail size (width, height)

    def __init__(self, parent: Optional[QWidget] = None, thumbnail_size: Optional[Tuple[int, int]] = None):
        super().__init__(parent)

        # Data
        self.media_list: List[Media] = []
        self.filtered_paths: Set[str] = set()
        self.selected_media: Optional[Media] = None
        self.thumbnail_size = thumbnail_size or (200, 200)

        # UI setup
        self._setup_ui()
        self._setup_keyboard_shortcuts()

        # Connect signals
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # Header with title and info
        header_layout = QHBoxLayout()

        self.title_label = QLabel("Media Gallery (Virtual)")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        self.title_label.setFont(title_font)
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()
        
        # Add thumbnail size selector buttons
        self._create_size_selector(header_layout)
        
        # Add some spacing between size selector and info label
        header_layout.addSpacing(20)

        self.info_label = QLabel("0 items")
        self.info_label.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(self.info_label)

        main_layout.addLayout(header_layout)

        # Virtual scroll area
        self.scroll_area = VirtualScrollArea(parent=self, thumbnail_size=self.thumbnail_size)
        main_layout.addWidget(self.scroll_area)

        # Set focus policy for keyboard navigation
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_keyboard_shortcuts(self) -> None:
        """Set up keyboard shortcuts for navigation and actions."""
        # F key to toggle favorite
        favorite_shortcut = QShortcut(QKeySequence("F"), self)
        favorite_shortcut.activated.connect(self._toggle_selected_favorite)

        # Enter/Return to open media
        enter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        enter_shortcut.activated.connect(self._open_selected_media)

        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space_shortcut.activated.connect(self._open_selected_media)

    def _create_size_selector(self, parent_layout: QHBoxLayout) -> None:
        """Create thumbnail size selector buttons."""
        # Create button group for exclusive selection
        self.size_button_group = QButtonGroup(self)
        
        # Define size options
        self.size_options = {
            "small": (128, 128),
            "medium": (256, 256),
            "large": (512, 512)
        }
        
        # Create frame for buttons
        button_frame = QFrame()
        button_frame.setStyleSheet("""
            QFrame {
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 2px;
            }
        """)
        button_layout = QHBoxLayout(button_frame)
        button_layout.setContentsMargins(2, 2, 2, 2)
        button_layout.setSpacing(2)
        
        # Create buttons for each size
        self.size_buttons = {}
        for size_name, (width, height) in self.size_options.items():
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(28, 28)
            
            # Set icon-like text (you could use actual icons here)
            if size_name == "small":
                btn.setText("S")
                btn.setToolTip("Small thumbnails (128x128)")
            elif size_name == "medium":
                btn.setText("M")
                btn.setToolTip("Medium thumbnails (256x256)")
            else:  # large
                btn.setText("L")
                btn.setToolTip("Large thumbnails (512x512)")
            
            btn.setStyleSheet("""
                QPushButton {
                    font-weight: bold;
                    border: 1px solid transparent;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.05);
                }
                QPushButton:checked {
                    background-color: rgba(0, 120, 215, 0.2);
                    border: 1px solid rgba(0, 120, 215, 0.5);
                }
            """)
            
            # Check if this is the current size
            if (width, height) == self.thumbnail_size:
                btn.setChecked(True)
            
            # Connect to size change handler
            btn.clicked.connect(lambda checked, s=size_name: self._on_size_changed(s))
            
            self.size_button_group.addButton(btn)
            self.size_buttons[size_name] = btn
            button_layout.addWidget(btn)
        
        parent_layout.addWidget(button_frame)
    
    def _on_size_changed(self, size_name: str) -> None:
        """Handle thumbnail size change."""
        new_size = self.size_options[size_name]
        if new_size != self.thumbnail_size:
            # Update internal size
            self.thumbnail_size = new_size
            
            # Emit signal for main window to handle
            self.thumbnail_size_changed.emit(new_size)
    
    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self.scroll_area.item_clicked.connect(self._on_item_clicked)
        self.scroll_area.item_double_clicked.connect(self._on_item_double_clicked)
        self.scroll_area.selection_changed.connect(self._on_selection_changed)
        self.scroll_area.favorite_toggled.connect(self._on_favorite_toggled)

    def set_media_list(self, media_list: List[Media]) -> None:
        """
        Set the list of media to display.

        Args:
            media_list: List of Media objects to display
        """
        logger.info(
            f"VirtualThumbnailView: Setting media list with {len(media_list)} items"
        )

        self.media_list = media_list
        self.scroll_area.set_media_list(media_list)
        self._update_info_label()

    def apply_filters(self, filtered_paths: Set[str]) -> None:
        """
        Apply filters and update the display.

        Args:
            filtered_paths: Set of file paths to show (empty set shows all)
        """
        logger.debug(
            f"VirtualThumbnailView: Applying filters to {len(filtered_paths)} paths"
        )

        self.filtered_paths = filtered_paths
        self.scroll_area.apply_filters(filtered_paths)

        # Update info label
        visible_count = self.scroll_area.get_visible_media_count()
        self._update_info_label(visible_count)

    def _update_info_label(self, visible_count: Optional[int] = None) -> None:
        """Update the info label with item counts."""
        if visible_count is None:
            visible_count = len(self.media_list)

        total_count = len(self.media_list)

        if visible_count == total_count:
            self.info_label.setText(f"{total_count} items")
        else:
            self.info_label.setText(f"{visible_count} of {total_count} items")

    def _on_item_clicked(self, media: Media) -> None:
        """Handle item selection."""
        logger.debug(f"Item clicked: {media.file_name}")
        self.selected_media = media

    def _on_selection_changed(self, media: Media) -> None:
        """Handle selection change."""
        self.selected_media = media
        self.selection_changed.emit(media)

    def _on_item_double_clicked(self, media: Media) -> None:
        """Handle item double click to open file."""
        # Just pass the signal through - the main window will handle opening the media viewer
        logger.info(f"Double-clicked media file: {media.file_name}")

    def _on_favorite_toggled(self, media: Media) -> None:
        """Handle favorite toggle."""
        logger.debug(f"Favorite toggled for: {media.file_name}")
        self.favorite_toggled.emit(media)

    def _toggle_selected_favorite(self) -> None:
        """Toggle favorite status of the selected media."""
        if self.selected_media:
            self.selected_media.is_favorite = not self.selected_media.is_favorite
            # Find and update the widget
            for widget in self.scroll_area.visible_widgets.values():
                if widget.media.file_path == self.selected_media.file_path:
                    widget.update_star_icon()
                    break
            self.favorite_toggled.emit(self.selected_media)

    def _open_selected_media(self) -> None:
        """Open the currently selected media file."""
        if self.selected_media:
            # Emit double-click signal for the selected media
            self.scroll_area.item_double_clicked.emit(self.selected_media)

    def _open_media_file(self, file_path: Path) -> None:
        """Open a media file with the system's default viewer."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            # Try Qt's cross-platform approach first
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices

            url = QUrl.fromLocalFile(str(file_path))
            if QDesktopServices.openUrl(url):
                logger.info(f"Opened media file: {file_path}")
                return

            # Fallback to platform-specific approaches
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

    def keyPressEvent(self, event) -> None:
        """Handle keyboard navigation."""
        if not self.scroll_area.filtered_media:
            return super().keyPressEvent(event)

        current_index = self.scroll_area.selected_index
        media_count = len(self.scroll_area.filtered_media)
        columns = self.scroll_area.layout_metrics.columns

        # Initialize selection if none exists
        if current_index == -1 and media_count > 0:
            self.scroll_area.select_by_index(0)
            return

        key = event.key()
        new_index = current_index

        if key == Qt.Key.Key_Left:
            if current_index > 0:
                new_index = current_index - 1
        elif key == Qt.Key.Key_Right:
            if current_index < media_count - 1:
                new_index = current_index + 1
        elif key == Qt.Key.Key_Up:
            if current_index >= columns:
                new_index = current_index - columns
        elif key == Qt.Key.Key_Down:
            if current_index + columns < media_count:
                new_index = current_index + columns
        elif key == Qt.Key.Key_Home:
            new_index = 0
        elif key == Qt.Key.Key_End:
            new_index = media_count - 1
        elif key == Qt.Key.Key_PageUp:
            visible_rows = (
                self.scroll_area.viewport_info.visible_height
                // self.scroll_area.layout_metrics.cell_height
            )
            new_index = max(0, current_index - (visible_rows * columns))
        elif key == Qt.Key.Key_PageDown:
            visible_rows = (
                self.scroll_area.viewport_info.visible_height
                // self.scroll_area.layout_metrics.cell_height
            )
            new_index = min(media_count - 1, current_index + (visible_rows * columns))
        else:
            return super().keyPressEvent(event)

        if new_index != current_index and 0 <= new_index < media_count:
            self.scroll_area.select_by_index(new_index)

    def get_selected_media(self) -> Optional[Media]:
        """Get the currently selected media object."""
        return self.selected_media

    def clear(self) -> None:
        """Clear all thumbnails and reset the view."""
        logger.info("Clearing virtual thumbnail view")
        self.media_list.clear()
        self.filtered_paths.clear()
        self.selected_media = None
        self.scroll_area.set_media_list([])
        self._update_info_label()
