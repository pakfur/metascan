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
    QToolBar,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction
from pathlib import Path
from typing import Dict
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

        # Toolbar
        self._create_toolbar()

        # Status label
        self.status_label = QLabel("Queue Status: Idle")
        layout.addWidget(self.status_label)

        # Queue table
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(7)
        self.queue_table.setHorizontalHeaderLabels(
            ["File", "Type", "Scale", "Status", "Progress", "Output", "Actions"]
        )

        # Configure table
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.horizontalHeader().setStretchLastSection(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )

        layout.addWidget(self.queue_table)

        # Bottom controls
        bottom_layout = QHBoxLayout()

        self.clear_completed_button = QPushButton("Clear Completed")
        self.clear_completed_button.clicked.connect(self._clear_completed)
        bottom_layout.addWidget(self.clear_completed_button)

        bottom_layout.addStretch()

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        bottom_layout.addWidget(self.close_button)

        layout.addLayout(bottom_layout)

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._update_progress)
        self.refresh_timer.start(100)  # Update every 100ms

    def _create_toolbar(self):
        """Create the toolbar."""
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Pause/Resume action
        self.pause_action = QAction("Pause Queue", self)
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self._toggle_pause)
        toolbar.addAction(self.pause_action)

        toolbar.addSeparator()

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._refresh_queue)
        toolbar.addAction(refresh_action)

    def _connect_signals(self):
        """Connect queue signals."""
        self.queue.task_added.connect(self._on_task_added)
        self.queue.task_updated.connect(self._on_task_updated)
        self.queue.task_removed.connect(self._on_task_removed)
        self.queue.queue_changed.connect(self._refresh_queue)

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
        self.queue_table.setItem(row, 0, file_item)

        # Type
        type_item = QTableWidgetItem(task.file_type.capitalize())
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.queue_table.setItem(row, 1, type_item)

        # Scale
        scale_item = QTableWidgetItem(f"{task.scale}x")
        scale_item.setFlags(scale_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.queue_table.setItem(row, 2, scale_item)

        # Status
        status_item = QTableWidgetItem(self._get_status_text(task.status))
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._set_status_color(status_item, task.status)
        self.queue_table.setItem(row, 3, status_item)

        # Progress
        progress_widget = QProgressBar()
        progress_widget.setRange(0, 100)
        progress_widget.setValue(int(task.progress))
        self.queue_table.setCellWidget(row, 4, progress_widget)

        # Output
        output_text = ""
        if task.output_path:
            output_path = Path(task.output_path)
            output_text = output_path.name
        output_item = QTableWidgetItem(output_text)
        output_item.setToolTip(task.output_path or "")
        output_item.setFlags(output_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.queue_table.setItem(row, 5, output_item)

        # Actions
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)

        if task.status in [UpscaleStatus.PENDING, UpscaleStatus.PROCESSING]:
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(lambda: self._cancel_task(task.id))
            actions_layout.addWidget(cancel_button)
        elif task.status in [
            UpscaleStatus.COMPLETED,
            UpscaleStatus.FAILED,
            UpscaleStatus.CANCELLED,
        ]:
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(lambda: self._remove_task(task.id))
            actions_layout.addWidget(remove_button)

        self.queue_table.setCellWidget(row, 6, actions_widget)

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
        status_item = self.queue_table.item(row, 3)
        if status_item:
            status_item.setText(self._get_status_text(task.status))
            self._set_status_color(status_item, task.status)

        # Update progress
        progress_widget = self.queue_table.cellWidget(row, 4)
        if isinstance(progress_widget, QProgressBar):
            progress_widget.setValue(int(task.progress))

        # Update output
        if task.output_path:
            output_item = self.queue_table.item(row, 5)
            if output_item:
                output_path = Path(task.output_path)
                output_item.setText(output_path.name)
                output_item.setToolTip(task.output_path)

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
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)

        if task.status in [UpscaleStatus.PENDING, UpscaleStatus.PROCESSING]:
            cancel_button = QPushButton("Cancel")
            cancel_button.clicked.connect(lambda: self._cancel_task(task.id))
            actions_layout.addWidget(cancel_button)
        elif task.status in [
            UpscaleStatus.COMPLETED,
            UpscaleStatus.FAILED,
            UpscaleStatus.CANCELLED,
        ]:
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(lambda: self._remove_task(task.id))
            actions_layout.addWidget(remove_button)

        self.queue_table.setCellWidget(row, 6, actions_widget)

    def _cancel_task(self, task_id: str):
        """Cancel a task."""
        reply = QMessageBox.question(
            self,
            "Cancel Task",
            "Are you sure you want to cancel this task?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.queue.cancel_task(task_id)

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
        # This will be implemented when we add the worker thread
        pass

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

        if processing > 0:
            status = "Processing"
        elif pending > 0:
            status = "Ready"
        else:
            status = "Idle"

        status_text = f"Queue Status: {status} | Total: {total}"
        if pending > 0:
            status_text += f" | Pending: {pending}"
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
