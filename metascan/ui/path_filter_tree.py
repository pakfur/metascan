from PyQt6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QHBoxLayout,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from typing import Dict, List, Set, Optional, Tuple
from pathlib import Path
import json

from metascan.utils.path_utils import to_native_path


class PathFilterTree(QFrame):
    """Tree widget for filtering by file paths based on configured directories."""

    path_selected = pyqtSignal(str)  # Emits the selected path prefix
    path_cleared = pyqtSignal()  # Emits when selection is cleared

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_dirs: List[Dict[str, any]] = []
        self.indexed_paths: Set[str] = set()
        self.selected_path: Optional[str] = None
        self.is_expanded = False  # Default to collapsed

        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header button (consistent with other filter sections)
        self.header_button = QPushButton()
        self.header_button.setCheckable(True)
        self.header_button.setText("▶ Paths")  # Start with collapsed icon
        self.header_button.setChecked(False)  # Start unchecked (collapsed)
        self.header_button.clicked.connect(self.toggle_section)
        layout.addWidget(self.header_button)

        # Content area
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(10, 5, 10, 5)
        content_layout.setSpacing(2)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setMaximumHeight(150)  # Reduced height by half
        self.tree.itemClicked.connect(self.on_item_clicked)

        # Get system folder icons
        icon_provider = QFileIconProvider()
        self.folder_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)

        # Use minimal styling to keep default arrows but remove outline
        self.tree.setStyleSheet(
            """
            QTreeWidget {
                outline: 0;
            }
        """
        )

        # Keep root decoration enabled for default expand/collapse behavior
        self.tree.setRootIsDecorated(True)

        content_layout.addWidget(self.tree)

        # Add stretch to push content to top and minimize empty space
        content_layout.addStretch()

        layout.addWidget(self.content_widget)
        self.content_widget.setVisible(False)  # Start collapsed

        # Set maximum height for the entire widget to match tree + margins
        self.setMaximumHeight(200)  # Tree height + header + margins

    def toggle_section(self):
        """Toggle the expanded/collapsed state of the section."""
        self.is_expanded = not self.is_expanded
        self.content_widget.setVisible(self.is_expanded)

        # Update header button text and icon
        icon = "▼" if self.is_expanded else "▶"
        self.header_button.setText(f"{icon} Paths")
        self.header_button.setChecked(self.is_expanded)

    def load_config(self, config_path: str = "config.json"):
        """Load directory configuration from config file."""
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                self.config_dirs = config.get("directories", [])

            # Convert paths from POSIX storage format to native format
            for dir_config in self.config_dirs:
                if "filepath" in dir_config:
                    dir_config["filepath"] = to_native_path(dir_config["filepath"])
        except Exception as e:
            print(f"Error loading config: {e}")
            self.config_dirs = []

    def update_indexed_paths(self, paths: Set[str]):
        """Update the set of indexed paths from the database."""
        self.indexed_paths = paths
        self.rebuild_tree()

    def rebuild_tree(self):
        """Rebuild the tree based on config and indexed paths."""
        self.tree.clear()

        if not self.config_dirs or not self.indexed_paths:
            return

        for dir_config in self.config_dirs:
            dir_path = dir_config.get("filepath", "")
            search_subfolders = dir_config.get("search_subfolders", True)

            if not dir_path:
                continue

            # Normalize the path
            dir_path = str(Path(dir_path).resolve())

            if search_subfolders:
                # Build a tree for paths under this directory
                self._build_subtree(dir_path)
            else:
                # Add as a single non-expandable item if it has indexed files
                if self._has_indexed_files(dir_path):
                    item = QTreeWidgetItem(self.tree)
                    item.setText(0, dir_path)
                    item.setData(0, Qt.ItemDataRole.UserRole, dir_path)
                    item.setIcon(0, self.folder_icon)  # Set folder icon
                    # Make it non-expandable
                    item.setChildIndicatorPolicy(
                        QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
                    )

    def _build_subtree(self, root_path: str):
        """Build a subtree for a directory with search_subfolders=true."""
        # Find all indexed paths that start with this root
        matching_paths = [
            p for p in self.indexed_paths if p.lower().startswith(root_path.lower())
        ]

        if not matching_paths:
            return

        # Build path tree structure
        path_tree: Dict[str, Dict] = {}
        for path in matching_paths:
            # Get relative path from root
            try:
                rel_path = Path(path).relative_to(root_path)
                parts = rel_path.parts[:-1]  # Exclude the filename

                # Build nested dict structure
                current = path_tree
                for part in parts:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
            except ValueError:
                # Path is not relative to root (shouldn't happen)
                continue

        # Create tree items from the structure
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, Path(root_path).name or root_path)
        root_item.setData(0, Qt.ItemDataRole.UserRole, root_path)
        root_item.setIcon(0, self.folder_icon)  # Set folder icon

        if path_tree:
            self._add_tree_items(root_item, path_tree, root_path)
            # Expand the root item by default
            root_item.setExpanded(True)

    def _add_tree_items(
        self, parent_item: QTreeWidgetItem, tree_dict: Dict, current_path: str
    ):
        """Recursively add tree items from the path tree structure."""
        for name, subtree in sorted(tree_dict.items()):
            child_path = str(Path(current_path) / name)
            child_item = QTreeWidgetItem(parent_item)
            child_item.setText(0, name)
            child_item.setData(0, Qt.ItemDataRole.UserRole, child_path)
            child_item.setIcon(0, self.folder_icon)  # Set folder icon

            if subtree:
                self._add_tree_items(child_item, subtree, child_path)

    def _has_indexed_files(self, dir_path: str):
        """Check if a directory has any indexed files directly in it."""
        dir_path_lower = dir_path.lower()
        for path in self.indexed_paths:
            # Check if file is directly in this directory (not in subdirs)
            if path.lower().startswith(dir_path_lower):
                parent = str(Path(path).parent)
                if parent.lower() == dir_path_lower:
                    return True
        return False

    def on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle tree item selection with toggle behavior."""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            # Check if this item is already selected
            if self.selected_path == path:
                # Toggle off - clear selection
                self.tree.clearSelection()
                self.selected_path = None
                self.path_cleared.emit()
            else:
                # Select the new item
                self.tree.clearSelection()
                item.setSelected(True)
                self.selected_path = path
                self.path_selected.emit(path)

    def clear_selection(self):
        """Clear the current path selection."""
        self.tree.clearSelection()
        self.selected_path = None
        self.path_cleared.emit()

    def get_selected_path(self) -> Optional[str]:
        """Get the currently selected path prefix."""
        return self.selected_path
