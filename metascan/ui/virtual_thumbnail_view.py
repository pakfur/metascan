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
    QShowEvent,
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
from metascan.ui.thumbnail_view import ThumbnailWidget
from metascan.utils.app_paths import get_thumbnail_cache_dir

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

    def __init__(
        self,
        parent: QWidget,
        initial_size: int = 20,
        thumbnail_size: Tuple[int, int] = (200, 200),
    ):
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

            widget = ThumbnailWidget(
                dummy_media, parent=self.parent, size=self.thumbnail_size
            )
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
    # Context menu signals
    open_requested = pyqtSignal(object)  # Forward Media object for File|Open
    open_folder_requested = pyqtSignal(
        object
    )  # Forward Media object for File|Open Folder
    delete_requested = pyqtSignal(object)  # Forward Media object for File|Delete
    upscale_requested = pyqtSignal(object)  # Forward Media object for File|Upscale
    selection_changed = pyqtSignal(object)  # Emits Media object

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        thumbnail_size: Optional[Tuple[int, int]] = None,
        scroll_step: int = 120,
    ):
        super().__init__(parent)

        # Data
        self.media_list: List[Media] = []
        self.filtered_media: List[Media] = []  # Current filtered subset
        self.selected_media: Optional[Media] = None
        self.selected_index: int = -1
        self.scroll_step = scroll_step  # Store scroll step setting

        # Multi-selection support
        self.multi_select_mode: bool = False
        self.selected_media_set: Set[str] = (
            set()
        )  # Set of file paths for multi-selection

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

        # Thumbnail loading (synchronous approach)
        self.thumbnail_cache: Optional[ThumbnailCache] = None
        self._cancel_thumbnail_render: bool = False  # Flag to stop rendering on state change

        # Timer for rendering thumbnails after scroll stops (debounce)
        self._thumbnail_render_timer = QTimer(self)
        self._thumbnail_render_timer.setSingleShot(True)
        self._thumbnail_render_timer.timeout.connect(self._render_visible_thumbnails)

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

        # Initialize thumbnail cache with correct size (use app's cache directory)
        cache_dir = get_thumbnail_cache_dir()
        thumb_size = (self.layout_metrics.item_width, self.layout_metrics.item_height)
        self.thumbnail_cache = ThumbnailCache(cache_dir, thumb_size)

    def set_media_list(self, media_list: List[Media]) -> None:
        """
        Set the media list for display.

        Args:
            media_list: List of Media objects to display
        """
        logger.info(f"Setting media list with {len(media_list)} items")

        # Cancel any pending thumbnail rendering
        self._cancel_thumbnail_render = True
        self._thumbnail_render_timer.stop()

        # Clear existing state completely - this ensures deleted media is removed
        self._clear_viewport()
        self.widget_pool.clear_all()

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

        # Schedule thumbnail rendering after a short delay
        self._cancel_thumbnail_render = False
        self._thumbnail_render_timer.start(100)

    def apply_filters(
        self, filtered_paths: Optional[Set[str]], preserve_selection: bool = False
    ) -> None:
        """
        Apply filters to the media list.

        Args:
            filtered_paths: Set of file paths to show (empty set shows all)
            preserve_selection: If True, don't clear selections (used during view recreation)
        """
        # Cancel any pending thumbnail rendering
        self._cancel_thumbnail_render = True
        self._thumbnail_render_timer.stop()

        # Handle None vs empty set differently
        if filtered_paths is None:
            logger.debug("No filters applied - showing all media")
            # No filter - show all
            self.filtered_media = self.media_list.copy()
        else:
            logger.debug(f"Applying filters: {len(filtered_paths)} paths")
            # Filter media list - empty set means show nothing
            self.filtered_media = [
                media
                for media in self.media_list
                if str(media.file_path) in filtered_paths
            ]

        # Clear all selections when filters change (unless preserving)
        if not preserve_selection:
            self.clear_all_selections()

        # Recalculate layout and update
        self._calculate_layout()
        self._update_scroll_range()

        # Force a complete viewport refresh to ensure deleted media is properly removed
        self._clear_viewport()
        self.widget_pool.clear_all()
        self._update_viewport()

        # Schedule thumbnail rendering after a short delay
        self._cancel_thumbnail_render = False
        self._thumbnail_render_timer.start(100)

    def _calculate_layout(self) -> None:
        """Calculate grid layout metrics based on current size and media count."""
        # For a QScrollArea, we want the width of the scroll area itself, not the viewport
        # The viewport is the visible area, but we want to layout based on the full width
        scroll_area_width = self.width()

        # Account for vertical scrollbar width if it's shown
        vscrollbar = self.verticalScrollBar()
        scrollbar_width = (
            vscrollbar.width() if vscrollbar and vscrollbar.isVisible() else 0
        )

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

        logger.debug(
            f"Layout calculated: {columns}x{rows} grid for {item_count} items, width={scroll_area_width}, cell_width={self.layout_metrics.cell_width}"
        )

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
        # Cancel any in-progress thumbnail rendering
        self._cancel_thumbnail_render = True
        self._thumbnail_render_timer.stop()

        vscroll = self.verticalScrollBar()
        if vscroll:
            self.viewport_info.scroll_y = vscroll.value()
        # Throttle viewport updates for performance
        if not self.update_timer.isActive():
            self.update_timer.start(16)  # ~60 FPS

        # Schedule thumbnail rendering after scroll stops (debounce)
        self._cancel_thumbnail_render = False
        self._thumbnail_render_timer.start(150)  # Wait 150ms after last scroll

    def _update_viewport(self) -> None:
        """Update the viewport to show the correct widgets."""
        if not self.filtered_media:
            self._clear_viewport()
            return

        # Ensure we have valid viewport dimensions
        # If visible_height is 0, try to get current viewport dimensions
        if self.viewport_info.visible_height <= 0:
            viewport = self.viewport()
            if viewport and viewport.height() > 0:
                self.viewport_info.visible_height = viewport.height()
            else:
                # Viewport not yet laid out, skip this update
                # showEvent or resizeEvent will trigger update when dimensions are valid
                logger.debug("Skipping viewport update - viewport height not yet available")
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

        # Disconnect any existing signals first (important for widget reuse)
        try:
            widget.clicked.disconnect()
        except TypeError:
            pass  # No connections exist
        try:
            widget.double_clicked.disconnect()
        except TypeError:
            pass
        try:
            widget.favorite_toggled.disconnect()
        except TypeError:
            pass
        try:
            widget.open_requested.disconnect()
        except TypeError:
            pass
        try:
            widget.open_folder_requested.disconnect()
        except TypeError:
            pass
        try:
            widget.delete_requested.disconnect()
        except TypeError:
            pass
        try:
            widget.upscale_requested.disconnect()
        except TypeError:
            pass

        # Connect signals
        widget.clicked.connect(self._on_widget_clicked)
        widget.double_clicked.connect(self._on_widget_double_clicked)
        widget.favorite_toggled.connect(self._on_favorite_toggled)
        # Connect context menu signals
        widget.open_requested.connect(self.open_requested.emit)
        widget.open_folder_requested.connect(self.open_folder_requested.emit)
        widget.delete_requested.connect(self.delete_requested.emit)
        widget.upscale_requested.connect(self.upscale_requested.emit)

        # Set selection state based on mode
        if self.multi_select_mode:
            widget.set_selected(str(media.file_path) in self.selected_media_set)
        else:
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
        """Load thumbnail for a widget from cache if available."""
        if not widget.pixmap() or widget.pixmap().isNull():
            # Check if thumbnail exists in cache
            if self.thumbnail_cache:
                thumbnail_path = self.thumbnail_cache.get_thumbnail_path(
                    widget.media.file_path
                )
                if thumbnail_path and thumbnail_path.exists():
                    widget.load_thumbnail(thumbnail_path)
                # If not in cache, _render_visible_thumbnails will handle it

    def _render_visible_thumbnails(self) -> None:
        """Synchronously render thumbnails for visible widgets that are missing them.

        Called after scroll stops or state changes. Checks cancel flag between each
        thumbnail to allow immediate interruption on new scroll/state change.
        """
        if not self.thumbnail_cache or not self.visible_widgets:
            return

        logger.debug(f"Starting thumbnail render for {len(self.visible_widgets)} visible widgets")

        # Get a snapshot of visible widgets to avoid modification during iteration
        widgets_snapshot = list(self.visible_widgets.items())

        for index, widget in widgets_snapshot:
            # Check cancel flag before each thumbnail - allows immediate stop on scroll
            if self._cancel_thumbnail_render:
                logger.debug("Thumbnail rendering cancelled")
                return

            # Skip if widget is no longer in visible_widgets (was removed by scroll)
            if index not in self.visible_widgets or self.visible_widgets[index] is not widget:
                continue

            # Skip if widget already has a thumbnail (with guard for deleted widgets)
            try:
                if widget.pixmap() and not widget.pixmap().isNull():
                    continue
            except RuntimeError:
                # Widget's C++ object was deleted, skip it
                continue

            # Skip if index is no longer valid
            if index >= len(self.filtered_media):
                continue

            try:
                media = self.filtered_media[index]
                # Get or create thumbnail (this may generate it if not cached)
                thumbnail_path = self.thumbnail_cache.get_or_create_thumbnail(
                    media.file_path
                )

                # Check cancel flag again after potentially slow thumbnail generation
                if self._cancel_thumbnail_render:
                    logger.debug("Thumbnail rendering cancelled after generation")
                    return

                # Load the thumbnail into the widget if it's still visible and same widget
                if (
                    index in self.visible_widgets
                    and self.visible_widgets[index] is widget
                    and thumbnail_path
                    and thumbnail_path.exists()
                ):
                    try:
                        self.visible_widgets[index].load_thumbnail(thumbnail_path)
                    except RuntimeError:
                        # Widget was deleted, skip
                        pass

                # Process Qt events to keep UI responsive
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()

            except Exception as e:
                logger.error(f"Failed to render thumbnail for index {index}: {e}")

    def _clear_viewport(self) -> None:
        """Clear all visible widgets."""
        indices_to_clear = list(self.visible_widgets.keys())
        for index in indices_to_clear:
            self._hide_widget_at_index(index)

        self.widget_pool.clear_all()

    def _on_widget_clicked(self, media: Media) -> None:
        """Handle widget click."""
        media_path = str(media.file_path)

        if self.multi_select_mode:
            # Multi-select mode: toggle selection
            if media_path in self.selected_media_set:
                self.selected_media_set.remove(media_path)
            else:
                self.selected_media_set.add(media_path)

            # Update widget selection states for visible widgets
            for widget in self.visible_widgets.values():
                widget_path = str(widget.media.file_path)
                widget.set_selected(widget_path in self.selected_media_set)

            # Update selected_media to be the last clicked item
            self.selected_media = (
                media if media_path in self.selected_media_set else None
            )

            # Find index in filtered media
            try:
                self.selected_index = next(
                    i
                    for i, m in enumerate(self.filtered_media)
                    if m.file_path == media.file_path
                )
            except StopIteration:
                self.selected_index = -1

            # Emit signals
            self.item_clicked.emit(media)
            self.selection_changed.emit(media)
        else:
            # Single-select mode: clear previous selections
            old_selected = self.selected_media
            self.selected_media = media
            self.selected_media_set.clear()
            self.selected_media_set.add(media_path)

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

    def set_multi_select_mode(self, enabled: bool) -> None:
        """Enable or disable multi-select mode."""
        self.multi_select_mode = enabled
        if not enabled:
            # Clear all selections when disabling multi-select
            self.clear_all_selections()

    def clear_all_selections(self) -> None:
        """Clear all selected items."""
        self.selected_media_set.clear()
        self.selected_media = None
        self.selected_index = -1

        # Update visible widgets
        for widget in self.visible_widgets.values():
            widget.set_selected(False)

    def get_selected_count(self) -> int:
        """Get the number of selected items."""
        return len(self.selected_media_set)

    def get_selected_media_list(self) -> List[Media]:
        """Get list of all selected media items."""
        selected_list = []
        for media in self.filtered_media:
            if str(media.file_path) in self.selected_media_set:
                selected_list.append(media)
        return selected_list

    def restore_selections(self, selected_paths: Set[str]) -> None:
        """Restore selections and update visible widgets."""
        self.selected_media_set = selected_paths

        # Find the first selected media to set as the current selection
        for media in self.filtered_media:
            if str(media.file_path) in selected_paths:
                self.selected_media = media
                # Find its index
                try:
                    self.selected_index = next(
                        i
                        for i, m in enumerate(self.filtered_media)
                        if m.file_path == media.file_path
                    )
                except StopIteration:
                    self.selected_index = -1
                break

        # Clear and recreate all visible widgets to ensure proper selection state
        # This forces all widgets to be recreated with the correct selection border
        indices_to_refresh = list(self.visible_widgets.keys())
        for index in indices_to_refresh:
            self._hide_widget_at_index(index)
            self._show_widget_at_index(index)

    def showEvent(self, event: Optional[QShowEvent]) -> None:
        """Handle show events - ensures viewport is updated when widget becomes visible."""
        super().showEvent(event)
        # Schedule a viewport update when the widget becomes visible
        # This handles cases where set_media_list was called before the widget was shown
        if self.filtered_media and not self.visible_widgets:
            # Update scroll range to get correct viewport dimensions
            self._update_scroll_range()
            self.update_timer.start(50)

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        """Handle resize events."""
        super().resizeEvent(event)

        # Track previous visible height to detect initial layout
        old_visible_height = self.viewport_info.visible_height

        # Recalculate layout
        old_columns = self.layout_metrics.columns
        self._calculate_layout()

        # Update scroll range (this updates viewport_info.visible_height)
        self._update_scroll_range()

        # Determine if we need to update the viewport:
        # 1. Columns changed significantly
        # 2. Height became valid (changed from 0 to > 0) - handles initial layout
        # 3. We have media but no visible widgets (initial state after set_media_list)
        columns_changed = abs(self.layout_metrics.columns - old_columns) >= 1
        height_became_valid = old_visible_height == 0 and self.viewport_info.visible_height > 0
        needs_initial_widgets = (
            self.filtered_media and not self.visible_widgets
        )

        if columns_changed or height_became_valid or needs_initial_widgets:
            if columns_changed:
                self._clear_viewport()
            self.update_timer.start(50)  # Slight delay to avoid rapid updates

    def wheelEvent(self, event: Optional[QWheelEvent]) -> None:
        """Handle wheel scrolling with smooth animation."""
        if event is None:
            return
        # Calculate scroll delta using the scroll_step setting
        delta = -event.angleDelta().y()

        vscroll = self.verticalScrollBar()
        if not vscroll:
            return
        current_scroll = vscroll.value()
        max_scroll = vscroll.maximum()

        # Calculate target scroll position using scroll_step
        # The scroll_step determines how many pixels to scroll per wheel notch
        # Standard wheel delta is 120 for one notch, so we scale accordingly
        target_scroll = max(
            0, min(max_scroll, current_scroll + (delta * self.scroll_step) // 120)
        )

        # Use smooth scrolling for wheel events
        if abs(target_scroll - current_scroll) > 10:
            self._smooth_scroll_to(target_scroll)
        else:
            vscroll.setValue(target_scroll)

        event.accept()

    def keyPressEvent(self, event) -> None:
        """Handle keyboard navigation."""
        if not self.filtered_media:
            return super().keyPressEvent(event)

        current_index = self.selected_index
        media_count = len(self.filtered_media)
        columns = self.layout_metrics.columns

        # Initialize selection if none exists
        if current_index == -1 and media_count > 0:
            self.select_by_index(0)
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
                self.viewport_info.visible_height // self.layout_metrics.cell_height
            )
            new_index = max(0, current_index - (visible_rows * columns))
        elif key == Qt.Key.Key_PageDown:
            visible_rows = (
                self.viewport_info.visible_height // self.layout_metrics.cell_height
            )
            new_index = min(media_count - 1, current_index + (visible_rows * columns))
        else:
            return super().keyPressEvent(event)

        if new_index != current_index and 0 <= new_index < media_count:
            self.select_by_index(new_index)
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
    thumbnail_size_changed = pyqtSignal(
        tuple
    )  # Emits new thumbnail size (width, height)
    multi_selection_changed = pyqtSignal(int)  # Emits number of selected items
    # Context menu action signals to forward to main window (to match ThumbnailView)
    open_requested = pyqtSignal(object)  # Forward Media object for File|Open
    open_folder_requested = pyqtSignal(
        object
    )  # Forward Media object for File|Open Folder
    delete_requested = pyqtSignal(object)  # Forward Media object for File|Delete
    upscale_requested = pyqtSignal(object)  # Forward Media object for File|Upscale

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        thumbnail_size: Optional[Tuple[int, int]] = None,
        scroll_step: int = 120,
    ):
        super().__init__(parent)

        # Data
        self.media_list: List[Media] = []
        self.filtered_paths: Optional[Set[str]] = None
        self.selected_media: Optional[Media] = None
        self.thumbnail_size = thumbnail_size or (200, 200)
        self.scroll_step = scroll_step

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

        # Add Select button for multi-selection mode
        header_layout.addSpacing(15)
        self.select_button = QPushButton("Select...")
        self.select_button.setCheckable(True)
        self.select_button.setChecked(False)
        self.select_button.setFixedWidth(100)
        self.select_button.setToolTip("Enable multi-selection mode")
        self.select_button.toggled.connect(self._on_select_mode_toggled)
        header_layout.addWidget(self.select_button)

        header_layout.addStretch()

        self._create_size_selector(header_layout)

        # Add some spacing between size selector and info label
        header_layout.addSpacing(15)

        self.info_label = QLabel("0 items")
        self.info_label.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(self.info_label)

        main_layout.addLayout(header_layout)

        # Virtual scroll area
        self.scroll_area = VirtualScrollArea(
            parent=self,
            thumbnail_size=self.thumbnail_size,
            scroll_step=self.scroll_step,
        )
        main_layout.addWidget(self.scroll_area)

        # Set scroll wheel sensitivity
        v_scrollbar = self.scroll_area.verticalScrollBar()
        if v_scrollbar:
            v_scrollbar.setSingleStep(self.scroll_step)

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
            "large": (512, 512),
        }

        # Create frame for buttons
        button_frame = QFrame()
        button_frame.setStyleSheet(
            """
            QFrame {
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 2px;
            }
        """
        )
        button_layout = QHBoxLayout(button_frame)
        button_layout.setContentsMargins(2, 2, 2, 2)
        button_layout.setSpacing(2)

        # Create buttons for each size
        self.size_buttons = {}
        for size_name, (width, height) in self.size_options.items():
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(24, 24)

            # Set icon-like text (you could use actual icons here)
            if size_name == "small":
                btn.setText("▫️")
                btn.setToolTip("Small thumbnails (128x128)")
            elif size_name == "medium":
                btn.setText("◻️")
                btn.setToolTip("Medium thumbnails (256x256)")
            else:  # large
                btn.setText("⬜️")
                btn.setToolTip("Large thumbnails (512x512)")

            btn.setStyleSheet(
                """
                QPushButton {
                    font-weight: bold;
                    border: 1px solid transparent;
                    border-radius: 3px;
                    font-size: 14px;
                    line-height: 24px;
                    padding: 0px;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.05);
                }
                QPushButton:checked {
                    background-color: rgba(0, 120, 215, 0.2);
                    border: 1px solid rgba(0, 120, 215, 0.5);
                }
            """
            )

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
        # Forward context menu signals
        self.scroll_area.open_requested.connect(self.open_requested.emit)
        self.scroll_area.open_folder_requested.connect(self.open_folder_requested.emit)
        self.scroll_area.delete_requested.connect(self.delete_requested.emit)
        self.scroll_area.upscale_requested.connect(self.upscale_requested.emit)

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

    def apply_filters(
        self, filtered_paths: Optional[Set[str]], preserve_selection: bool = False
    ) -> None:
        """
        Apply filters and update the display.

        Args:
            filtered_paths: Set of file paths to show (None shows all, empty set shows none)
            preserve_selection: If True, don't clear selections (used during view recreation)
        """
        if filtered_paths is None:
            logger.debug("VirtualThumbnailView: No filters applied - showing all media")
        else:
            logger.debug(
                f"VirtualThumbnailView: Applying filters to {len(filtered_paths)} paths"
            )

        self.filtered_paths = filtered_paths
        self.scroll_area.apply_filters(filtered_paths, preserve_selection)

        # Reset multi-select mode when filters change (unless preserving)
        if not preserve_selection and self.select_button.isChecked():
            self.select_button.setChecked(False)

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
        # Set focus to enable keyboard navigation
        self.setFocus()

    def _on_selection_changed(self, media: Media) -> None:
        """Handle selection change."""
        self.selected_media = media

        # Emit multi-selection signal if in multi-select mode
        if self.scroll_area.multi_select_mode:
            selected_count = self.scroll_area.get_selected_count()
            self.multi_selection_changed.emit(selected_count)

            # Only emit selection_changed if exactly one item is selected
            if selected_count == 1:
                self.selection_changed.emit(media)
            else:
                self.selection_changed.emit(None)  # Clear metadata panel
        else:
            self.selection_changed.emit(media)

    def _on_item_double_clicked(self, media: Media) -> None:
        """Handle item double click to open file."""
        # Just pass the signal through - the main window will handle opening the media viewer
        logger.info(f"Double-clicked media file: {media.file_name}")

    def _on_favorite_toggled(self, media: Media) -> None:
        """Handle favorite toggle."""
        logger.debug(f"Favorite toggled for: {media.file_name}")
        self.favorite_toggled.emit(media)

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """Handle Select button toggle."""
        self.scroll_area.set_multi_select_mode(checked)

        # Update button text to indicate state
        if checked:
            self.select_button.setText("Select ✓")
        else:
            self.select_button.setText("Select...")

        # Emit signal if selections were cleared
        if not checked:
            self.multi_selection_changed.emit(0)
            self.selection_changed.emit(None)

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
        """Handle keyboard navigation by delegating to scroll area."""
        # Delegate to the scroll area's keyPressEvent handler
        self.scroll_area.keyPressEvent(event)

    def get_selected_media(self) -> Optional[Media]:
        """Get the currently selected media object."""
        return self.selected_media

    def get_all_selected_media(self) -> List[Media]:
        """Get all selected media items in multi-select mode."""
        return self.scroll_area.get_selected_media_list()

    def is_multi_select_mode(self) -> bool:
        """Check if multi-select mode is enabled."""
        return self.scroll_area.multi_select_mode

    def clear(self) -> None:
        """Clear all thumbnails and reset the view."""
        logger.info("Clearing virtual thumbnail view")
        self.media_list.clear()
        self.filtered_paths = None
        self.selected_media = None
        self.scroll_area.set_media_list([])
        self._update_info_label()
