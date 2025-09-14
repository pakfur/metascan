import sys
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
)
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QTimer
from typing import Tuple, Dict, List
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QActionGroup, QMovie
from qt_material import apply_stylesheet, list_themes
from metascan.ui.config_dialog import ConfigDialog
from metascan.ui.filters_panel import FiltersPanel
from metascan.ui.thumbnail_view import ThumbnailView
from metascan.ui.virtual_thumbnail_view import VirtualThumbnailView
from metascan.ui.metadata_panel import MetadataPanel
from metascan.ui.media_viewer import MediaViewer
from metascan.ui.upscale_dialog import UpscaleDialog, ModelSetupDialog
from metascan.ui.upscale_queue_window import UpscaleQueueWindow
from metascan.core.scanner import Scanner, ThreadedScanner
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media_upscaler import MediaUpscaler
from metascan.core.upscale_queue_process import ProcessUpscaleQueue
from metascan.cache.thumbnail import ThumbnailCache
from metascan.utils.app_paths import (
    get_data_dir,
    get_config_path,
    get_thumbnail_cache_dir,
    get_models_dir,
    get_queue_dir,
)
import os
import json
from pathlib import Path
import shutil
import platform
import subprocess
import logging


class ScannerThread(QThread):
    progress_updated = pyqtSignal(int, int, str)  # current, total, current_file
    directory_progress_updated = pyqtSignal(
        int, int, str
    )  # current_dir, total_dirs, dir_path
    scan_complete = pyqtSignal(int)  # processed_count
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
                self.scan_complete.emit(total_processed)
        except Exception as e:
            self.scan_error.emit(str(e))


class ScanProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scanning Media Files (Threaded)")
        self.setModal(True)
        self.setFixedSize(550, 250)

        # Layout
        layout = QVBoxLayout(self)
        layout.setSpacing(5)  # Default spacing between widgets

        # Container for stacked progress bars with no gap
        progress_container = QWidget()
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(0)  # No gap between progress bars

        # Directory progress bar
        self.dir_progress_bar = QProgressBar()
        self.dir_progress_bar.setTextVisible(True)
        self.dir_progress_bar.setFormat("Directory %v of %m")
        progress_layout.addWidget(self.dir_progress_bar)

        # File progress bar (stacked directly below with no gap)
        self.file_progress_bar = QProgressBar()
        self.file_progress_bar.setTextVisible(True)
        self.file_progress_bar.setFormat("File %v of %m")
        progress_layout.addWidget(self.file_progress_bar)

        # Add the progress container to main layout
        layout.addWidget(progress_container)

        # Add small spacing after progress bars
        layout.addSpacing(10)

        # Current directory label
        self.dir_label = QLabel("Preparing to scan...")
        layout.addWidget(self.dir_label)

        # Current file label
        self.file_label = QLabel("")
        layout.addWidget(self.file_label)

        # Progress text summary
        self.progress_label = QLabel("0 / 0 files")
        layout.addWidget(self.progress_label)

        # Add stretch to push cancel button to bottom
        layout.addStretch()

        # Cancel button
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.on_cancel_clicked)
        layout.addWidget(cancel_button)

        self.cancel_requested = False

    def update_directory_progress(self, current_dir, total_dirs, dir_path):
        """Update the directory progress display."""
        self.dir_progress_bar.setMaximum(total_dirs)
        self.dir_progress_bar.setValue(current_dir)

        # Show shortened directory path
        if isinstance(dir_path, str):
            dir_name = Path(dir_path).name
            parent_name = Path(dir_path).parent.name
            # Show parent/directory for better context
            display_path = f"{parent_name}/{dir_name}" if parent_name else dir_name
            self.dir_label.setText(f"Directory: {display_path}")

    def update_file_progress(self, current, total, file_path):
        """Update the file progress display."""
        self.file_progress_bar.setMaximum(total)
        self.file_progress_bar.setValue(current)
        self.progress_label.setText(f"{current} / {total} files")

        # Show shortened file path
        if isinstance(file_path, str):
            file_name = Path(file_path).name
            self.file_label.setText(f"Processing: {file_name}")

    def on_cancel_clicked(self):
        """Handle cancel button click."""
        reply = QMessageBox.question(
            self,
            "Cancel Scanning",
            "Are you sure you want to cancel the scanning process?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.cancel_requested = True
            self.file_label.setText("Cancelling...")


class ScanConfirmationDialog(QDialog):
    def __init__(self, total_dirs: int, total_files: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm Scan")
        self.setModal(True)
        self.setFixedSize(400, 200)

        # Layout
        layout = QVBoxLayout(self)

        # Information text
        info_text = f"Scan Configuration:\n\n"
        info_text += f"Directories to scan: {total_dirs}\n"
        info_text += f"Media files to process: {total_files:,}\n\n"
        info_text += (
            "This operation may take several minutes depending on the number of files."
        )

        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Full clean checkbox - no custom styling, use theme
        self.full_clean_checkbox = QCheckBox("Full clean and scan")
        self.full_clean_checkbox.setToolTip(
            "Clear all existing data (media records, indices, and thumbnails) before scanning.\n"
            "This ensures a completely fresh start but will remove all previously scanned data."
        )
        self.full_clean_checkbox.setChecked(False)
        layout.addWidget(self.full_clean_checkbox)

        # Question - no custom styling, use theme
        question_label = QLabel("Do you want to continue?")
        layout.addWidget(question_label)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No,
            Qt.Orientation.Horizontal,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Set focus on Yes button
        yes_button = button_box.button(QDialogButtonBox.StandardButton.Yes)
        if yes_button:
            yes_button.setFocus()

    def is_full_clean_requested(self) -> bool:
        """Check if full clean was requested."""
        return self.full_clean_checkbox.isChecked()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Metascan - AI Media Browser")

        # Initialize logger
        self.logger = logging.getLogger(__name__)

        # Initialize theme before other components
        self.available_themes = list_themes()
        self.current_theme = None

        # Initialize components
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
        self.load_and_apply_theme()
        db_path = get_data_dir()
        self.db_manager = DatabaseManager(db_path)

        # Current filter state
        self.current_filters = {}
        self.filtered_media_paths = (
            None  # None means no filters, empty set means filtered to nothing
        )
        self.favorites_active = False  # Track if favorites filter is active
        self.all_media = []  # Cache of all media for filtering

        # Initialize thumbnail cache for metadata panel
        cache_dir = get_thumbnail_cache_dir()
        # Get thumbnail size from config, default to (200, 200)
        thumbnail_size = tuple(self.config.get("thumbnail_size", [200, 200]))
        self.thumbnail_cache = ThumbnailCache(cache_dir, thumbnail_size)

        # Initialize scanner with thumbnail cache
        self.scanner = ThreadedScanner(
            self.db_manager,
            num_workers=4,
            batch_size=10,
            thumbnail_cache=self.thumbnail_cache,
        )

        # Initialize upscale components
        models_dir = get_models_dir()
        self.media_upscaler = MediaUpscaler(
            models_dir=models_dir, device="auto", tile_size=512, debug=False
        )

        # Initialize process-based upscale queue
        queue_dir = get_queue_dir()
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
        self.media_viewer = (
            MediaViewer()
        )  # Create without parent to control positioning
        self.media_viewer.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.media_viewer.closed.connect(self.on_media_viewer_closed)
        self.media_viewer.media_changed.connect(self.on_viewer_media_changed)
        self.media_viewer.delete_requested.connect(
            lambda media: self._confirm_and_delete_media(media, from_viewer=True)
        )
        self.media_viewer.favorite_toggled.connect(self.on_viewer_favorite_toggled)

        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Create splitter for three panels
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        # Left panel - Filters
        filter_panel = self._create_filter_panel()
        self.main_splitter.addWidget(filter_panel)

        # Middle panel - Thumbnails
        thumbnail_panel = self._create_thumbnail_panel()
        self.main_splitter.addWidget(thumbnail_panel)

        # Right panel - Metadata
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
        self._create_menu_bar()

        # Create toolbar
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

    def _create_filter_panel(self) -> QWidget:
        self.filters_panel = FiltersPanel()

        # Set maximum width constraint
        self.filters_panel.setMaximumWidth(395)

        # Connect signals
        self.filters_panel.filters_changed.connect(self.on_filters_changed)
        self.filters_panel.sort_changed.connect(self.on_sort_order_changed)
        self.filters_panel.favorites_toggled.connect(self.on_favorites_toggled)
        self.filters_panel.set_refresh_callback(self.refresh_filters)

        # Load initial filter data
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
        self.thumbnail_view.refresh_metadata_requested.connect(
            self.on_context_refresh_metadata_requested
        )

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

        # # Scan button - no custom styling, use theme
        # scan_button = QPushButton("Scan")
        # scan_button.clicked.connect(self._scan_directories)
        # toolbar.addWidget(scan_button)

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
                tasks = self.upscale_queue.get_all_tasks()
                processing = [t for t in tasks if t.status.value == "processing"]
                pending = [t for t in tasks if t.status.value == "pending"]
                completed = [t for t in tasks if t.status.value == "completed"]

                total = len(processing) + len(pending)
                current = len(completed) + 1 if processing else len(completed)

                if processing and processing[0].progress is not None:
                    percent = int(processing[0].progress)
                else:
                    percent = 0

                if total > 0:
                    spinner = self.spinner_frames[self.spinner_frame_index]
                    self.upscaling_widget.setText(
                        f"{spinner} Upscaling in progress... Processing: {len(processing)} Pending: {len(pending)} Completed: {len(completed)}"
                    )
                else:
                    self.upscaling_widget.setText(
                        self.spinner_frames[self.spinner_frame_index]
                    )
            else:
                self.upscaling_widget.setText(
                    self.spinner_frames[self.spinner_frame_index]
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

    def _on_task_added(self, task):
        """Handle when a task is added to the queue."""
        self._show_spinner()
        # Start processing immediately if no tasks are currently running
        self.upscale_queue.start_processing()

    def _on_task_updated(self, task):
        """Handle when a task is updated."""
        # Check if we need to hide spinner based on current queue state
        self._update_spinner_visibility()

        # If this task completed successfully, refresh the current view
        if task.status.value == "completed":
            # Reload media from database to show any new upscaled files
            self.load_all_media()

    def _on_task_removed(self, task_id):
        """Handle when a task is removed from the queue."""
        self._update_spinner_visibility()

    def _update_spinner_visibility(self):
        """Update spinner visibility based on current queue state."""
        all_tasks = self.upscale_queue.get_all_tasks()
        has_active_tasks = any(
            task.status.value in ["pending", "processing"] for task in all_tasks
        )

        if has_active_tasks:
            self._show_spinner()
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

            # Count directories and estimate media files
            total_dirs = len(directories)
            total_files = 0

            # Count media files in each directory
            for dir_info in directories:
                dir_path = Path(dir_info["filepath"])
                if dir_path.exists():
                    recursive = dir_info.get("search_subfolders", True)
                    media_files = self._count_media_files(dir_path, recursive)
                    total_files += media_files

            # Show confirmation dialog with full clean option
            confirmation_dialog = ScanConfirmationDialog(total_dirs, total_files, self)
            if confirmation_dialog.exec() != QDialog.DialogCode.Accepted:
                print("Scan cancelled by user")
                return

            # Check if full clean was requested
            full_clean_requested = confirmation_dialog.is_full_clean_requested()

            if full_clean_requested:
                print("Full clean and scan requested")
                # Save favorites before cleanup
                saved_favorites = self._save_favorites_before_cleanup()
                # Perform cleanup before scanning
                self._perform_full_cleanup()
                # Store favorites to restore after scan
                self._favorites_to_restore = saved_favorites
            else:
                self._favorites_to_restore = {}

            # Create progress dialog
            self.progress_dialog = ScanProgressDialog(self)

            # Create and configure scanner thread
            self.scanner_thread = ScannerThread(
                self.scanner, directories, full_scan=full_clean_requested
            )
            self.scanner_thread.progress_updated.connect(self._on_scan_file_progress)
            self.scanner_thread.directory_progress_updated.connect(
                self._on_scan_directory_progress
            )
            self.scanner_thread.scan_complete.connect(self._on_scan_complete)
            self.scanner_thread.scan_error.connect(self._on_scan_error)

            # Start scanning
            self.scanner_thread.start()

            # Show progress dialog
            self.progress_dialog.exec()

        except Exception as e:
            print(f"Error during scanning: {e}")
            QMessageBox.critical(self, "Scan Error", f"Failed to start scanning: {e}")

    def _count_media_files(self, directory: Path, recursive: bool) -> int:
        """Count media files in a directory without processing them."""
        # Use the same supported extensions as the Scanner class
        SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"}

        count = 0
        try:
            if recursive:
                for ext in SUPPORTED_EXTENSIONS:
                    count += len(list(directory.rglob(f"*{ext}")))
                    count += len(list(directory.rglob(f"*{ext.upper()}")))
            else:
                for ext in SUPPORTED_EXTENSIONS:
                    count += len(list(directory.glob(f"*{ext}")))
                    count += len(list(directory.glob(f"*{ext.upper()}")))
        except Exception as e:
            print(f"Error counting files in {directory}: {e}")

        return count

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

    def _on_scan_complete(self, processed_count):
        """Handle scan completion."""
        print(f"Scanning completed. Processed {processed_count} files.")

        # Close progress dialog
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.accept()

        # Restore favorites if we had saved them before a full clean
        if hasattr(self, "_favorites_to_restore") and self._favorites_to_restore:
            self._restore_favorites_after_scan()
            self._favorites_to_restore = {}

        # Refresh filters after scanning
        self.refresh_filters()

        # Reload media after scanning
        self.load_all_media()

        # Show completion message
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Successfully processed {processed_count} media files.",
        )

    def _on_scan_error(self, error_message):
        """Handle scan errors."""
        print(f"Scan error: {error_message}")

        # Close progress dialog
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.reject()

        # Show error message
        QMessageBox.critical(
            self, "Scan Error", f"An error occurred during scanning:\n{error_message}"
        )

    def load_all_media(self):
        """Load all media from database."""
        try:
            self.all_media = self.db_manager.get_all_media()
            # Load favorite status from database
            self.db_manager.load_favorite_status(self.all_media)
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

    def on_context_refresh_metadata_requested(self, media):
        """Handle Refresh Metadata request from thumbnail context menu."""
        try:
            # Show confirmation dialog
            reply = QMessageBox.question(
                self,
                "Refresh Metadata",
                f"Refresh metadata for '{media.file_name}'?\n\n"
                "This will rescan the file and update the database entry and thumbnail.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                self._refresh_single_media_metadata(media)
        except Exception as e:
            self.logger.error(f"Error refreshing metadata from context menu: {e}")

    def _refresh_single_media_metadata(self, media):
        """Refresh metadata for a single media file."""
        try:
            from metascan.core.media import Media

            file_path = media.file_path

            if not file_path.exists():
                QMessageBox.warning(
                    self, "File Not Found", f"The file no longer exists:\n{file_path}"
                )
                return

            # Delete old metadata entry from database
            self.db_manager.delete_media(file_path)

            # Delete old thumbnail
            if self.thumbnail_cache:
                thumbnail_path = self.thumbnail_cache.get_thumbnail_path(file_path)
                if thumbnail_path and thumbnail_path.exists():
                    thumbnail_path.unlink()

            # Rescan the file - Scanner requires db_manager
            scanner = Scanner(self.db_manager)
            new_media = scanner._process_media_file(file_path)

            if new_media:
                # Add to database
                self.db_manager.save_media(new_media)

                # Generate new thumbnail
                if self.thumbnail_cache:
                    self.thumbnail_cache.get_or_create_thumbnail(file_path)

                # Update the in-memory media list
                for i, existing_media in enumerate(self.all_media):
                    if existing_media.file_path == file_path:
                        self.all_media[i] = new_media
                        break

                # Refresh the UI
                self.apply_all_filters()

                # Update metadata panel if this media is currently selected
                if (
                    self.thumbnail_view.selected_media
                    and self.thumbnail_view.selected_media.file_path == file_path
                ):
                    self.metadata_panel.display_metadata(new_media)

            else:
                QMessageBox.warning(
                    self, "Scan Failed", f"Failed to rescan file:\n{file_path}"
                )

        except Exception as e:
            self.logger.error(f"Error refreshing metadata for {media.file_path}: {e}")
            QMessageBox.critical(
                self, "Refresh Error", f"Failed to refresh metadata:\n{str(e)}"
            )

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
        self.thumbnail_view.refresh_metadata_requested.disconnect()

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
        new_thumbnail_view.refresh_metadata_requested.connect(
            self.on_context_refresh_metadata_requested
        )

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
            for media in media_list:
                try:
                    file_path = media.file_path

                    # 1. Delete from database
                    db_success = self.db_manager.delete_media(file_path)
                    if not db_success:
                        print(f"Warning: Media not found in database: {file_path}")

                    # 2. Move thumbnail to trash
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

                    # 3. Move media file to trash
                    if file_path.exists():
                        try:
                            self._move_to_trash(file_path)
                            print(f"Moved media file to trash: {file_path}")
                            deleted_count += 1
                        except Exception as e:
                            print(f"Failed to move media file to trash: {e}")
                            failed_files.append(file_path.name)
                            continue

                    # 4. Remove from all_media list
                    self.all_media = [
                        m for m in self.all_media if m.file_path != file_path
                    ]

                except Exception as e:
                    print(f"Error deleting {media.file_name}: {e}")
                    failed_files.append(media.file_name)

            # 5. Update filters and view once after all deletions
            self.refresh_filters()
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
                        raise RuntimeError("No suitable file manager found on Linux")
            else:
                raise OSError(f"Unsupported platform: {system}")

            print(f"Opened directory in file manager: {directory_path}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to open directory with file manager: {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error opening directory: {e}")

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


def main():
    app = QApplication(sys.argv)

    # Fix for Windows DirectWrite font issues
    if platform.system() == "Windows":
        # Set a reliable font that exists on Windows
        from PyQt6.QtGui import QFont

        default_font = QFont("Segoe UI", 9)  # Standard Windows font
        app.setFont(default_font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
