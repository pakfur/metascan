from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QCheckBox,
    QFrame,
    QSizePolicy,
    QLineEdit,
    QMessageBox,
    QToolButton,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from typing import Dict, List, Set, Any, Optional

from metascan.ui.path_filter_tree import PathFilterTree


class FilterSection(QFrame):
    """Individual filter section with collapsible content.

    Uses lazy loading to defer checkbox creation until the section is first expanded.
    This significantly improves startup performance when there are many filter items.
    """

    selection_changed = pyqtSignal()

    def __init__(
        self, section_name: str, filter_items: List[Dict[str, Any]], parent=None
    ):
        super().__init__(parent)
        self.section_name = section_name
        self.items_list = filter_items
        self.all_items = filter_items.copy()  # Keep original list for filtering
        self.is_expanded = False  # Start collapsed for lazy loading
        self.checkboxes: Dict[str, QCheckBox] = {}
        self.toggleable_widget: Optional[QWidget] = (
            None  # Widget to show/hide when toggling
        )
        self._content_initialized = False  # Track if checkboxes have been created

        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)

        # Set size policy to expand vertically with minimum height
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(100)

        self._setup_header()

    def _setup_header(self):
        """Set up only the header and scroll area container - defer checkbox creation."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header container for button and clear icon
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        # Header button - start collapsed
        self.header_button = QPushButton()
        self.header_button.setCheckable(True)
        self.header_button.setText(
            f"▶ {self.section_name.title()} ({len(self.items_list)})"
        )
        # Use theme styling for header button
        self.header_button.clicked.connect(self.toggle_section)
        header_layout.addWidget(self.header_button, 1)  # Stretch to fill space

        # Clear selection button
        self.clear_button = QToolButton()
        self.clear_button.setText("✕")
        self.clear_button.setToolTip(f"Clear all selections in {self.section_name}")
        self.clear_button.setStyleSheet(
            """
            QToolButton {
                border: none;
                padding: 2px 6px;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: rgba(255, 0, 0, 30);
                border-radius: 3px;
            }
        """
        )
        self.clear_button.clicked.connect(self.on_clear_section)
        self.clear_button.setVisible(False)  # Initially hidden
        header_layout.addWidget(self.clear_button)

        layout.addWidget(header_container)

        # Content area - create container but don't populate with checkboxes yet
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 5, 10, 5)
        self.content_layout.setSpacing(2)

        # Content widget should size itself based on checkboxes (fixed height each)
        self.content_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        scroll_area = QScrollArea()
        scroll_area.setWidget(self.content_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Scroll area expands to show more items as panel grows
        scroll_area.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(scroll_area, 1)  # Stretch factor to expand
        self.toggleable_widget = scroll_area

        # Start collapsed for lazy loading
        if self.toggleable_widget:
            self.toggleable_widget.setVisible(False)
        self.header_button.setChecked(False)

    def _create_checkboxes(self):
        """Create checkbox widgets - called lazily on first expand."""
        if self._content_initialized:
            return

        for item in self.items_list:
            checkbox = QCheckBox(f"{item['key']} ({item['count']})")
            checkbox.setObjectName(item["key"])  # Store the key for easy retrieval
            checkbox.stateChanged.connect(self.on_checkbox_changed)

            # Ensure fixed height for checkbox - don't expand vertically
            checkbox.setMinimumHeight(20)
            checkbox.setMaximumHeight(30)
            checkbox.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
            )

            self.checkboxes[item["key"]] = checkbox
            self.content_layout.addWidget(checkbox)

        self._content_initialized = True

    def setup_ui(self):
        """Legacy method for compatibility - now handled by _setup_header."""
        pass

    def toggle_section(self):
        """Toggle the expanded/collapsed state of the section."""
        # Create checkboxes on first expand (lazy loading)
        if not self._content_initialized and not self.is_expanded:
            self._create_checkboxes()

        self.is_expanded = not self.is_expanded
        if self.toggleable_widget:
            self.toggleable_widget.setVisible(self.is_expanded)

        # Update header button text and icon
        icon = "▼" if self.is_expanded else "▶"
        self.header_button.setText(
            f"{icon} {self.section_name.title()} ({len(self.items_list)})"
        )
        self.header_button.setChecked(self.is_expanded)

    def on_checkbox_changed(self):
        """Handle checkbox state changes."""
        # Show/hide clear button based on whether any items are selected
        has_selection = any(cb.isChecked() for cb in self.checkboxes.values())
        self.clear_button.setVisible(has_selection)
        self.selection_changed.emit()

    def on_clear_section(self):
        """Handle clear button click with confirmation."""
        # Check if there are any selected items
        if not self.get_selected_items():
            return

        # Show confirmation dialog
        reply = QMessageBox.question(
            self,
            "Clear Filters",
            f"Clear all filters in {self.section_name.title()} section?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.clear_selection()
            self.clear_button.setVisible(False)
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

    def filter_items(self, filter_text: str):
        """Filter displayed items based on text."""
        # Filter items
        if filter_text:
            filtered_items = [
                item
                for item in self.all_items
                if item["key"].lower().startswith(filter_text.lower())
            ]
        else:
            filtered_items = self.all_items

        # Update items_list for display
        self.items_list = filtered_items

        # Update header count
        icon = "▼" if self.is_expanded else "▶"
        self.header_button.setText(
            f"{icon} {self.section_name.title()} ({len(filtered_items)})"
        )

        # If content hasn't been initialized yet, just update the items list
        # Checkboxes will be created with filtered items on first expand
        if not self._content_initialized:
            return

        # Store current selections
        current_selections = self.get_selected_items()

        # Store expansion state
        was_expanded = self.is_expanded

        # Clear current checkboxes
        for checkbox in self.checkboxes.values():
            checkbox.deleteLater()
        self.checkboxes.clear()

        # Clear layout
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.deleteLater()

        # Recreate checkboxes with filtered items
        for item in filtered_items:
            checkbox = QCheckBox(f"{item['key']} ({item['count']})")
            checkbox.setObjectName(item["key"])  # Store the key for easy retrieval
            checkbox.stateChanged.connect(self.on_checkbox_changed)

            # Ensure fixed height for checkbox - don't expand vertically
            checkbox.setMinimumHeight(20)
            checkbox.setMaximumHeight(30)
            checkbox.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
            )

            # Restore selection if it was selected before
            if item["key"] in current_selections:
                checkbox.setChecked(True)

            self.checkboxes[item["key"]] = checkbox
            self.content_layout.addWidget(checkbox)

        # Update clear button visibility based on selections
        has_selection = any(cb.isChecked() for cb in self.checkboxes.values())
        self.clear_button.setVisible(has_selection)

        # Restore expansion state
        if was_expanded:
            if self.toggleable_widget:
                self.toggleable_widget.setVisible(True)
            self.is_expanded = True
            self.header_button.setChecked(True)


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
        self.prompt_filter_edit = None
        self.path_filter_tree = None
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        button_layout = QHBoxLayout()

        self.clear_all_button = QPushButton("Clear All")
        # Use theme styling for Clear All button
        self.clear_all_button.clicked.connect(self.clear_all_filters)
        button_layout.addWidget(self.clear_all_button)

        self.refresh_button = QPushButton("Refresh")
        # Use theme styling for Refresh button
        button_layout.addWidget(self.refresh_button)

        # Sort order controls
        sort_separator = QLabel("|")
        # Use theme styling for separator
        button_layout.addWidget(sort_separator)

        # Sort by count button
        self.sort_count_button = QPushButton("321 ▼")
        self.sort_count_button.setToolTip("Sort by count")
        self.sort_count_button.setCheckable(True)
        self.sort_count_button.setChecked(True)  # Default active
        # Use theme styling for sort count button
        self.sort_count_button.clicked.connect(lambda: self.set_sort_order("count"))
        button_layout.addWidget(self.sort_count_button)

        # Sort alphabetically button
        self.sort_alpha_button = QPushButton("ABC ▲")
        self.sort_alpha_button.setToolTip("Sort alphabetically (A-Z)")
        self.sort_alpha_button.setCheckable(True)
        # Use theme styling for sort alpha button
        self.sort_alpha_button.clicked.connect(
            lambda: self.set_sort_order("alphabetical")
        )
        button_layout.addWidget(self.sort_alpha_button)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # Favorites filter checkbox (always visible at top)
        self.favorites_checkbox = QCheckBox("★ Favorites")
        # Use theme styling for Favorites checkbox
        self.favorites_checkbox.setToolTip("Show only favorite items")
        self.favorites_checkbox.stateChanged.connect(self.on_favorites_toggled)
        main_layout.addWidget(self.favorites_checkbox)

        # Path filter tree (above prompt filter)
        self.path_filter_tree = PathFilterTree(self)
        self.path_filter_tree.path_selected.connect(self.on_path_filter_changed)
        self.path_filter_tree.path_cleared.connect(self.on_path_filter_cleared)
        main_layout.addWidget(self.path_filter_tree)

        # Prompt filter text input - fixed at top of filter sections
        prompt_filter_container = QWidget()
        prompt_filter_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        prompt_filter_layout = QVBoxLayout(prompt_filter_container)
        prompt_filter_layout.setContentsMargins(5, 5, 5, 5)
        prompt_filter_layout.setSpacing(2)

        prompt_filter_label = QLabel("Filter Prompts:")
        # Use theme styling for prompt filter label
        prompt_filter_layout.addWidget(prompt_filter_label)

        self.prompt_filter_edit = QLineEdit()
        self.prompt_filter_edit.setPlaceholderText("Type to filter prompt values...")
        self.prompt_filter_edit.textChanged.connect(self.on_prompt_filter_changed)
        self.prompt_filter_edit.setClearButtonEnabled(True)
        prompt_filter_layout.addWidget(self.prompt_filter_edit)

        main_layout.addWidget(prompt_filter_container)

        # Scroll area for filter sections - expands to fill available space
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Set size policy to expand and fill available vertical space
        self.scroll_area.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )

        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(2)

        self.scroll_area.setWidget(self.scroll_widget)
        # Add with stretch factor to expand proportionally
        main_layout.addWidget(self.scroll_area, 1)

    def update_filters(self, filter_data: Dict[str, List[Dict[str, Any]]]):
        """Update the filter sections with new data."""
        # Save current prompt filter text
        current_prompt_filter = (
            self.prompt_filter_edit.text() if self.prompt_filter_edit else ""
        )

        # Save expansion states AND selected items of existing sections
        expansion_states = {}
        selected_states = {}
        for section_name, section in self.filter_sections.items():
            expansion_states[section_name] = section.is_expanded
            selected_states[section_name] = section.get_selected_items()

        # Clear existing sections
        self.clear_sections()

        # Define the order of sections (prompt first, exclude date and path)
        section_order = ["prompt", "model", "source", "ext", "lora"]

        # Create sections in the specified order
        for section_name in section_order:
            if section_name in filter_data and filter_data[section_name]:
                items = filter_data[section_name]
                section = FilterSection(section_name, items, self)
                section.selection_changed.connect(self.on_filter_selection_changed)

                # Insert before the stretch
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, section)
                self.filter_sections[section_name] = section

                # Restore expansion state if section existed before
                # if section_name in expansion_states and expansion_states[section_name]:
                #    section.toggle_section()

                # Restore selected items if section existed before
                if section_name in selected_states:
                    for item_key in selected_states[section_name]:
                        if item_key in section.checkboxes:
                            section.checkboxes[item_key].setChecked(True)
                    # Update clear button visibility if items were restored
                    if selected_states[section_name]:
                        section.clear_button.setVisible(True)

                # Apply prompt filter if it's the prompt section and there's filter text
                if section_name == "prompt" and current_prompt_filter:
                    section.filter_items(current_prompt_filter)

        # Add any remaining sections not in the predefined order (excluding path)
        for section_name, items in filter_data.items():
            if section_name not in section_order and section_name != "path" and items:
                section = FilterSection(section_name, items, self)
                section.selection_changed.connect(self.on_filter_selection_changed)

                # Insert before the stretch
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, section)
                self.filter_sections[section_name] = section

                # Restore expansion state if section existed before
                if section_name in expansion_states and expansion_states[section_name]:
                    section.toggle_section()

                # Restore selected items if section existed before
                if section_name in selected_states:
                    for item_key in selected_states[section_name]:
                        if item_key in section.checkboxes:
                            section.checkboxes[item_key].setChecked(True)
                    # Update clear button visibility if items were restored
                    if selected_states[section_name]:
                        section.clear_button.setVisible(True)

        # If there were any selected filters previously, re-emit the filters changed signal
        # to ensure the UI stays in sync
        if any(selected_states.values()):
            self.on_filter_selection_changed()

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

        # Add path filter if one is selected
        if self.path_filter_tree and self.path_filter_tree.get_selected_path():
            filters["path"] = [self.path_filter_tree.get_selected_path()]

        return filters

    def clear_all_filters(self):
        """Clear all filter selections."""
        for section in self.filter_sections.values():
            section.clear_selection()
        # Also clear the prompt filter text
        if self.prompt_filter_edit:
            self.prompt_filter_edit.clear()
        # Clear path filter
        if self.path_filter_tree:
            self.path_filter_tree.clear_selection()
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
        return str(self.sort_order)

    def set_refresh_callback(self, callback):
        """Set the callback function for the refresh button."""
        self.refresh_button.clicked.connect(callback)

    def on_favorites_toggled(self):
        """Handle favorites checkbox toggle."""
        is_checked = self.favorites_checkbox.isChecked()
        self.favorites_toggled.emit(is_checked)

    def is_favorites_active(self) -> bool:
        """Check if favorites filter is active."""
        return (
            bool(self.favorites_checkbox.isChecked())
            if self.favorites_checkbox
            else False
        )

    def on_prompt_filter_changed(self, text: str):
        """Handle prompt filter text changes."""
        # Only filter the prompt section
        if "prompt" in self.filter_sections:
            prompt_section = self.filter_sections["prompt"]
            prompt_section.filter_items(text)

            # Force refresh of the content widget to ensure proper display
            prompt_section.content_widget.update()

            # Emit filter change if there are selected items
            if prompt_section.get_selected_items():
                self.on_filter_selection_changed()

    def on_path_filter_changed(self, path: str):
        """Handle path filter selection."""
        self.on_filter_selection_changed()

    def on_path_filter_cleared(self):
        """Handle path filter clearing."""
        self.on_filter_selection_changed()

    def update_path_filter_data(self, indexed_paths: Set[str]):
        """Update the path filter tree with indexed paths from database."""
        if self.path_filter_tree:
            from metascan.utils.app_paths import get_config_path

            self.path_filter_tree.load_config(str(get_config_path()))
            self.path_filter_tree.update_indexed_paths(indexed_paths)
