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
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.Box)
        layout = QVBoxLayout(panel)
        
        # Title
        title = QLabel("Filters")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 5px;")
        layout.addWidget(title)
        
        # Filter list (placeholder)
        filter_list = QListWidget()
        filter_list.addItems([
            "All Images",
            "ComfyUI",
            "SwarmUI", 
            "Fooocus",
            "Recent",
            "Favorites"
        ])
        layout.addWidget(filter_list)
        
        return panel
    
    def _create_thumbnail_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.Box)
        layout = QVBoxLayout(panel)
        
        # Title
        title = QLabel("Media Gallery")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 5px;")
        layout.addWidget(title)
        
        # Scroll area for thumbnails
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        grid_layout = QGridLayout(scroll_widget)
        
        # Add placeholder thumbnails
        for i in range(12):
            placeholder = QLabel(f"Image {i+1}")
            placeholder.setStyleSheet(
                "border: 1px solid #ccc; "
                "background-color: #f0f0f0; "
                "min-height: 150px; "
                "min-width: 150px;"
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid_layout.addWidget(placeholder, i // 4, i % 4)
        
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        layout.addWidget(scroll_area)
        
        return panel
    
    def _create_metadata_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameStyle(QFrame.Shape.Box)
        layout = QVBoxLayout(panel)
        
        # Title
        title = QLabel("Metadata")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 5px;")
        layout.addWidget(title)
        
        # Metadata display (placeholder)
        metadata_text = QLabel("Select an image to view metadata")
        metadata_text.setWordWrap(True)
        metadata_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        metadata_text.setStyleSheet("padding: 10px;")
        
        scroll_area = QScrollArea()
        scroll_area.setWidget(metadata_text)
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
            padding: 8px 20px;
            font-size: 14px;
            font-weight: bold;
            min-width: 100px;
            min-height: 30px;
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
            border: 2px solid #1976D2;
            padding: 8px 20px;
            font-size: 14px;
            font-weight: bold;
            min-width: 100px;
            min-height: 30px;
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
            
        except Exception as e:
            print(f"Error during scanning: {e}")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()