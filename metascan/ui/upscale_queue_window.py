"""
Window for viewing and managing the upscale processing queue.
"""

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QProgressBar,
    QLabel,
    QHeaderView,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from pathlib import Path
from typing import Dict
import subprocess
import sys
from metascan.core.upscale_queue_process import (
    UpscaleTask,
    UpscaleStatus,
    ProcessUpscaleQueue,
)


class UpscaleQueueWindow(QMainWindow):
    """Window for displaying and managing the upscale queue."""

    def __init__(self, queue: ProcessUpscaleQueue, parent=None):
        """
        Initialize the queue window.

        Args:
            queue: The upscale queue to display
            parent: Parent widget
        """
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle("Upscale Queue")
        self.setMinimumSize(800, 400)

        # Track task rows
        self.task_rows: Dict[str, int] = {}

        self._init_ui()
        self._connect_signals()
        self._refresh_queue()

    def _init_ui(self):
        """Initialize the user interface."""
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Status label
        self.status_label = QLabel("Queue Status: Idle")
        layout.addWidget(self.status_label)

        # Queue table
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(4)
        self.queue_table.setHorizontalHeaderLabels(["File", "Status", "Progress", ""])

        # Configure table
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.horizontalHeader().setStretchLastSection(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        # Hide the horizontal header (table title row)
        self.queue_table.horizontalHeader().setVisible(False)

        # Connect double-click signal
        self.queue_table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        layout.addWidget(self.queue_table)

        # Bottom controls
        bottom_layout = QHBoxLayout()

        self.clear_completed_button = QPushButton("Clear Completed")
        self.clear_completed_button.clicked.connect(self._clear_completed)
        bottom_layout.addWidget(self.clear_completed_button)

        bottom_layout.addStretch()

        # Pause/Resume button
        self.pause_button = QPushButton("Pause Queue")
        self.pause_button.clicked.connect(self._toggle_pause)
        bottom_layout.addWidget(self.pause_button)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        bottom_layout.addWidget(self.close_button)

        layout.addLayout(bottom_layout)

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._update_progress)
        self.refresh_timer.start(100)  # Update every 100ms

    def _connect_signals(self):
        """Connect queue signals."""
        self.queue.task_added.connect(self._on_task_added)
        self.queue.task_updated.connect(self._on_task_updated)
        self.queue.task_removed.connect(self._on_task_removed)
        self.queue.queue_changed.connect(self._refresh_queue)

    def _on_cell_double_clicked(self, row: int, column: int):
        """Handle double-click on a table cell."""
        # Get the file path from the first column
        file_item = self.queue_table.item(row, 0)
        if not file_item:
            return

        file_path = file_item.data(Qt.ItemDataRole.UserRole)
        if not file_path:
            return

        # Check if Command/Ctrl key is pressed
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        is_cmd_pressed = modifiers & Qt.KeyboardModifier.ControlModifier or modifiers & Qt.KeyboardModifier.MetaModifier

        try:
            if is_cmd_pressed:
                # Open folder containing the file
                if sys.platform == "darwin":
                    # macOS: reveal in Finder
                    subprocess.call(["open", "-R", file_path])
                elif sys.platform == "win32":
                    # Windows: open folder and select file
                    subprocess.call(["explorer", "/select,", file_path])
                else:
                    # Linux: open parent directory
                    parent_dir = str(Path(file_path).parent)
                    subprocess.call(["xdg-open", parent_dir])
            else:
                # Open the file directly
                if sys.platform == "darwin":
                    subprocess.call(["open", file_path])
                elif sys.platform == "win32":
                    subprocess.call(["start", file_path], shell=True)
                else:
                    subprocess.call(["xdg-open", file_path])
        except Exception as e:
            QMessageBox.warning(
                self,
                "Open Failed",
                f"Failed to open file or folder: {str(e)}"
            )

    def _refresh_queue(self):
        """Refresh the entire queue display."""
        # Clear table
        self.queue_table.setRowCount(0)
        self.task_rows.clear()

        # Add all tasks
        tasks = self.queue.get_all_tasks()
        for task in tasks:
            self._add_task_row(task)

        # Update status
        self._update_status()

    def _add_task_row(self, task: UpscaleTask):
        """Add a row for a task."""
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self.task_rows[task.id] = row

        # File name
        file_path = Path(task.file_path)
        file_item = QTableWidgetItem(file_path.name)
        file_item.setToolTip(str(file_path))
        file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        # Store the full file path in item data for double-click handling
        file_item.setData(Qt.ItemDataRole.UserRole, str(file_path))
        self.queue_table.setItem(row, 0, file_item)

        # Status
        status_item = QTableWidgetItem(self._get_status_text(task.status))
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._set_status_color(status_item, task.status)
        self.queue_table.setItem(row, 1, status_item)

        # Progress
        progress_widget = QProgressBar()
        progress_widget.setRange(0, 100)
        progress_widget.setValue(int(task.progress))
        self.queue_table.setCellWidget(row, 2, progress_widget)

        # Actions - Red X button for removal (hidden for processing files)
        if task.status != UpscaleStatus.PROCESSING:
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            remove_button = QPushButton("✕")
            remove_button.setFixedSize(24, 24)
            remove_button.setStyleSheet(
                """
                QPushButton {
                    color: red;
                    font-size: 16px;
                    font-weight: bold;
                    border: none;
                    background-color: transparent;
                }
                QPushButton:hover {
                    background-color: rgba(255, 0, 0, 0.1);
                    border-radius: 12px;
                }
                """
            )
            remove_button.clicked.connect(lambda: self._remove_task(task.id))
            actions_layout.addWidget(remove_button)

            self.queue_table.setCellWidget(row, 3, actions_widget)
        else:
            # No action button for processing files
            self.queue_table.setCellWidget(row, 3, None)

    @pyqtSlot(UpscaleTask)
    def _on_task_added(self, task: UpscaleTask):
        """Handle task added to queue."""
        self._add_task_row(task)
        self._update_status()

    @pyqtSlot(UpscaleTask)
    def _on_task_updated(self, task: UpscaleTask):
        """Handle task update."""
        if task.id not in self.task_rows:
            return

        row = self.task_rows[task.id]

        # Update status
        status_item = self.queue_table.item(row, 1)
        if status_item:
            status_item.setText(self._get_status_text(task.status))
            self._set_status_color(status_item, task.status)

        # Update progress
        progress_widget = self.queue_table.cellWidget(row, 2)
        if isinstance(progress_widget, QProgressBar):
            progress_widget.setValue(int(task.progress))

        # Update actions
        self._update_task_actions(row, task)
        self._update_status()

    @pyqtSlot(str)
    def _on_task_removed(self, task_id: str):
        """Handle task removed from queue."""
        if task_id in self.task_rows:
            row = self.task_rows[task_id]
            self.queue_table.removeRow(row)
            del self.task_rows[task_id]

            # Update row indices
            for tid, r in list(self.task_rows.items()):
                if r > row:
                    self.task_rows[tid] = r - 1

        self._update_status()

    def _update_task_actions(self, row: int, task: UpscaleTask):
        """Update the actions widget for a task."""
        # Red X button for removal (hidden for processing files)
        if task.status != UpscaleStatus.PROCESSING:
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            remove_button = QPushButton("✕")
            remove_button.setFixedSize(24, 24)
            remove_button.setStyleSheet(
                """
                QPushButton {
                    color: red;
                    font-size: 16px;
                    font-weight: bold;
                    border: none;
                    background-color: transparent;
                }
                QPushButton:hover {
                    background-color: rgba(255, 0, 0, 0.1);
                    border-radius: 12px;
                }
                """
            )
            remove_button.clicked.connect(lambda: self._remove_task(task.id))
            actions_layout.addWidget(remove_button)

            self.queue_table.setCellWidget(row, 3, actions_widget)
        else:
            # No action button for processing files
            self.queue_table.setCellWidget(row, 3, None)

    def _remove_task(self, task_id: str):
        """Remove a task from the queue."""
        self.queue.remove_task(task_id)

    def _clear_completed(self):
        """Clear all completed tasks."""
        completed_count = sum(
            1
            for task in self.queue.get_all_tasks()
            if task.status
            in [UpscaleStatus.COMPLETED, UpscaleStatus.FAILED, UpscaleStatus.CANCELLED]
        )

        if completed_count == 0:
            QMessageBox.information(
                self, "Clear Completed", "No completed tasks to clear."
            )
            return

        reply = QMessageBox.question(
            self,
            "Clear Completed",
            f"Remove {completed_count} completed task(s) from the queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.queue.clear_completed()

    def _toggle_pause(self):
        """Toggle queue pause state."""
        # Check if there are any paused tasks
        tasks = self.queue.get_all_tasks()
        has_paused = any(t.status == UpscaleStatus.PAUSED for t in tasks)

        if has_paused:
            # Resume: change paused tasks back to pending
            count = self.queue.resume_queue()
            if count > 0:
                self.pause_button.setText("Pause Queue")
        else:
            # Pause: change pending tasks to paused
            count = self.queue.pause_queue()
            if count > 0:
                self.pause_button.setText("Resume Queue")

    def _update_progress(self):
        """Update progress displays."""
        # This is called by the timer to update progress bars
        # The actual updates are handled by the task_updated signal
        pass

    def _update_status(self):
        """Update the status label."""
        tasks = self.queue.get_all_tasks()

        total = len(tasks)
        pending = sum(1 for t in tasks if t.status == UpscaleStatus.PENDING)
        processing = sum(1 for t in tasks if t.status == UpscaleStatus.PROCESSING)
        completed = sum(1 for t in tasks if t.status == UpscaleStatus.COMPLETED)
        failed = sum(1 for t in tasks if t.status == UpscaleStatus.FAILED)
        paused = sum(1 for t in tasks if t.status == UpscaleStatus.PAUSED)

        # Update pause button text based on whether queue is paused
        if paused > 0:
            self.pause_button.setText("Resume Queue")
            status = "Paused"
        elif processing > 0:
            self.pause_button.setText("Pause Queue")
            status = "Processing"
        elif pending > 0:
            self.pause_button.setText("Pause Queue")
            status = "Ready"
        else:
            self.pause_button.setText("Pause Queue")
            status = "Idle"

        status_text = f"Queue Status: {status} | Total: {total}"
        if pending > 0:
            status_text += f" | Pending: {pending}"
        if paused > 0:
            status_text += f" | Paused: {paused}"
        if processing > 0:
            status_text += f" | Processing: {processing}"
        if completed > 0:
            status_text += f" | Completed: {completed}"
        if failed > 0:
            status_text += f" | Failed: {failed}"

        self.status_label.setText(status_text)

    def _get_status_text(self, status: UpscaleStatus) -> str:
        """Get display text for a status."""
        return status.value.capitalize()

    def _set_status_color(self, item: QTableWidgetItem, status: UpscaleStatus):
        """Set the color for a status item."""
        if status == UpscaleStatus.PENDING:
            item.setForeground(Qt.GlobalColor.darkGray)
        elif status == UpscaleStatus.PROCESSING:
            item.setForeground(Qt.GlobalColor.blue)
        elif status == UpscaleStatus.COMPLETED:
            item.setForeground(Qt.GlobalColor.darkGreen)
        elif status == UpscaleStatus.FAILED:
            item.setForeground(Qt.GlobalColor.red)
        elif status == UpscaleStatus.CANCELLED:
            item.setForeground(Qt.GlobalColor.darkYellow)
        elif status == UpscaleStatus.PAUSED:
            item.setForeground(Qt.GlobalColor.darkCyan)
