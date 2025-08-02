import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, 
    QVBoxLayout, QListWidget, QLabel, QSplitter,
    QScrollArea, QGridLayout, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Metascan - AI Media Browser")
        self.setGeometry(100, 100, 1200, 800)
        
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
        
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("F5")
        view_menu.addAction(refresh_action)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()