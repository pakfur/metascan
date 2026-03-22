"""
Dialog for finding and managing duplicate/near-duplicate media files.

Uses perceptual hashing (pHash) for fast duplicate detection and optionally
CLIP embeddings for semantic similarity refinement.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QLabel,
    QSlider,
    QComboBox,
    QProgressBar,
    QMessageBox,
    QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QIcon

from metascan.core.database_sqlite import DatabaseManager

logger = logging.getLogger(__name__)


def find_phash_duplicate_groups(
    phashes: Dict[str, str], threshold: int = 10
) -> List[List[Tuple[str, int]]]:
    """Find groups of files with similar perceptual hashes.

    Args:
        phashes: Dict mapping file_path -> phash hex string.
        threshold: Maximum hamming distance to consider as duplicate.

    Returns:
        List of groups, where each group is a list of (file_path, distance_to_first) tuples.
    """
    import imagehash

    paths = list(phashes.keys())
    hashes = [imagehash.hex_to_hash(phashes[p]) for p in paths]
    n = len(paths)

    visited: Set[int] = set()
    groups: List[List[Tuple[str, int]]] = []

    for i in range(n):
        if i in visited:
            continue

        group = [(paths[i], 0)]
        visited.add(i)

        for j in range(i + 1, n):
            if j in visited:
                continue
            dist = hashes[i] - hashes[j]
            if dist <= threshold:
                group.append((paths[j], dist))
                visited.add(j)

        if len(group) > 1:
            groups.append(group)

    return groups


class DuplicateFinderDialog(QDialog):
    """Dialog for finding and managing duplicate media files."""

    delete_requested = pyqtSignal(list)  # List of file paths to delete

    def __init__(
        self,
        db_manager: DatabaseManager,
        parent=None,
    ):
        super().__init__(parent)
        self.db_manager = db_manager
        self._groups: List[List[Tuple[str, int]]] = []

        self.setWindowTitle("Find Duplicates")
        self.setMinimumSize(900, 600)
        self.setModal(False)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Controls bar
        controls = QHBoxLayout()

        self.find_button = QPushButton("Find Duplicates")
        self.find_button.clicked.connect(self._find_duplicates)
        controls.addWidget(self.find_button)

        controls.addWidget(QLabel("Threshold:"))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setMinimum(0)
        self.threshold_slider.setMaximum(20)
        self.threshold_slider.setValue(10)
        self.threshold_slider.setFixedWidth(150)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        controls.addWidget(self.threshold_slider)

        self.threshold_label = QLabel("10")
        self.threshold_label.setFixedWidth(30)
        controls.addWidget(self.threshold_label)

        controls.addStretch()

        self.status_label = QLabel("")
        controls.addWidget(self.status_label)

        layout.addLayout(controls)

        # Results tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["File", "Dimensions", "Size", "Hamming Distance"]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 100)
        self.tree.setColumnWidth(3, 130)

        layout.addWidget(self.tree)

        # Action buttons
        actions = QHBoxLayout()

        self.select_dupes_button = QPushButton("Select All Duplicates (Keep First)")
        self.select_dupes_button.clicked.connect(self._select_all_duplicates)
        self.select_dupes_button.setEnabled(False)
        actions.addWidget(self.select_dupes_button)

        self.select_smallest_button = QPushButton("Select Smallest in Group")
        self.select_smallest_button.clicked.connect(self._select_smallest)
        self.select_smallest_button.setEnabled(False)
        actions.addWidget(self.select_smallest_button)

        actions.addStretch()

        self.delete_button = QPushButton("Delete Selected...")
        self.delete_button.clicked.connect(self._delete_selected)
        self.delete_button.setEnabled(False)
        actions.addWidget(self.delete_button)

        layout.addLayout(actions)

    def _on_threshold_changed(self, value: int) -> None:
        self.threshold_label.setText(str(value))

    def _find_duplicates(self) -> None:
        """Run duplicate detection using pHash."""
        self.find_button.setEnabled(False)
        self.status_label.setText("Loading hashes...")

        try:
            phashes = self.db_manager.get_all_phashes()
            if not phashes:
                self.status_label.setText("No hashes found. Run a scan first.")
                self.find_button.setEnabled(True)
                return

            self.status_label.setText(f"Comparing {len(phashes)} files...")

            threshold = self.threshold_slider.value()
            self._groups = find_phash_duplicate_groups(phashes, threshold)

            self._populate_tree()

            total_dupes = sum(len(g) - 1 for g in self._groups)
            self.status_label.setText(
                f"Found {len(self._groups)} groups with {total_dupes} duplicates"
            )

            has_results = len(self._groups) > 0
            self.select_dupes_button.setEnabled(has_results)
            self.select_smallest_button.setEnabled(has_results)
            self.delete_button.setEnabled(has_results)

        except Exception as e:
            logger.error(f"Duplicate detection failed: {e}")
            self.status_label.setText(f"Error: {e}")

        finally:
            self.find_button.setEnabled(True)

    def _populate_tree(self) -> None:
        """Populate the tree widget with duplicate groups."""
        self.tree.clear()

        for group_idx, group in enumerate(self._groups):
            # Group header
            group_item = QTreeWidgetItem(
                [f"Group {group_idx + 1} ({len(group)} files)", "", "", ""]
            )
            group_item.setFlags(
                group_item.flags() | Qt.ItemFlag.ItemIsAutoTristate
            )
            self.tree.addTopLevelItem(group_item)

            for file_path, distance in group:
                path = Path(file_path)
                try:
                    stat = path.stat()
                    size_str = self._format_size(stat.st_size)
                except OSError:
                    size_str = "N/A"

                # Try to get dimensions from DB
                media = self.db_manager.get_media(path)
                if media:
                    dims = f"{media.width}x{media.height}"
                else:
                    dims = "N/A"

                child = QTreeWidgetItem(
                    [str(path.name), dims, size_str, str(distance)]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, file_path)
                child.setToolTip(0, file_path)
                child.setFlags(
                    child.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                child.setCheckState(0, Qt.CheckState.Unchecked)
                group_item.addChild(child)

            group_item.setExpanded(True)

    def _select_all_duplicates(self) -> None:
        """Select all duplicates, keeping the first item in each group."""
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group is None:
                continue
            for j in range(group.childCount()):
                child = group.child(j)
                if child is None:
                    continue
                if j == 0:
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    child.setCheckState(0, Qt.CheckState.Checked)

    def _select_smallest(self) -> None:
        """Select the smallest file in each group."""
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group is None:
                continue

            # Find sizes for each child
            sizes = []
            for j in range(group.childCount()):
                child = group.child(j)
                if child is None:
                    continue
                fp = child.data(0, Qt.ItemDataRole.UserRole)
                try:
                    size = Path(fp).stat().st_size
                except OSError:
                    size = 0
                sizes.append((j, size))

            if not sizes:
                continue

            # Find smallest
            smallest_idx = min(sizes, key=lambda x: x[1])[0]

            for j in range(group.childCount()):
                child = group.child(j)
                if child is None:
                    continue
                if j == smallest_idx:
                    child.setCheckState(0, Qt.CheckState.Checked)
                else:
                    child.setCheckState(0, Qt.CheckState.Unchecked)

    def _get_checked_paths(self) -> List[str]:
        """Get all checked file paths."""
        paths = []
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group is None:
                continue
            for j in range(group.childCount()):
                child = group.child(j)
                if child is None:
                    continue
                if child.checkState(0) == Qt.CheckState.Checked:
                    fp = child.data(0, Qt.ItemDataRole.UserRole)
                    if fp:
                        paths.append(fp)
        return paths

    def _delete_selected(self) -> None:
        """Delete checked files after confirmation."""
        paths = self._get_checked_paths()
        if not paths:
            QMessageBox.information(self, "No Selection", "No files are selected.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete {len(paths)} selected file(s)?\n\nThis will move them to trash.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(paths)
            self.status_label.setText(f"Deleted {len(paths)} files")
            # Re-run detection after deletion
            self._find_duplicates()

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024  # type: ignore[assignment]
        return f"{size_bytes:.1f} TB"
