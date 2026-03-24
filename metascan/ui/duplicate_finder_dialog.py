"""
Dialog for finding and managing duplicate/near-duplicate media files.

Uses perceptual hashing (pHash) for fast duplicate detection and optionally
CLIP embeddings for semantic similarity refinement.
"""

import logging
import platform
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

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
    QSplitter,
    QWidget,
    QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QPixmap, QCursor, QDesktopServices

from metascan.core.database_sqlite import DatabaseManager
from metascan.cache.thumbnail import ThumbnailCache
from metascan.utils.app_paths import get_thumbnail_cache_dir

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".webm"}


def _is_video_path(file_path: str) -> bool:
    """Check if a file path refers to a video based on extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def _find_groups_single_partition(
    phashes: Dict[str, str],
    threshold: int,
    progress_callback: Optional[Callable[[int, int], bool]],
    comparisons_offset: int,
    total_comparisons: int,
    report_interval: int,
) -> Tuple[List[List[Tuple[str, int]]], int, bool]:
    """Run duplicate grouping on a single partition of same-type files.

    Returns (groups, comparisons_done, was_cancelled).
    """
    import imagehash

    paths = list(phashes.keys())
    hashes = [imagehash.hex_to_hash(phashes[p]) for p in paths]
    n = len(paths)

    visited: Set[int] = set()
    groups: List[List[Tuple[str, int]]] = []
    comparisons_done = comparisons_offset

    for i in range(n):
        if i in visited:
            continue

        group = [(paths[i], 0)]
        visited.add(i)

        for j in range(i + 1, n):
            if j in visited:
                continue
            dist = hashes[i] - hashes[j]
            comparisons_done += 1
            if dist <= threshold:
                group.append((paths[j], dist))
                visited.add(j)

            if progress_callback and comparisons_done % report_interval == 0:
                if not progress_callback(comparisons_done, total_comparisons):
                    if len(group) > 1:
                        groups.append(group)
                    return groups, comparisons_done, True

        if len(group) > 1:
            groups.append(group)

    return groups, comparisons_done, False


def find_phash_duplicate_groups(
    phashes: Dict[str, str],
    threshold: int = 10,
    progress_callback: Optional[Callable[[int, int], bool]] = None,
) -> List[List[Tuple[str, int]]]:
    """Find groups of files with similar perceptual hashes.

    Images and videos are grouped separately — a group will never contain
    both image and video files.

    Args:
        phashes: Dict mapping file_path -> phash hex string.
        threshold: Maximum hamming distance to consider as duplicate.
        progress_callback: Called with (current, total) comparisons.
            Return False to cancel.

    Returns:
        List of groups, where each group is a list of (file_path, distance_to_first) tuples.
    """
    # Partition by media type
    image_hashes = {p: h for p, h in phashes.items() if not _is_video_path(p)}
    video_hashes = {p: h for p, h in phashes.items() if _is_video_path(p)}

    ni = len(image_hashes)
    nv = len(video_hashes)
    total_comparisons = ni * (ni - 1) // 2 + nv * (nv - 1) // 2
    report_interval = max(total_comparisons // 100, 500) if total_comparisons > 0 else 1

    all_groups: List[List[Tuple[str, int]]] = []

    # Process images
    if image_hashes:
        groups, cmp_done, cancelled = _find_groups_single_partition(
            image_hashes, threshold, progress_callback,
            comparisons_offset=0,
            total_comparisons=total_comparisons,
            report_interval=report_interval,
        )
        all_groups.extend(groups)
        if cancelled:
            return all_groups
    else:
        cmp_done = 0

    # Process videos
    if video_hashes:
        groups, cmp_done, cancelled = _find_groups_single_partition(
            video_hashes, threshold, progress_callback,
            comparisons_offset=cmp_done,
            total_comparisons=total_comparisons,
            report_interval=report_interval,
        )
        all_groups.extend(groups)
        if cancelled:
            return all_groups

    # Final progress update
    if progress_callback:
        progress_callback(total_comparisons, total_comparisons)

    return all_groups


class ClickablePreviewLabel(QLabel):
    """QLabel that emits a signal on double-click for opening the file."""

    double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


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
        self._cancel_requested = False
        self._current_preview_path: Optional[str] = None

        # Thumbnail cache for video previews
        cache_dir = get_thumbnail_cache_dir()
        self._thumbnail_cache = ThumbnailCache(cache_dir)

        self.setWindowTitle("Find Duplicates")
        self.setMinimumSize(1100, 650)
        self.setModal(False)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Controls bar ──
        controls = QHBoxLayout()

        self.find_button = QPushButton("Find Duplicates")
        self.find_button.clicked.connect(self._find_duplicates)
        controls.addWidget(self.find_button)

        self.media_type_combo = QComboBox()
        self.media_type_combo.addItem("Images", "images")
        self.media_type_combo.addItem("Video", "video")
        self.media_type_combo.addItem("Images and Video", "all")
        self.media_type_combo.setCurrentIndex(0)
        controls.addWidget(self.media_type_combo)

        self.cancel_search_button = QPushButton("Cancel")
        self.cancel_search_button.clicked.connect(self._cancel_search)
        self.cancel_search_button.setVisible(False)
        controls.addWidget(self.cancel_search_button)

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

        # Help button
        self.help_button = QPushButton("?")
        self.help_button.setFixedSize(24, 24)
        self.help_button.setToolTip("What is this threshold?")
        self.help_button.clicked.connect(self._toggle_help)
        controls.addWidget(self.help_button)

        controls.addStretch()

        self.status_label = QLabel("")
        controls.addWidget(self.status_label)

        layout.addLayout(controls)

        # ── Help text (hidden by default) ──
        self.help_label = QLabel(
            "<b>pHash Hamming Distance Threshold</b><br>"
            "Controls how similar two images must be to count as duplicates. "
            "The value is the number of differing bits between two perceptual hashes (0–64).<br>"
            "<b>0</b> = exact pixel-identical duplicates only<br>"
            "<b>1–5</b> = minor edits, recompression artifacts, slight crops<br>"
            "<b>6–10</b> = visually very similar (default=10)<br>"
            "<b>11–20</b> = loose matching — may include false positives"
        )
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet(
            "QLabel { padding: 8px; border: 1px solid gray; border-radius: 4px; }"
        )
        self.help_label.setVisible(False)
        layout.addWidget(self.help_label)

        # ── Progress bar (hidden by default) ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # ── Main content: splitter with tree + preview ──
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: results tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["File", "Dimensions", "Size", "Distance"]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 80)
        self.tree.setColumnWidth(3, 70)
        self.tree.currentItemChanged.connect(self._on_item_selected)
        self.splitter.addWidget(self.tree)

        # Right: preview panel
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(4, 4, 4, 4)

        self.preview_image = ClickablePreviewLabel()
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumSize(250, 250)
        self.preview_image.setStyleSheet(
            "QLabel { background-color: black; border: 1px solid gray; }"
        )
        self.preview_image.setText("Select a file to preview")
        self.preview_image.double_clicked.connect(self._open_preview_file)
        preview_layout.addWidget(self.preview_image, 1)

        self.preview_info = QLabel("")
        self.preview_info.setWordWrap(True)
        self.preview_info.setStyleSheet("QLabel { padding: 4px; }")
        preview_layout.addWidget(self.preview_info)

        self.preview_hint = QLabel("Double-click image to open in viewer")
        self.preview_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_hint.setStyleSheet(
            "QLabel { color: gray; font-size: 11px; }"
        )
        self.preview_hint.setVisible(False)
        preview_layout.addWidget(self.preview_hint)

        self.splitter.addWidget(preview_panel)
        self.splitter.setSizes([650, 350])

        layout.addWidget(self.splitter, 1)

        # ── Action buttons ──
        actions = QHBoxLayout()

        self.select_dupes_button = QPushButton("Select All Duplicates (Keep First)")
        self.select_dupes_button.clicked.connect(self._select_all_duplicates)
        self.select_dupes_button.setEnabled(False)
        actions.addWidget(self.select_dupes_button)

        self.select_keep_largest_button = QPushButton("Select Largest in Group")
        self.select_keep_largest_button.clicked.connect(self._select_keep_largest)
        self.select_keep_largest_button.setEnabled(False)
        actions.addWidget(self.select_keep_largest_button)

        actions.addStretch()

        self.delete_button = QPushButton("Delete Selected...")
        self.delete_button.clicked.connect(self._delete_selected)
        self.delete_button.setEnabled(False)
        actions.addWidget(self.delete_button)

        layout.addLayout(actions)

    def _toggle_help(self) -> None:
        self.help_label.setVisible(not self.help_label.isVisible())

    def _on_threshold_changed(self, value: int) -> None:
        self.threshold_label.setText(str(value))

    def _cancel_search(self) -> None:
        self._cancel_requested = True

    def _find_duplicates(self) -> None:
        """Run duplicate detection using pHash."""
        self._cancel_requested = False
        self.find_button.setEnabled(False)
        self.cancel_search_button.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Loading hashes...")
        QApplication.processEvents()

        try:
            phashes = self.db_manager.get_all_phashes()
            if not phashes:
                self.status_label.setText("No hashes found. Run a scan first.")
                return

            # Filter by selected media type
            media_filter = self.media_type_combo.currentData()
            if media_filter == "images":
                phashes = {p: h for p, h in phashes.items() if not _is_video_path(p)}
            elif media_filter == "video":
                phashes = {p: h for p, h in phashes.items() if _is_video_path(p)}
            # "all" keeps everything

            if not phashes:
                self.status_label.setText(f"No {media_filter} files with hashes found.")
                return

            n = len(phashes)
            total_cmp = n * (n - 1) // 2
            self.progress_bar.setMaximum(max(total_cmp, 1))
            self.status_label.setText(f"Comparing {n} files ({total_cmp:,} comparisons)...")
            QApplication.processEvents()

            threshold = self.threshold_slider.value()

            def progress_cb(current: int, total: int) -> bool:
                self.progress_bar.setValue(current)
                QApplication.processEvents()
                return not self._cancel_requested

            self._groups = find_phash_duplicate_groups(
                phashes, threshold, progress_callback=progress_cb
            )

            self._populate_tree()

            if self._cancel_requested:
                total_dupes = sum(len(g) - 1 for g in self._groups)
                self.status_label.setText(
                    f"Cancelled. Partial results: {len(self._groups)} groups, "
                    f"{total_dupes} duplicates"
                )
            else:
                total_dupes = sum(len(g) - 1 for g in self._groups)
                self.status_label.setText(
                    f"Found {len(self._groups)} groups with {total_dupes} duplicates"
                )

            has_results = len(self._groups) > 0
            self.select_dupes_button.setEnabled(has_results)
            self.select_keep_largest_button.setEnabled(has_results)
            self.delete_button.setEnabled(has_results)

        except Exception as e:
            logger.error(f"Duplicate detection failed: {e}")
            self.status_label.setText(f"Error: {e}")

        finally:
            self.find_button.setEnabled(True)
            self.cancel_search_button.setVisible(False)
            self.progress_bar.setVisible(False)

    def _populate_tree(self) -> None:
        """Populate the tree widget with duplicate groups."""
        self.tree.clear()

        for group_idx, group in enumerate(self._groups):
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

    def _on_item_selected(self, current: QTreeWidgetItem, previous: QTreeWidgetItem) -> None:
        """Update the preview panel when a tree item is selected."""
        if current is None:
            return

        file_path = current.data(0, Qt.ItemDataRole.UserRole)
        if not file_path:
            # Group header — clear preview
            self.preview_image.setText("Select a file to preview")
            self.preview_image.setPixmap(QPixmap())
            self.preview_info.setText("")
            self.preview_hint.setVisible(False)
            self._current_preview_path = None
            return

        self._current_preview_path = file_path
        path = Path(file_path)
        ext = path.suffix.lower()

        # Load preview
        pixmap = None
        if ext in VIDEO_EXTENSIONS:
            # Use thumbnail cache for videos
            thumb_path = self._thumbnail_cache.get_or_create_thumbnail(path)
            if thumb_path and thumb_path.exists():
                pixmap = QPixmap(str(thumb_path))
        else:
            pixmap = QPixmap(file_path)

        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                self.preview_image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_image.setPixmap(scaled)
        else:
            self.preview_image.setText("Preview not available")

        # File info
        try:
            stat = path.stat()
            size_str = self._format_size(stat.st_size)
        except OSError:
            size_str = "N/A"

        media = self.db_manager.get_media(path)
        dims = f"{media.width}x{media.height}" if media else "N/A"
        media_type = "Video" if ext in VIDEO_EXTENSIONS else "Image"

        self.preview_info.setText(
            f"<b>{path.name}</b><br>"
            f"{media_type} — {dims} — {size_str}<br>"
            f"<span style='color: gray; font-size: 11px;'>{file_path}</span>"
        )
        self.preview_hint.setVisible(True)

    def _open_preview_file(self) -> None:
        """Open the currently previewed file in the platform's default viewer."""
        if not self._current_preview_path:
            return

        path = Path(self._current_preview_path)
        if not path.exists():
            QMessageBox.warning(self, "File Not Found", f"File not found:\n{path}")
            return

        try:
            url = QUrl.fromLocalFile(str(path))
            if QDesktopServices.openUrl(url):
                return

            system = platform.system()
            if system == "Darwin":
                subprocess.run(["open", str(path)], check=True)
            elif system == "Windows":
                subprocess.run(["start", str(path)], shell=True, check=True)
            elif system == "Linux":
                subprocess.run(["xdg-open", str(path)], check=True)
        except Exception as e:
            logger.error(f"Failed to open file: {e}")
            QMessageBox.warning(self, "Error", f"Could not open file:\n{e}")

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

    def _select_keep_largest(self) -> None:
        """Select all files except the largest in each group (keep largest unselected)."""
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group is None:
                continue

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

            largest_idx = max(sizes, key=lambda x: x[1])[0]

            for j in range(group.childCount()):
                child = group.child(j)
                if child is None:
                    continue
                if j == largest_idx:
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    child.setCheckState(0, Qt.CheckState.Checked)

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
            self._find_duplicates()

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024  # type: ignore[assignment]
        return f"{size_bytes:.1f} TB"
