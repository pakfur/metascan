"""
Slideshow viewer for displaying media files in automatic slideshow mode.
Separate implementation from MediaViewer to avoid conditional logic.
"""

import random
from typing import List, Optional
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QRadioButton,
    QComboBox,
    QButtonGroup,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint, QCoreApplication
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QCursor, QCloseEvent

from metascan.core.media import Media
from metascan.core.database_sqlite import DatabaseManager
from metascan.ui.media_viewer import ImageViewer, VideoPlayer


class SlideshowViewer(QWidget):
    """Full-screen slideshow viewer with auto-advance and shuffle support."""

    # Signals
    closed = pyqtSignal()
    media_changed = pyqtSignal(Media)
    favorite_toggled = pyqtSignal(Media, bool)  # Emits Media object, not Path
    delete_requested = pyqtSignal(Media)

    def __init__(self, db_manager: DatabaseManager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager

        # Media list and navigation state
        self.media_list: List[Media] = []
        self.current_index = 0
        self.shuffled_indices: List[int] = []
        self.shuffle_position = 0

        # Slideshow state
        self.is_running = False
        self.is_paused = False
        self.view_mode = "ordered"  # "ordered" or "random"
        self.slide_duration = 5000  # milliseconds (default 5s)

        # Timers
        self.advance_timer = QTimer(self)
        self.advance_timer.timeout.connect(self._advance_to_next)
        self.ui_hide_timer = QTimer(self)
        self.ui_hide_timer.timeout.connect(self._hide_ui)
        self.ui_hide_timer.setSingleShot(True)

        # UI components (initialized in _init_ui)
        self.image_viewer: ImageViewer
        self.video_player: Optional[VideoPlayer] = None  # Created on demand
        self.video_player_index: int = -1  # Track video player index in stacked widget
        self.stacked_widget: QStackedWidget
        self.setup_panel: QFrame
        self.start_button: QPushButton
        self.ordered_radio: QRadioButton
        self.random_radio: QRadioButton
        self.mode_button_group: QButtonGroup
        self.timing_combo: QComboBox

        self._init_ui()
        self._setup_window()

    def _setup_window(self):
        """Configure window properties."""
        self.setWindowTitle("Slideshow")
        self.setStyleSheet("background-color: black;")
        self.setMouseTracking(True)

    def _init_ui(self):
        """Initialize the UI components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create stacked widget for image/video switching
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background-color: black;")

        # Create image viewer
        self.image_viewer = ImageViewer()
        self.stacked_widget.addWidget(self.image_viewer)

        # Video player will be created on demand to avoid state issues

        # Add stacked widget to main layout
        main_layout.addWidget(self.stacked_widget)

        # Create setup panel at the bottom (not overlaid)
        self.setup_panel = self._create_setup_panel()
        main_layout.addWidget(self.setup_panel)

        self.setLayout(main_layout)

    def _create_setup_panel(self) -> QFrame:
        """Create the setup panel at the bottom with Start button and controls."""
        panel = QFrame()
        panel.setStyleSheet(
            """
            QFrame {
                background-color: rgba(0, 0, 0, 180);
            }
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 12px 30px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QRadioButton {
                color: white;
                font-size: 14px;
                spacing: 5px;
            }
            QComboBox {
                background-color: #424242;
                color: white;
                border: 1px solid #666;
                border-radius: 3px;
                padding: 5px 10px;
                font-size: 14px;
                min-width: 100px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid white;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #424242;
                color: white;
                selection-background-color: #2196F3;
            }
            """
        )

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(20)

        # Start button
        self.start_button = QPushButton("Start Slideshow")
        self.start_button.clicked.connect(self._start_slideshow)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setFixedHeight(40)
        layout.addWidget(self.start_button)

        # Spacer
        layout.addStretch()

        # View mode selector
        mode_label = QLabel("View Mode:")
        self.ordered_radio = QRadioButton("Ordered")
        self.random_radio = QRadioButton("Random")
        self.ordered_radio.setChecked(True)

        self.mode_button_group = QButtonGroup()
        self.mode_button_group.addButton(self.ordered_radio)
        self.mode_button_group.addButton(self.random_radio)

        layout.addWidget(mode_label)
        layout.addWidget(self.ordered_radio)
        layout.addWidget(self.random_radio)

        # Spacer
        layout.addStretch()

        # Timing selector
        timing_label = QLabel("Image Duration:")
        self.timing_combo = QComboBox()
        self.timing_combo.addItems(
            ["3 seconds", "5 seconds", "10 seconds", "15 seconds", "30 seconds"]
        )
        self.timing_combo.setCurrentIndex(1)  # Default to 5 seconds

        layout.addWidget(timing_label)
        layout.addWidget(self.timing_combo)

        return panel

    def set_media_list(
        self, media_list: List[Media], current_media: Optional[Media] = None
    ):
        """Set the media list and optionally select a starting media item."""
        self.media_list = media_list

        if not media_list:
            return

        # Find starting index
        if current_media:
            try:
                self.current_index = next(
                    i
                    for i, m in enumerate(media_list)
                    if m.file_path == current_media.file_path
                )
            except StopIteration:
                self.current_index = 0
        else:
            self.current_index = 0

        # Display initial media
        self._display_current_media()

    def _start_slideshow(self):
        """Start the slideshow."""
        if not self.media_list:
            return

        self.is_running = True
        self.is_paused = False

        # Get selected mode
        if self.random_radio.isChecked():
            self.view_mode = "random"
            self._create_shuffle()
        else:
            self.view_mode = "ordered"

        # Get timing
        timing_text = self.timing_combo.currentText()
        seconds = int(timing_text.split()[0])
        self.slide_duration = seconds * 1000

        # Hide setup panel
        self.setup_panel.hide()

        # Set focus to main window to ensure key events are received
        self.setFocus()

        # Hide UI and cursor initially
        self._hide_ui()

        # Restart displaying media (to start video playback if on video)
        self._display_current_media()

    def _create_shuffle(self):
        """Create a shuffled list of indices for random mode."""
        self.shuffled_indices = list(range(len(self.media_list)))
        random.shuffle(self.shuffled_indices)

        # Find current position in shuffle
        try:
            self.shuffle_position = self.shuffled_indices.index(self.current_index)
        except ValueError:
            self.shuffle_position = 0
            self.current_index = self.shuffled_indices[0]

    def _cleanup_video_player(self):
        """Clean up and remove the current video player."""
        if self.video_player is not None:
            try:
                # Stop playback
                self.video_player.stop()
                # Process events to allow Qt cleanup
                QCoreApplication.processEvents()
                # Remove from stacked widget
                if self.video_player_index >= 0:
                    widget = self.stacked_widget.widget(self.video_player_index)
                    if widget is not None:
                        self.stacked_widget.removeWidget(widget)
                # Delete the widget
                self.video_player.deleteLater()
                self.video_player = None
                self.video_player_index = -1
                # Process events again after deletion
                QCoreApplication.processEvents()
            except Exception as e:
                print(f"Error cleaning up video player: {e}")

    def _create_video_player(self):
        """Create a fresh video player instance."""
        try:
            # Clean up any existing video player first
            self._cleanup_video_player()

            # Create new video player
            self.video_player = VideoPlayer(db_manager=self.db_manager)
            self.video_player_index = self.stacked_widget.addWidget(self.video_player)
        except Exception as e:
            print(f"Error creating video player: {e}")

    def _display_current_media(self):
        """Display the current media item."""
        if not self.media_list or self.current_index >= len(self.media_list):
            return

        current_media = self.media_list[self.current_index]

        # Stop any existing timer
        self.advance_timer.stop()

        # Determine if video or image
        is_video = current_media.file_path.suffix.lower() in [
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".webm",
            ".m4v",
        ]

        if is_video:
            # Only create a new video player if we don't have one
            # This avoids the flash when navigating between videos
            if self.video_player is None:
                self._create_video_player()
                if self.video_player is None:
                    return
            else:
                # Stop the current video before loading new one
                try:
                    self.video_player.stop()
                except Exception:
                    pass

            # Show video player
            self.stacked_widget.setCurrentWidget(self.video_player)

            try:
                success = self.video_player.load_video(
                    current_media.file_path, current_media
                )
                if success and self.is_running:
                    self.video_player.play()
            except Exception as e:
                print(f"Error loading/playing video: {e}")
            # No auto-advance for videos
        else:
            # If there's a video player, clean it up when switching to image
            if self.video_player is not None:
                self._cleanup_video_player()

            # Show image viewer
            self.stacked_widget.setCurrentWidget(self.image_viewer)
            try:
                self.image_viewer.load_image(str(current_media.file_path))
            except Exception as e:
                print(f"Error loading image: {e}")

            # Start auto-advance timer if slideshow is running and not paused
            if self.is_running and not self.is_paused:
                self.advance_timer.start(self.slide_duration)

        # Emit signal
        self.media_changed.emit(current_media)

    def _advance_to_next(self):
        """Advance to the next media item."""
        self.navigate_next()

    def navigate_next(self):
        """Navigate to the next media item."""
        if not self.media_list:
            return

        if self.view_mode == "random":
            self.shuffle_position += 1
            if self.shuffle_position >= len(self.shuffled_indices):
                # Re-shuffle and start over
                self._create_shuffle()
                self.shuffle_position = 0
            self.current_index = self.shuffled_indices[self.shuffle_position]
        else:
            # Ordered mode
            self.current_index = (self.current_index + 1) % len(self.media_list)

        self._display_current_media()

    def navigate_previous(self):
        """Navigate to the previous media item."""
        if not self.media_list:
            return

        if self.view_mode == "random":
            self.shuffle_position -= 1
            if self.shuffle_position < 0:
                self.shuffle_position = len(self.shuffled_indices) - 1
            self.current_index = self.shuffled_indices[self.shuffle_position]
        else:
            # Ordered mode
            self.current_index = (self.current_index - 1) % len(self.media_list)

        self._display_current_media()

    def toggle_pause(self):
        """Toggle pause state of slideshow."""
        if not self.is_running:
            return

        self.is_paused = not self.is_paused

        if self.is_paused:
            self.advance_timer.stop()
        else:
            # Resume timer if on an image
            current_media = self.media_list[self.current_index]
            is_video = current_media.file_path.suffix.lower() in [
                ".mp4",
                ".avi",
                ".mov",
                ".mkv",
                ".webm",
                ".m4v",
            ]
            if not is_video:
                self.advance_timer.start(self.slide_duration)

    def _show_ui(self):
        """Show UI elements."""
        if not self.is_running:
            return

        # Show video controls if on video
        if (
            self.video_player is not None
            and self.stacked_widget.currentWidget() == self.video_player
        ):
            self.video_player.control_widget.show()

        # Show cursor
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # Start hide timer
        self.ui_hide_timer.start(3000)  # 3 seconds

    def _hide_ui(self):
        """Hide UI elements."""
        if not self.is_running:
            return

        # Hide video controls
        if (
            self.video_player is not None
            and self.stacked_widget.currentWidget() == self.video_player
        ):
            self.video_player.control_widget.hide()

        # Hide cursor
        self.setCursor(Qt.CursorShape.BlankCursor)

    def showEvent(self, event) -> None:
        """Handle window show event."""
        super().showEvent(event)
        # Set focus to main window to receive key events
        self.setFocus()
        # Ensure setup panel is visible when window opens (if slideshow not running)
        if not self.is_running:
            self.setup_panel.show()
            # Show cursor when browsing (setup panel visible)
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event: Optional[QMouseEvent]) -> None:
        """Handle mouse movement to show/hide UI."""
        if self.is_running:
            self._show_ui()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        """Handle keyboard shortcuts."""
        if event is None:
            return
        key = event.key()

        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Space:
            if self.is_running:
                # Toggle pause
                self.toggle_pause()
                # Also toggle video playback if on video
                if (
                    self.video_player is not None
                    and self.stacked_widget.currentWidget() == self.video_player
                ):
                    self.video_player.toggle_playback()
        elif key == Qt.Key.Key_Left:
            self.navigate_previous()
        elif key == Qt.Key.Key_Right:
            self.navigate_next()
        elif key == Qt.Key.Key_F:
            self._toggle_favorite()
        elif key == Qt.Key.Key_M:
            if (
                self.video_player is not None
                and self.stacked_widget.currentWidget() == self.video_player
            ):
                self.video_player.toggle_mute()
        elif key == Qt.Key.Key_Up:
            if (
                self.video_player is not None
                and self.stacked_widget.currentWidget() == self.video_player
            ):
                current_volume = self.video_player.volume_slider.value()
                self.video_player.volume_slider.setValue(min(100, current_volume + 5))
        elif key == Qt.Key.Key_Down:
            if (
                self.video_player is not None
                and self.stacked_widget.currentWidget() == self.video_player
            ):
                current_volume = self.video_player.volume_slider.value()
                self.video_player.volume_slider.setValue(max(0, current_volume - 5))
        else:
            super().keyPressEvent(event)

    def _toggle_favorite(self):
        """Toggle favorite status of current media."""
        if not self.media_list or self.current_index >= len(self.media_list):
            return

        current_media = self.media_list[self.current_index]
        new_status = not current_media.is_favorite
        current_media.is_favorite = new_status
        self.db_manager.set_favorite(current_media.file_path, new_status)
        # Emit the Media object, not just the file_path (matches MediaViewer behavior)
        self.favorite_toggled.emit(current_media, new_status)

    def closeEvent(self, event: Optional[QCloseEvent]) -> None:
        """Handle window close event."""
        # Stop timers
        self.advance_timer.stop()
        self.ui_hide_timer.stop()

        # Clean up video player if it exists
        self._cleanup_video_player()

        # Reset state
        self.is_running = False
        self.is_paused = False

        # Show setup panel and cursor for next time
        self.setup_panel.show()
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # Emit closed signal
        self.closed.emit()

        super().closeEvent(event)
