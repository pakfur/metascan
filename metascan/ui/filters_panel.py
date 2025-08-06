from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QCheckBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from typing import Dict, List, Set, Any


class FilterSection(QFrame):
    """Individual filter section with collapsible content."""
    
    selection_changed = pyqtSignal()
    
    def __init__(self, section_name: str, filter_items: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.section_name = section_name
        self.filter_items = filter_items
        self.is_expanded = False
        self.checkboxes = {}
        
        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header button
        self.header_button = QPushButton()
        self.header_button.setCheckable(True)
        self.header_button.setText(f"▶ {self.section_name.title()} ({len(self.filter_items)})")
        self.header_button.setStyleSheet("""
            QPushButton {
                background-color: #e0e0e0;
                border: none;
                padding: 8px;
                text-align: left;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d0d0d0;
            }
            QPushButton:checked {
                background-color: #c0c0c0;
            }
        """)
        self.header_button.clicked.connect(self.toggle_section)
        layout.addWidget(self.header_button)
        
        # Content area (initially hidden)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 5, 10, 5)
        self.content_layout.setSpacing(2)
        
        # Add checkboxes for each filter item
        for item in self.filter_items:
            checkbox = QCheckBox(f"{item['key']} ({item['count']})")
            checkbox.setObjectName(item['key'])  # Store the key for easy retrieval
            checkbox.stateChanged.connect(self.on_checkbox_changed)
            self.checkboxes[item['key']] = checkbox
            self.content_layout.addWidget(checkbox)
        
        # Add content to scroll area if there are many items
        if len(self.filter_items) > 10:
            scroll_area = QScrollArea()
            scroll_area.setWidget(self.content_widget)
            scroll_area.setWidgetResizable(True)
            scroll_area.setMaximumHeight(200)  # Limit height
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            layout.addWidget(scroll_area)
        else:
            layout.addWidget(self.content_widget)
        
        # Initially hide content
        self.content_widget.setVisible(False)
    
    def toggle_section(self):
        """Toggle the expanded/collapsed state of the section."""
        self.is_expanded = not self.is_expanded
        self.content_widget.setVisible(self.is_expanded)
        
        # Update header button text and icon
        icon = "▼" if self.is_expanded else "▶"
        self.header_button.setText(f"{icon} {self.section_name.title()} ({len(self.filter_items)})")
        self.header_button.setChecked(self.is_expanded)
    
    def on_checkbox_changed(self):
        """Handle checkbox state changes."""
        self.selection_changed.emit()
    
    def get_selected_items(self) -> List[str]:
        """Get list of selected filter keys."""
        selected = []
        for key, checkbox in self.checkboxes.items():
            if checkbox.isChecked():
                selected.append(key)
        return selected
    
    def clear_selection(self):
        """Clear all checkbox selections."""
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(False)


class FiltersPanel(QWidget):
    """Main filters panel with accordion-style sections."""
    
    filters_changed = pyqtSignal(dict)  # Emits current filter selections
    sort_changed = pyqtSignal(str)  # Emits sort order: "count" or "alphabetical"
    favorites_toggled = pyqtSignal(bool)  # Emits when favorites filter is toggled
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_sections = {}
        self.sort_order = "count"  # Default sort by count
        self.favorites_checkbox = None
        self.setup_ui()
    
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # Title
        title = QLabel("Filters")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        title.setFont(title_font)
        title.setStyleSheet("padding: 5px; background-color: #f0f0f0; border: 1px solid #ccc;")
        main_layout.addWidget(title)
        
        # Control buttons
        button_layout = QHBoxLayout()
        
        self.clear_all_button = QPushButton("Clear All")
        self.clear_all_button.setStyleSheet("""
            QPushButton {
                background-color: #ff6b6b;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #ff5252;
            }
        """)
        self.clear_all_button.clicked.connect(self.clear_all_filters)
        button_layout.addWidget(self.clear_all_button)
        
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.refresh_button)
        
        # Sort order controls
        sort_separator = QLabel("|")
        sort_separator.setStyleSheet("color: #ccc; font-weight: bold; padding: 0 5px;")
        button_layout.addWidget(sort_separator)
        
        # Sort by count button
        self.sort_count_button = QPushButton("🔢")
        self.sort_count_button.setToolTip("Sort by count (most common first)")
        self.sort_count_button.setFixedSize(30, 30)
        self.sort_count_button.setCheckable(True)
        self.sort_count_button.setChecked(True)  # Default active
        self.sort_count_button.setStyleSheet("""
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #e8f5e8;
                font-size: 14px;
                padding: 2px;
            }
            QPushButton:checked {
                background-color: #4CAF50;
                color: white;
                border-color: #45a049;
            }
            QPushButton:hover {
                border-color: #4CAF50;
            }
        """)
        self.sort_count_button.clicked.connect(lambda: self.set_sort_order("count"))
        button_layout.addWidget(self.sort_count_button)
        
        # Sort alphabetically button
        self.sort_alpha_button = QPushButton("🔤")
        self.sort_alpha_button.setToolTip("Sort alphabetically (A-Z)")
        self.sort_alpha_button.setFixedSize(30, 30)
        self.sort_alpha_button.setCheckable(True)
        self.sort_alpha_button.setStyleSheet("""
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f0f0f0;
                font-size: 14px;
                padding: 2px;
            }
            QPushButton:checked {
                background-color: #2196F3;
                color: white;
                border-color: #1976D2;
            }
            QPushButton:hover {
                border-color: #2196F3;
            }
        """)
        self.sort_alpha_button.clicked.connect(lambda: self.set_sort_order("alphabetical"))
        button_layout.addWidget(self.sort_alpha_button)
        
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # Favorites filter checkbox (always visible at top)
        self.favorites_checkbox = QCheckBox("★ Favorites")
        self.favorites_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 13px;
                font-weight: bold;
                padding: 8px;
                background-color: #fff9e6;
                border: 1px solid #ffc107;
                border-radius: 4px;
                margin: 5px 0;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                border: 2px solid #ffc107;
                background-color: white;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #ffc107;
                background-color: #ffc107;
                border-radius: 3px;
            }
            QCheckBox:hover {
                background-color: #fff3cd;
            }
        """)
        self.favorites_checkbox.setToolTip("Show only favorite items")
        self.favorites_checkbox.stateChanged.connect(self.on_favorites_toggled)
        main_layout.addWidget(self.favorites_checkbox)
        
        # Scroll area for filter sections
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(2)
        
        self.scroll_area.setWidget(self.scroll_widget)
        main_layout.addWidget(self.scroll_area)
        
        # Add stretch to push everything to top
        self.scroll_layout.addStretch()
    
    def update_filters(self, filter_data: Dict[str, List[Dict[str, Any]]]):
        """Update the filter sections with new data."""
        # Clear existing sections
        self.clear_sections()
        
        # Create new sections
        for section_name, items in filter_data.items():
            if items:  # Only create sections with items
                section = FilterSection(section_name, items, self)
                section.selection_changed.connect(self.on_filter_selection_changed)
                
                # Insert before the stretch
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, section)
                self.filter_sections[section_name] = section
    
    def clear_sections(self):
        """Remove all filter sections."""
        for section in self.filter_sections.values():
            section.deleteLater()
        self.filter_sections.clear()
    
    def on_filter_selection_changed(self):
        """Handle when any filter selection changes."""
        current_filters = self.get_current_filters()
        self.filters_changed.emit(current_filters)
    
    def get_current_filters(self) -> Dict[str, List[str]]:
        """Get the current filter selections."""
        filters = {}
        for section_name, section in self.filter_sections.items():
            selected_items = section.get_selected_items()
            if selected_items:
                filters[section_name] = selected_items
        return filters
    
    def clear_all_filters(self):
        """Clear all filter selections."""
        for section in self.filter_sections.values():
            section.clear_selection()
        self.on_filter_selection_changed()  # Emit the change
    
    def set_sort_order(self, sort_order: str):
        """Set the sort order and update UI."""
        if sort_order == self.sort_order:
            return  # No change needed
        
        self.sort_order = sort_order
        
        # Update button states
        if sort_order == "count":
            self.sort_count_button.setChecked(True)
            self.sort_alpha_button.setChecked(False)
        else:  # alphabetical
            self.sort_count_button.setChecked(False)
            self.sort_alpha_button.setChecked(True)
        
        # Emit signal for parent to handle
        self.sort_changed.emit(sort_order)
    
    def get_sort_order(self) -> str:
        """Get the current sort order."""
        return self.sort_order
    
    def set_refresh_callback(self, callback):
        """Set the callback function for the refresh button."""
        self.refresh_button.clicked.connect(callback)
    
    def on_favorites_toggled(self):
        """Handle favorites checkbox toggle."""
        is_checked = self.favorites_checkbox.isChecked()
        self.favorites_toggled.emit(is_checked)
    
    def is_favorites_active(self) -> bool:
        """Check if favorites filter is active."""
        return self.favorites_checkbox.isChecked() if self.favorites_checkbox else False