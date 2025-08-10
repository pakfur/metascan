import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, 
    QVBoxLayout, QListWidget, QLabel, QSplitter,
    QScrollArea, QGridLayout, QFrame, QPushButton,
    QToolBar, QMessageBox, QProgressBar, QDialog,
    QDialogButtonBox, QCheckBox
)
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from metascan.ui.config_dialog import ConfigDialog
from metascan.ui.filters_panel import FiltersPanel
from metascan.ui.thumbnail_view import ThumbnailView
from metascan.ui.virtual_thumbnail_view import VirtualThumbnailView
from metascan.ui.metadata_panel import MetadataPanel
from metascan.ui.media_viewer import MediaViewer
from metascan.core.scanner import Scanner, ThreadedScanner
from metascan.core.database_sqlite import DatabaseManager
from metascan.cache.thumbnail import ThumbnailCache
import os
import json
from pathlib import Path
import shutil
import platform


class ScannerThread(QThread):
    """Thread for running the scanner with progress reporting."""
    progress_updated = pyqtSignal(int, int, str)  # current, total, current_file
    scan_complete = pyqtSignal(int)  # processed_count
    scan_error = pyqtSignal(str)  # error message
    
    def __init__(self, scanner, directories):
        super().__init__()
        self.scanner = scanner
        self.directories = directories
        self._is_cancelled = False
        
    def cancel(self):
        """Request cancellation of the scan."""
        self._is_cancelled = True
        # If using ThreadedScanner, signal it to stop
        if hasattr(self.scanner, 'stop_scanning'):
            self.scanner.stop_scanning()
        
    def run(self):
        """Run the scanning process."""
        total_processed = 0
        
        try:
            for dir_info in self.directories:
                if self._is_cancelled:
                    break
                    
                # Define progress callback
                def progress_callback(current, total, file_path):
                    if self._is_cancelled:
                        return False  # Return False to stop scanning
                    self.progress_updated.emit(current, total, str(file_path))
                    return True  # Continue scanning
                
                # Scan directory with progress callback
                processed = self.scanner.scan_directory(
                    dir_info['filepath'],
                    recursive=dir_info['search_subfolders'],
                    progress_callback=progress_callback
                )
                total_processed += processed
                
            if not self._is_cancelled:
                self.scan_complete.emit(total_processed)
        except Exception as e:
            self.scan_error.emit(str(e))


class ScanProgressDialog(QDialog):
    """Dialog showing scan progress with cancel option."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scanning Media Files (Threaded)")
        self.setModal(True)
        self.setFixedSize(550, 180)
        
        # Layout
        layout = QVBoxLayout(self)
        
        # Current file label
        self.file_label = QLabel("Preparing to scan...")
        layout.addWidget(self.file_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)
        
        # Progress text
        self.progress_label = QLabel("0 / 0 files")
        layout.addWidget(self.progress_label)
        
        # Threading info label
        self.thread_info_label = QLabel("Multi-threaded scanning: 4 workers + batch database writes")
        self.thread_info_label.setStyleSheet("color: #666; font-size: 10px; font-style: italic;")
        layout.addWidget(self.thread_info_label)
        
        # Cancel button
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.on_cancel_clicked)
        layout.addWidget(cancel_button)
        
        self.cancel_requested = False
        
    def update_progress(self, current, total, file_path):
        """Update the progress display."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
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
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.cancel_requested = True
            self.file_label.setText("Cancelling...")


class ScanConfirmationDialog(QDialog):
    """Dialog for confirming scan with optional full clean."""
    
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
        info_text += "This operation may take several minutes depending on the number of files."
        
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Full clean checkbox
        self.full_clean_checkbox = QCheckBox("Full clean and scan")
        self.full_clean_checkbox.setToolTip(
            "Clear all existing data (media records, indices, and thumbnails) before scanning.\n"
            "This ensures a completely fresh start but will remove all previously scanned data."
        )
        self.full_clean_checkbox.setChecked(False)
        self.full_clean_checkbox.setStyleSheet("""
            QCheckBox {
                font-weight: bold;
                padding: 8px;
                color: #d32f2f;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                border: 2px solid #ccc;
                background-color: white;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #d32f2f;
                background-color: #d32f2f;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.full_clean_checkbox)
        
        # Question
        question_label = QLabel("Do you want to continue?")
        question_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(question_label)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No,
            Qt.Orientation.Horizontal
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
        self.setGeometry(100, 100, 1200, 800)
        
        # Initialize components
        self.config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.json')
        db_path = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / 'data'
        self.db_manager = DatabaseManager(db_path)
        self.scanner = ThreadedScanner(self.db_manager, num_workers=4, batch_size=10)
        
        # Current filter state
        self.current_filters = {}
        self.filtered_media_paths = set()
        self.favorites_active = False  # Track if favorites filter is active
        self.all_media = []  # Cache of all media for filtering
        
        # Initialize thumbnail cache for metadata panel
        cache_dir = Path.home() / ".metascan" / "thumbnails"
        self.thumbnail_cache = ThumbnailCache(cache_dir, (200, 200))
        
        # Create media viewer (initially hidden)
        self.media_viewer = MediaViewer()  # Create without parent to control positioning
        self.media_viewer.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.media_viewer.closed.connect(self.on_media_viewer_closed)
        self.media_viewer.media_changed.connect(self.on_viewer_media_changed)
        self.media_viewer.delete_requested.connect(lambda media: self._confirm_and_delete_media(media, from_viewer=True))
        self.media_viewer.favorite_toggled.connect(self.on_viewer_favorite_toggled)
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Create splitter for three panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left panel - Filters
        filter_panel = self._create_filter_panel()
        splitter.addWidget(filter_panel)
        
        # Middle panel - Thumbnails
        thumbnail_panel = self._create_thumbnail_panel()
        splitter.addWidget(thumbnail_panel)
        
        # Right panel - Metadata
        metadata_panel = self._create_metadata_panel()
        splitter.addWidget(metadata_panel)
        
        # Set initial splitter sizes (proportional)
        splitter.setSizes([250, 600, 350])
        
        # Create menu bar
        self._create_menu_bar()
        
        # Create toolbar
        self._create_toolbar()
        
        # Setup keyboard shortcuts
        self._setup_shortcuts()
        
    def _create_filter_panel(self) -> QWidget:
        # Create the filters panel
        self.filters_panel = FiltersPanel()
        
        # Connect signals
        self.filters_panel.filters_changed.connect(self.on_filters_changed)
        self.filters_panel.sort_changed.connect(self.on_sort_order_changed)
        self.filters_panel.favorites_toggled.connect(self.on_favorites_toggled)
        self.filters_panel.set_refresh_callback(self.refresh_filters)
        
        # Load initial filter data
        self.refresh_filters()
        
        return self.filters_panel
    
    def _create_thumbnail_panel(self) -> QWidget:
        # Create the thumbnail view
        self.thumbnail_view = VirtualThumbnailView()
        
        # Connect selection signal
        self.thumbnail_view.selection_changed.connect(self.on_thumbnail_selected)
        self.thumbnail_view.favorite_toggled.connect(self.on_favorite_toggled)
        
        # Connect double-click to open media viewer
        self.thumbnail_view.scroll_area.item_double_clicked.connect(self.on_thumbnail_double_clicked)
        
        # Load initial media if any exists
        self.load_all_media()
        
        return self.thumbnail_view
    
    def _create_metadata_panel(self) -> QWidget:
        # Create the enhanced metadata panel
        self.metadata_panel = MetadataPanel()
        
        # Set the thumbnail cache for preview images
        self.metadata_panel.set_thumbnail_cache(self.thumbnail_cache)
        
        return self.metadata_panel
    
    def _create_menu_bar(self):
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        open_action = QAction("Open Folder", self)
        open_action.setShortcut("Ctrl+O")
        file_menu.addAction(open_action)
        
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
        view_menu.addAction(refresh_action)
    
    def _create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Define button style
        button_style = """
        QPushButton {
            background-color: #4CAF50;
            color: white;
            border: 2px solid #45a049;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: bold;
            min-width: 75px;
            min-height: 20px;
        }
        QPushButton:hover {
            background-color: #45a049;
            border-color: #3d8b40;
        }
        QPushButton:pressed {
            background-color: #3d8b40;
        }
        """
        
        config_button_style = """
        QPushButton {
            background-color: #2196F3;
            color: white;
            border: 2px solid #45a049;
            padding: 4px 10px;
            font-size: 10px;
            font-weight: bold;
            min-width: 75px;
            min-height: 20px;
        }
        QPushButton:hover {
            background-color: #1976D2;
            border-color: #1565C0;
        }
        QPushButton:pressed {
            background-color: #1565C0;
        }
        """
        
        # Scan button
        scan_button = QPushButton("Scan")
        scan_button.setStyleSheet(button_style)
        scan_button.clicked.connect(self._scan_directories)
        toolbar.addWidget(scan_button)
        
        # Add some spacing
        toolbar.addSeparator()
        
        # Config button
        config_button = QPushButton("Config")
        config_button.setStyleSheet(config_button_style)
        config_button.clicked.connect(self._open_config)
        toolbar.addWidget(config_button)
    
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
            with open(self.config_file, 'r') as f:
                config = json.load(f)
                directories = config.get('directories', [])
                
            if not directories:
                print("No directories configured. Please configure directories first.")
                self._open_config()
                return
            
            # Count directories and estimate media files
            total_dirs = len(directories)
            total_files = 0
            
            # Count media files in each directory
            for dir_info in directories:
                dir_path = Path(dir_info['filepath'])
                if dir_path.exists():
                    recursive = dir_info.get('search_subfolders', True)
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
                # Perform cleanup before scanning
                self._perform_full_cleanup()
                
            # Create progress dialog
            self.progress_dialog = ScanProgressDialog(self)
            
            # Create and configure scanner thread
            self.scanner_thread = ScannerThread(self.scanner, directories)
            self.scanner_thread.progress_updated.connect(self._on_scan_progress)
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
        SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4'}
        
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
    
    def _on_scan_progress(self, current, total, file_path):
        """Handle scan progress updates."""
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.update_progress(current, total, file_path)
            
            # Check if cancellation was requested
            if self.progress_dialog.cancel_requested:
                self.scanner_thread.cancel()
    
    def _on_scan_complete(self, processed_count):
        """Handle scan completion."""
        print(f"Scanning completed. Processed {processed_count} files.")
        
        # Close progress dialog
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.accept()
        
        # Refresh filters after scanning
        self.refresh_filters()
        
        # Reload media after scanning
        self.load_all_media()
        
        # Show completion message
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Successfully processed {processed_count} media files."
        )
    
    def _on_scan_error(self, error_message):
        """Handle scan errors."""
        print(f"Scan error: {error_message}")
        
        # Close progress dialog
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.reject()
        
        # Show error message
        QMessageBox.critical(
            self,
            "Scan Error",
            f"An error occurred during scanning:\n{error_message}"
        )
    
    def load_all_media(self):
        """Load all media from database."""
        try:
            self.all_media = self.db_manager.get_all_media()
            # Load favorite status from database
            self.db_manager.load_favorite_status(self.all_media)
            self.thumbnail_view.set_media_list(self.all_media)
            print(f"Loaded {len(self.all_media)} media items")
        except Exception as e:
            print(f"Error loading media: {e}")
            self.all_media = []
    
    def refresh_filters(self):
        """Refresh the filters panel with current database data."""
        try:
            sort_order = self.filters_panel.get_sort_order()
            filter_data = self.db_manager.get_filter_data(sort_order)
            self.filters_panel.update_filters(filter_data)
            print(f"Filters refreshed with {sort_order} sort. Found {len(filter_data)} filter types.")
        except Exception as e:
            print(f"Error refreshing filters: {e}")
    
    def on_sort_order_changed(self, sort_order: str):
        """Handle when sort order is changed."""
        print(f"Sort order changed to: {sort_order}")
        self.refresh_filters()
    
    def on_filters_changed(self, filters: dict):
        """Handle when filter selections change."""
        self.current_filters = filters
        self.apply_all_filters()
    
    def on_favorites_toggled(self, is_active: bool):
        """Handle when favorites filter is toggled."""
        self.favorites_active = is_active
        print(f"Favorites filter {'activated' if is_active else 'deactivated'}")
        self.apply_all_filters()
    
    def on_favorite_toggled(self, media):
        """Handle when a media item's favorite status is toggled."""
        try:
            # Update database
            success = self.db_manager.set_favorite(media.file_path, media.is_favorite)
            if success:
                print(f"{'Added to' if media.is_favorite else 'Removed from'} favorites: {media.file_name}")
                
                # If favorites filter is active, reapply filters
                if self.favorites_active:
                    self.apply_all_filters()
            else:
                print(f"Failed to update favorite status for {media.file_name}")
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
                filtered_paths = self.db_manager.get_filtered_media_paths(self.current_filters)
                print(f"Filters applied: {self.current_filters}")
                print(f"Found {len(filtered_paths)} matching media files")
            else:
                # No filters - start with all media
                filtered_paths = {str(media.file_path) for media in self.all_media}
                print("No filters applied - starting with all media")
            
            # Apply favorites filter if active
            if self.favorites_active:
                favorite_paths = self.db_manager.get_favorite_media_paths()
                # Intersect with current filtered paths
                self.filtered_media_paths = filtered_paths & favorite_paths
                print(f"Favorites filter applied: {len(self.filtered_media_paths)} items after favorites filter")
            else:
                self.filtered_media_paths = filtered_paths
            
            # Update thumbnail view
            self.thumbnail_view.apply_filters(self.filtered_media_paths)
        except Exception as e:
            print(f"Error applying filters: {e}")
    
    def on_thumbnail_selected(self, media):
        """Handle when a thumbnail is selected."""
        try:
            # Update metadata panel
            self.metadata_panel.display_metadata(media)
            print(f"Selected: {media.file_name}")
        except Exception as e:
            print(f"Error handling thumbnail selection: {e}")
    
    def on_thumbnail_double_clicked(self, media):
        """Handle when a thumbnail is double-clicked."""
        try:
            print(f"Opening media viewer for: {media.file_name}")
            
            # Get the currently filtered media list
            if self.filtered_media_paths:
                # Use filtered list
                filtered_media = [m for m in self.all_media 
                                if str(m.file_path) in self.filtered_media_paths]
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
        print("Media viewer closed")
        # Return focus to main window
        self.activateWindow()
        self.thumbnail_view.setFocus()
        # Do not refresh filters here - they should maintain their state
    
    def on_viewer_media_changed(self, media):
        """Handle when media changes in the viewer."""
        try:
            # Update metadata panel to show current viewed media
            self.metadata_panel.display_metadata(media)
            print(f"Viewer showing: {media.file_name}")
        except Exception as e:
            print(f"Error updating metadata for viewer: {e}")
    
    def on_viewer_favorite_toggled(self, media, is_favorite):
        """Handle favorite toggle from media viewer."""
        import logging
        logger = logging.getLogger(__name__)
        
        if self.db_manager:
            success = self.db_manager.set_favorite(media.file_path, is_favorite)
            if success:
                logger.info(f"Updated favorite status for {media.file_name}: {is_favorite}")
                # Update the media in all_media list
                for m in self.all_media:
                    if m.file_path == media.file_path:
                        m.is_favorite = is_favorite
                        break
                # Refresh thumbnails to show updated favorite status if virtualization is enabled
                if hasattr(self, 'virtual_view') and self.virtual_view:
                    if hasattr(self.virtual_view, 'refresh_thumbnails'):
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
            # Delete from thumbnail view
            selected_media = self.thumbnail_view.get_selected_media()
            if selected_media:
                self._confirm_and_delete_media(selected_media, from_viewer=False)
    
    def _delete_from_viewer(self):
        """Handle delete from the media viewer."""
        if self.media_viewer.current_media:
            self._confirm_and_delete_media(self.media_viewer.current_media, from_viewer=True)
    
    def _confirm_and_delete_media(self, media, from_viewer=False):
        """Show confirmation dialog and delete media if confirmed."""
        # Create confirmation dialog
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Delete Media")
        msg_box.setText("Delete this file?")
        msg_box.setInformativeText(f"File: {media.file_name}")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        
        # Focus on OK button
        ok_button = msg_box.button(QMessageBox.StandardButton.Ok)
        if ok_button:
            ok_button.setFocus()
        
        # Show dialog and handle response
        if msg_box.exec() == QMessageBox.StandardButton.Ok:
            self._delete_media(media, from_viewer)
    
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
                    QMessageBox.critical(self, "Delete Error", f"Failed to move file to trash: {e}")
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
                    self.media_viewer.current_index = max(0, min(current_index, len(media_list) - 1))
                    self.media_viewer._display_current_media()
            
            # 7. Update thumbnail view (remove from display)
            self.apply_all_filters()
            
            print(f"Successfully deleted: {file_path.name}")
            
        except Exception as e:
            print(f"Error deleting media: {e}")
            QMessageBox.critical(self, "Delete Error", f"Failed to delete media: {e}")
    
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
            self.filtered_media_paths.clear()
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
                f"Some cleanup operations failed: {e}\n\nScanning will continue, but some old data may remain."
            )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()