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
from metascan.core.scanner import Scanner
from metascan.core.database_sqlite import DatabaseManager
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
        self.all_media = []  # Cache of all media for filtering
        
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
        self.filters_panel.set_refresh_callback(self.refresh_filters)
        
        # Load initial filter data
        self.refresh_filters()
        
        return self.filters_panel
    
    def _create_thumbnail_panel(self) -> QWidget:
        # Create the thumbnail view
        self.thumbnail_view = ThumbnailView()
        
        # Connect selection signal
        self.thumbnail_view.selection_changed.connect(self.on_thumbnail_selected)
        
        # Load initial media if any exists
        self.load_all_media()
        
        return self.thumbnail_view
    
    def _create_metadata_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.Box)
        layout = QVBoxLayout(panel)
        
        # Title
        title = QLabel("Metadata")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 5px;")
        layout.addWidget(title)
        
        # Metadata display
        self.metadata_text = QLabel("Select an image to view metadata")
        self.metadata_text.setWordWrap(True)
        self.metadata_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.metadata_text.setStyleSheet("padding: 10px; font-family: monospace; font-size: 11px;")
        
        scroll_area = QScrollArea()
        scroll_area.setWidget(self.metadata_text)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)
        
        return panel
    
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
            self.thumbnail_view.set_media_list(self.all_media)
            print(f"Loaded {len(self.all_media)} media items")
        except Exception as e:
            print(f"Error loading media: {e}")
            self.all_media = []
    
    def refresh_filters(self):
        """Refresh the filters panel with current database data."""
        try:
            filter_data = self.db_manager.get_filter_data()
            self.filters_panel.update_filters(filter_data)
            print(f"Filters refreshed. Found {len(filter_data)} filter types.")
        except Exception as e:
            print(f"Error refreshing filters: {e}")
    
    def on_filters_changed(self, filters: dict):
        """Handle when filter selections change."""
        self.current_filters = filters
        
        if filters:
            # Get filtered media paths
            self.filtered_media_paths = self.db_manager.get_filtered_media_paths(filters)
            print(f"Filters applied: {filters}")
            print(f"Found {len(self.filtered_media_paths)} matching media files")
        else:
            # No filters selected - show all
            self.filtered_media_paths = set()
            print("All filters cleared - showing all media")
        
        # Update thumbnail view with filtered results
        self.thumbnail_view.apply_filters(self.filtered_media_paths)
    
    def on_thumbnail_selected(self, media):
        """Handle when a thumbnail is selected."""
        try:
            # Update metadata panel
            self.display_metadata(media)
            print(f"Selected: {media.file_name}")
        except Exception as e:
            print(f"Error handling thumbnail selection: {e}")
    
    def display_metadata(self, media):
        """Display metadata for the selected media."""
        metadata_lines = [
            f"File: {media.file_name}",
            f"Path: {media.file_path}",
            f"Size: {media.file_size} bytes",
            f"Dimensions: {media.width} x {media.height}",
            f"Format: {media.format}",
            f"Created: {media.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Modified: {media.modified_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "Generation Data:",
            f"Source: {media.metadata_source or 'Unknown'}",
            f"Model: {media.model or 'Unknown'}",
            f"Sampler: {media.sampler or 'Unknown'}",
            f"Steps: {media.steps or 'Unknown'}",
            f"CFG Scale: {media.cfg_scale or 'Unknown'}",
            f"Seed: {media.seed or 'Unknown'}",
            "",
        ]
        
        if media.prompt:
            metadata_lines.extend([
                "Prompt:",
                f"{media.prompt}",
                ""
            ])
        
        if media.negative_prompt:
            metadata_lines.extend([
                "Negative Prompt:",
                f"{media.negative_prompt}",
                ""
            ])
        
        if media.tags:
            metadata_lines.extend([
                "Tags:",
                ", ".join(media.tags),
                ""
            ])
        
        metadata_text = "\n".join(metadata_lines)
        self.metadata_text.setText(metadata_text)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()