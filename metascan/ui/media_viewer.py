"""
Media Viewer Widget for full-size media display with navigation.

Provides a full-screen media viewer with:
- Image display with automatic resizing to fit
- Video playback with controls
- Keyboard navigation (Esc, left/right arrows)
- Navigation limited to filtered media set
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl, QSize, QPoint
from PyQt6.QtGui import (
    QPixmap,
    QFont,
    QKeyEvent,
    QPainter,
    QImage,
    QAction,
    QShortcut,
    QKeySequence,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from pathlib import Path
from typing import List, Optional
import logging
import json

from metascan.core.media import Media
from metascan.utils.app_paths import get_config_path

logger = logging.getLogger(__name__)


class ImageViewer(QLabel):
    """Widget for displaying images with automatic resizing to fit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setScaledContents(False)
        self.current_pixmap = None

    def load_image(self, file_path: Path) -> bool:
        """Load and display an image file."""
        try:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                logger.error(f"Failed to load image: {file_path}")
                self.setText("Failed to load image")
                return False

            self.current_pixmap = pixmap
            self._update_display()
            return True

        except Exception as e:
            logger.error(f"Error loading image {file_path}: {e}")
            self.setText(f"Error: {str(e)}")
            return False

    def _update_display(self):
        """Update the display with the current pixmap scaled to fit."""
        if not self.current_pixmap:
            return

        # Scale pixmap to fit while maintaining aspect ratio
        scaled_pixmap = self.current_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        """Handle resize events to update the displayed image."""
        super().resizeEvent(event)
        if self.current_pixmap:
            self._update_display()

    def clear_image(self):
        """Clear the current image."""
        self.current_pixmap = None
        self.clear()


class VideoPlayer(QWidget):
    """Widget for playing videos with basic controls."""

    playback_state_changed = pyqtSignal(
        bool
    )  # True if playing, False if paused/stopped

    def __init__(self, parent=None):
        super().__init__(parent)

        # Create media player and video widget
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)

        # Set looping to infinite by default
        self.media_player.setLoops(QMediaPlayer.Loops.Infinite)

        # Load playback speed from config
        self.playback_speed = self._load_playback_speed()

        # Track last position for loop detection
        self._last_position = 0

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")
        self.video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.media_player.setVideoOutput(self.video_widget)

        # Create controls
        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(40, 30)
        self.play_button.clicked.connect(self.toggle_playback)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.set_position)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: white; padding: 5px;")

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.video_widget, 1)  # Give it stretch factor

        # Control bar
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.addWidget(self.play_button)
        control_layout.addWidget(self.position_slider)
        control_layout.addWidget(self.time_label)

        control_widget = QWidget()
        control_widget.setStyleSheet("background-color: rgba(0, 0, 0, 200);")
        control_widget.setLayout(control_layout)
        control_widget.setFixedHeight(50)

        layout.addWidget(control_widget)

        # Connect media player signals
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)
        self.media_player.playbackStateChanged.connect(self.update_playback_state)
        self.media_player.errorOccurred.connect(self.handle_error)
        self.media_player.mediaStatusChanged.connect(self.handle_media_status)

        # Timer for updating position
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_time_display)
        self.update_timer.start(100)

    def load_video(self, file_path: Path) -> bool:
        """Load and prepare a video file for playback."""
        try:
            # Stop any current playback
            self.media_player.stop()

            # Set the new source
            self.media_player.setSource(QUrl.fromLocalFile(str(file_path)))

            # Apply playback speed from config (reload in case config changed)
            self.playback_speed = self._load_playback_speed()
            self.media_player.setPlaybackRate(self.playback_speed)

            # Reset position and tracking
            self.position_slider.setValue(0)
            self._last_position = 0

            # Don't auto-play, let user control playback
            return True

        except Exception as e:
            logger.error(f"Error loading video {file_path}: {e}")
            return False

    def play(self):
        """Start video playback."""
        # Apply playback speed before playing (in case config changed)
        self.playback_speed = self._load_playback_speed()
        self.media_player.setPlaybackRate(self.playback_speed)
        self.media_player.play()

    def pause(self):
        """Pause video playback."""
        self.media_player.pause()

    def stop(self):
        """Stop video playback."""
        self.media_player.stop()

    def toggle_playback(self):
        """Toggle between play and pause."""
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def set_position(self, position):
        """Set playback position."""
        self.media_player.setPosition(position)

    def update_position(self, position):
        """Update position slider."""
        if not self.position_slider.isSliderDown():
            # Check if video has looped (position jumps from near end to near start)
            if (
                hasattr(self, "_last_position")
                and self._last_position > self.media_player.duration() - 1000
                and position < 1000
            ):
                # Video has looped, ensure slider resets to 0
                self.position_slider.setValue(0)
            else:
                self.position_slider.setValue(position)
            self._last_position = position

    def update_duration(self, duration):
        """Update duration when media is loaded."""
        self.position_slider.setRange(0, duration)

    def update_playback_state(self, state):
        """Update UI based on playback state."""
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("❚❚")
            self.playback_state_changed.emit(True)
        else:
            self.play_button.setText("▶")
            self.playback_state_changed.emit(False)

    def update_time_display(self):
        """Update the time display label."""
        position = self.media_player.position()
        duration = self.media_player.duration()

        position_str = self._format_time(position)
        duration_str = self._format_time(duration)

        self.time_label.setText(f"{position_str} / {duration_str}")

    def _format_time(self, ms):
        """Format milliseconds to MM:SS."""
        if ms <= 0:
            return "00:00"

        seconds = ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60

        return f"{minutes:02d}:{seconds:02d}"

    def handle_error(self, error):
        """Handle media player errors."""
        error_string = self.media_player.errorString()
        logger.error(f"Media player error: {error} - {error_string}")

    def handle_media_status(self, status):
        """Handle media status changes."""
        from PyQt6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            logger.info("Media loaded successfully")
            # Video is ready, show first frame
            self.media_player.pause()
            self.media_player.setPosition(0)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            logger.error("Invalid media format")
        elif status == QMediaPlayer.MediaStatus.NoMedia:
            logger.info("No media")

    def _load_playback_speed(self) -> float:
        """Load playback speed from config file."""
        try:
            config_path = get_config_path()
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
                    # Return configured speed, default to 1.0 if not set
                    speed = config.get("video_playback_speed", 1.0)
                    return float(speed) if isinstance(speed, (int, float)) else 1.0
        except Exception as e:
            logger.warning(f"Failed to load playback speed from config: {e}")
        return 1.0  # Default to normal speed

    def clear_video(self):
        """Clear the current video."""
        self.stop()
        self.media_player.setSource(QUrl())


class MediaViewer(QWidget):
    """
    Full-size media viewer with navigation support.

    Features:
    - Display images at full resolution (scaled to fit)
    - Play videos with controls
    - Navigate through filtered media with arrow keys
    - Close with Escape key
    - Delete media with Command-D
    """

    closed = pyqtSignal()
    media_changed = pyqtSignal(object)  # Emits current Media object
    delete_requested = pyqtSignal(object)  # Emits Media object to delete
    favorite_toggled = pyqtSignal(
        object, bool
    )  # Emits Media object and new favorite status

    def __init__(self, parent=None):
        super().__init__(parent)

        # Media list and current index
        self.media_list: List[Media] = []
        self.current_index: int = -1
        self.current_media: Optional[Media] = None

        # Setup UI
        self._setup_ui()
        self._setup_shortcuts()

        # Hide by default
        self.hide()

    def _setup_ui(self):
        """Setup the user interface."""
        self.setStyleSheet("background-color: black;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header with media info and close button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(10, 10, 10, 10)

        # Favorite button (star icon)
        self.favorite_button = QPushButton("☆")
        self.favorite_button.setFixedSize(30, 30)
        self.favorite_button.setStyleSheet(
            """
            QPushButton {
                color: white;
                background-color: transparent;
                border: none;
                font-size: 24px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 20);
            }
        """
        )
        self.favorite_button.clicked.connect(self.toggle_favorite)
        header_layout.addWidget(self.favorite_button)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: white; font-size: 14px;")
        header_layout.addWidget(self.info_label)

        header_layout.addStretch()

        self.close_button = QPushButton("✕")
        self.close_button.setStyleSheet(
            """
            QPushButton {
                color: white;
                background-color: rgba(255, 255, 255, 20);
                border: none;
                font-size: 20px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 40);
            }
        """
        )
        self.close_button.clicked.connect(self.close_viewer)
        header_layout.addWidget(self.close_button)

        layout.addLayout(header_layout)

        # Stacked widget for image/video display
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Image viewer
        self.image_viewer = ImageViewer()
        self.stacked_widget.addWidget(self.image_viewer)

        # Video player
        self.video_player = VideoPlayer()
        self.stacked_widget.addWidget(self.video_player)

        layout.addWidget(self.stacked_widget, 1)  # Give it stretch factor

        # Navigation buttons
        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(10, 10, 10, 10)

        self.prev_button = QPushButton("◀ Previous")
        self.prev_button.setStyleSheet(
            """
            QPushButton {
                color: white;
                background-color: rgba(255, 255, 255, 20);
                border: none;
                padding: 10px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 40);
            }
            QPushButton:disabled {
                color: gray;
                background-color: rgba(255, 255, 255, 10);
            }
        """
        )
        self.prev_button.clicked.connect(self.show_previous)
        nav_layout.addWidget(self.prev_button)

        nav_layout.addStretch()

        self.position_label = QLabel()
        self.position_label.setStyleSheet("color: white; font-size: 14px;")
        nav_layout.addWidget(self.position_label)

        nav_layout.addStretch()

        self.next_button = QPushButton("Next ▶")
        self.next_button.setStyleSheet(
            """
            QPushButton {
                color: white;
                background-color: rgba(255, 255, 255, 20);
                border: none;
                padding: 10px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 40);
            }
            QPushButton:disabled {
                color: gray;
                background-color: rgba(255, 255, 255, 10);
            }
        """
        )
        self.next_button.clicked.connect(self.show_next)
        nav_layout.addWidget(self.next_button)

        layout.addLayout(nav_layout)

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Escape to close
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_shortcut.activated.connect(self.close_viewer)

        # Left arrow for previous
        left_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        left_shortcut.activated.connect(self.show_previous)

        # Right arrow for next
        right_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        right_shortcut.activated.connect(self.show_next)

        # Space to toggle video playback
        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space_shortcut.activated.connect(self._toggle_video_playback)

        # Command-D (or Ctrl-D) to delete
        delete_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        delete_shortcut.activated.connect(self._request_delete)

        # F to toggle favorite
        favorite_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F), self)
        favorite_shortcut.activated.connect(self.toggle_favorite)

    def set_media_list(
        self, media_list: List[Media], initial_media: Optional[Media] = None
    ):
        """
        Set the list of media to navigate through.

        Args:
            media_list: List of Media objects (should be filtered)
            initial_media: Optional initial media to display
        """
        self.media_list = media_list

        if initial_media and initial_media in media_list:
            self.current_index = media_list.index(initial_media)
        elif media_list:
            self.current_index = 0
        else:
            self.current_index = -1

        if self.current_index >= 0:
            self._display_current_media()

    def show_media(self, media: Media):
        """
        Show a specific media item.

        Args:
            media: Media object to display
        """
        if media in self.media_list:
            self.current_index = self.media_list.index(media)
            self._display_current_media()
            self.show()
            self.raise_()
            self.setFocus()

    def _display_current_media(self):
        """Display the current media item."""
        if self.current_index < 0 or self.current_index >= len(self.media_list):
            return

        self.current_media = self.media_list[self.current_index]

        # Update info label
        self.info_label.setText(self.current_media.file_name)

        # Update favorite button
        self._update_favorite_button()

        # Update position label
        self.position_label.setText(
            f"{self.current_index + 1} / {len(self.media_list)}"
        )

        # Update navigation buttons
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.media_list) - 1)

        # Load media
        if self.current_media.is_video:
            # Show video player
            self.stacked_widget.setCurrentWidget(self.video_player)
            success = self.video_player.load_video(self.current_media.file_path)
            if not success:
                self.info_label.setText(
                    f"Failed to load video: {self.current_media.file_name}"
                )
        else:
            # Show image viewer
            self.stacked_widget.setCurrentWidget(self.image_viewer)
            success = self.image_viewer.load_image(self.current_media.file_path)
            if not success:
                self.info_label.setText(
                    f"Failed to load image: {self.current_media.file_name}"
                )

        # Emit signal
        self.media_changed.emit(self.current_media)

    def show_previous(self):
        """Show the previous media item."""
        if self.current_index > 0:
            # Stop video if playing
            if self.current_media and self.current_media.is_video:
                self.video_player.stop()

            self.current_index -= 1
            self._display_current_media()

    def show_next(self):
        """Show the next media item."""
        if self.current_index < len(self.media_list) - 1:
            # Stop video if playing
            if self.current_media and self.current_media.is_video:
                self.video_player.stop()

            self.current_index += 1
            self._display_current_media()

    def _toggle_video_playback(self):
        """Toggle video playback if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                self.video_player.toggle_playback()

    def close_viewer(self):
        """Close the media viewer."""
        # Stop video if playing
        if self.current_media and self.current_media.is_video:
            self.video_player.stop()

        # Clear media
        self.image_viewer.clear_image()
        self.video_player.clear_video()

        # Hide viewer
        self.hide()

        # Emit closed signal
        self.closed.emit()

    def _request_delete(self):
        """Request deletion of the current media."""
        if self.current_media:
            self.delete_requested.emit(self.current_media)

    def toggle_favorite(self):
        """Toggle the favorite status of the current media."""
        if self.current_media:
            # Toggle the favorite status
            new_status = not self.current_media.is_favorite
            self.current_media.is_favorite = new_status

            # Update the UI
            self._update_favorite_button()

            # Emit signal to update database
            self.favorite_toggled.emit(self.current_media, new_status)

    def _update_favorite_button(self):
        """Update the favorite button icon based on current media's favorite status."""
        if self.current_media and self.current_media.is_favorite:
            self.favorite_button.setText("★")
            self.favorite_button.setStyleSheet(
                """
                QPushButton {
                    color: gold;
                    background-color: transparent;
                    border: none;
                    font-size: 24px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 20);
                }
            """
            )
        else:
            self.favorite_button.setText("☆")
            self.favorite_button.setStyleSheet(
                """
                QPushButton {
                    color: white;
                    background-color: transparent;
                    border: none;
                    font-size: 24px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 20);
                }
            """
            )

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        """Handle key press events."""
        # Let shortcuts handle the keys
        super().keyPressEvent(event)
