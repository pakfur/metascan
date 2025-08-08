import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, 
    QVBoxLayout, QListWidget, QLabel, QSplitter,
    QScrollArea, QGridLayout, QFrame, QPushButton,
    QToolBar
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from metascan.ui.config_dialog import ConfigDialog
from metascan.ui.filters_panel import FiltersPanel
from metascan.ui.thumbnail_view import ThumbnailView
from metascan.ui.virtual_thumbnail_view import VirtualThumbnailView
from metascan.ui.metadata_panel import MetadataPanel
from metascan.ui.media_viewer import MediaViewer
from metascan.core.scanner import Scanner
from metascan.core.database_sqlite import DatabaseManager
from metascan.cache.thumbnail import ThumbnailCache
import os
import json
from pathlib import Path


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Metascan - AI Media Browser")
        self.setGeometry(100, 100, 1200, 800)
        
        # Initialize components
        self.config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.json')
        db_path = Path(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))) / 'data'
        self.db_manager = DatabaseManager(db_path)
        self.scanner = Scanner(self.db_manager)
        
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
                
            # Scan each directory
            for dir_info in directories:
                print(f"Scanning {dir_info['filepath']}...")
                self.scanner.scan_directory(
                    dir_info['filepath'], 
                    recursive=dir_info['search_subfolders']
                )
                
            print("Scanning completed.")
            
            # Refresh filters after scanning
            self.refresh_filters()
            
            # Reload media after scanning
            self.load_all_media()
            
        except Exception as e:
            print(f"Error during scanning: {e}")
    
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
    
    def on_viewer_media_changed(self, media):
        """Handle when media changes in the viewer."""
        try:
            # Update metadata panel to show current viewed media
            self.metadata_panel.display_metadata(media)
            print(f"Viewer showing: {media.file_name}")
        except Exception as e:
            print(f"Error updating metadata for viewer: {e}")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()