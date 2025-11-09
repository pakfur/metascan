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
    QComboBox,
    QToolTip,
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
import subprocess
import platform

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

        # Zoom and pan state
        self.zoom_factor = 1.0
        self.min_zoom = 0.1  # 10% minimum zoom
        self.max_zoom = 10.0  # 1000% maximum zoom
        self.zoom_step = 0.1  # 10% zoom change per scroll step
        self.pan_offset = QPoint(0, 0)  # Offset for panning

        # Panning state
        self.is_panning = False
        self.last_pan_point = QPoint()

    def load_image(self, file_path: Path) -> bool:
        """Load and display an image file."""
        try:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                logger.error(f"Failed to load image: {file_path}")
                self.setText("Failed to load image")
                return False

            self.current_pixmap = pixmap
            # Reset zoom and pan when loading new image
            self.zoom_factor = 1.0
            self.pan_offset = QPoint(0, 0)
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

        # Calculate the base size (fit to window)
        base_scaled_size = self.current_pixmap.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )

        # Apply zoom factor
        zoomed_size = base_scaled_size * self.zoom_factor

        # Scale pixmap with zoom applied
        scaled_pixmap = self.current_pixmap.scaled(
            zoomed_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # When zoomed, we need to handle panning by cropping/positioning
        if self.zoom_factor > 1.0:
            # Create a canvas the size of the widget
            canvas = QPixmap(self.size())
            canvas.fill(Qt.GlobalColor.black)

            # Calculate position to draw the zoomed image with pan offset
            x = (self.width() - scaled_pixmap.width()) // 2 + self.pan_offset.x()
            y = (self.height() - scaled_pixmap.height()) // 2 + self.pan_offset.y()

            # Draw the scaled pixmap onto the canvas
            painter = QPainter(canvas)
            painter.drawPixmap(x, y, scaled_pixmap)
            painter.end()

            self.setPixmap(canvas)
        else:
            # No zoom or zoomed out - just center the image
            self.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        """Handle resize events to update the displayed image."""
        super().resizeEvent(event)
        if self.current_pixmap:
            self._update_display()

    def wheelEvent(self, event):
        """Handle mouse wheel events for zooming."""
        if not self.current_pixmap:
            return

        # Get the mouse position relative to the widget
        mouse_pos = event.position().toPoint()

        # Calculate the base size (fit to window) for reference
        base_scaled_size = self.current_pixmap.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )

        # Calculate current zoomed size
        old_zoomed_size = base_scaled_size * self.zoom_factor

        # Calculate position of mouse in image coordinates (before zoom)
        # Account for centering and pan offset
        image_x_before = (
            self.width() - old_zoomed_size.width()
        ) // 2 + self.pan_offset.x()
        image_y_before = (
            self.height() - old_zoomed_size.height()
        ) // 2 + self.pan_offset.y()

        # Mouse position relative to image top-left
        rel_x = mouse_pos.x() - image_x_before
        rel_y = mouse_pos.y() - image_y_before

        # Calculate relative position as percentage of image size
        if old_zoomed_size.width() > 0 and old_zoomed_size.height() > 0:
            rel_x_pct = rel_x / old_zoomed_size.width()
            rel_y_pct = rel_y / old_zoomed_size.height()
        else:
            rel_x_pct = 0.5
            rel_y_pct = 0.5

        # Update zoom factor based on scroll direction
        # angleDelta().y() is positive for scroll up (zoom in), negative for scroll down (zoom out)
        old_zoom = self.zoom_factor
        if event.angleDelta().y() > 0:
            # Scroll up - zoom in
            self.zoom_factor = min(self.max_zoom, self.zoom_factor + self.zoom_step)
        else:
            # Scroll down - zoom out
            self.zoom_factor = max(self.min_zoom, self.zoom_factor - self.zoom_step)

        # If zoom didn't change (hit bounds), do nothing
        if self.zoom_factor == old_zoom:
            return

        # Calculate new zoomed size
        new_zoomed_size = base_scaled_size * self.zoom_factor

        # Calculate where the same point in the image should be after zoom
        new_rel_x = rel_x_pct * new_zoomed_size.width()
        new_rel_y = rel_y_pct * new_zoomed_size.height()

        # Calculate new image top-left to keep mouse point at same screen position
        new_image_x = mouse_pos.x() - new_rel_x
        new_image_y = mouse_pos.y() - new_rel_y

        # Update pan offset to maintain the focal point
        center_x = (self.width() - new_zoomed_size.width()) // 2
        center_y = (self.height() - new_zoomed_size.height()) // 2
        self.pan_offset.setX(int(new_image_x - center_x))
        self.pan_offset.setY(int(new_image_y - center_y))

        # Reset pan offset when zoomed out to fit or smaller
        if self.zoom_factor <= 1.0:
            self.pan_offset = QPoint(0, 0)

        self._update_display()
        event.accept()

    def mousePressEvent(self, event):
        """Handle mouse press events for panning."""
        if event.button() == Qt.MouseButton.LeftButton and self.zoom_factor > 1.0:
            self.is_panning = True
            self.last_pan_point = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move events for panning."""
        if self.is_panning:
            delta = event.pos() - self.last_pan_point
            self.pan_offset += delta
            self.last_pan_point = event.pos()
            self._update_display()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release events for panning."""
        if event.button() == Qt.MouseButton.LeftButton and self.is_panning:
            self.is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def clear_image(self):
        """Clear the current image."""
        self.current_pixmap = None
        self.zoom_factor = 1.0
        self.pan_offset = QPoint(0, 0)
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

        # Define available playback speeds (needed before loading config)
        self.available_speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

        # Load playback speed from config
        self.playback_speed = self._load_playback_speed()

        # Track last position for loop detection
        self._last_position = 0

        # Track current file for fallback handling
        self._current_file_path = None
        self._fallback_attempted = False

        # Commonly supported formats by QMediaPlayer
        self._preferred_formats = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
        self._preferred_codecs = {"h264", "h265", "vp8", "vp9", "av1"}

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")
        self.video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.media_player.setVideoOutput(self.video_widget)

        # Create playback controls
        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(40, 30)
        self.play_button.clicked.connect(self.toggle_playback)
        self.play_button.setToolTip("Play/Pause (Space)")

        # Frame-by-frame navigation buttons
        self.prev_frame_button = QPushButton("◀◀")
        self.prev_frame_button.setFixedSize(40, 30)
        self.prev_frame_button.clicked.connect(self.previous_frame)
        self.prev_frame_button.setToolTip("Previous Frame (,)")

        self.next_frame_button = QPushButton("▶▶")
        self.next_frame_button.setFixedSize(40, 30)
        self.next_frame_button.clicked.connect(self.next_frame)
        self.next_frame_button.setToolTip("Next Frame (.)")

        # Position slider
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.set_position)

        # Time label
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: white; padding: 5px;")

        # Playback speed selector
        self.speed_selector = QComboBox()
        self.speed_selector.addItems([f"{s:g}x" for s in self.available_speeds])
        # Format speed to match dropdown options (remove trailing zeros)
        speed_text = f"{self.playback_speed:g}x"  # :g removes trailing zeros
        self.speed_selector.setCurrentText(speed_text)
        self.speed_selector.currentTextChanged.connect(self.change_playback_speed)
        self.speed_selector.setFixedWidth(70)
        self.speed_selector.setStyleSheet(
            "color: white; background-color: rgba(50, 50, 50, 200); padding: 2px;"
        )
        self.speed_selector.setToolTip("Playback Speed")

        # Volume controls
        self.mute_button = QPushButton("🔊")
        self.mute_button.setFixedSize(40, 30)
        self.mute_button.setStyleSheet("padding: 2px;")
        self.mute_button.clicked.connect(self.toggle_mute)
        self.mute_button.setToolTip("Mute (M)")

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.audio_output.volume() * 100))
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.volume_slider.setToolTip("Volume (↑/↓)")

        # Keyboard shortcuts help button
        self.shortcuts_button = QPushButton("?")
        self.shortcuts_button.setFixedSize(30, 30)
        self.shortcuts_button.clicked.connect(self.show_keyboard_shortcuts)
        self.shortcuts_button.setToolTip("Show Keyboard Shortcuts (H)")

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.video_widget, 1)  # Give it stretch factor

        # Control bar - Left section (playback controls)
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(10, 5, 10, 5)
        control_layout.addWidget(self.prev_frame_button)
        control_layout.addWidget(self.play_button)
        control_layout.addWidget(self.next_frame_button)
        control_layout.addWidget(self.position_slider, 1)  # Give slider stretch
        control_layout.addWidget(self.time_label)

        # Right section (speed, volume, help)
        control_layout.addWidget(self.speed_selector)
        control_layout.addWidget(self.mute_button)
        control_layout.addWidget(self.volume_slider)
        control_layout.addWidget(self.shortcuts_button)

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

            # Reset fallback state for new file
            self._current_file_path = file_path
            self._fallback_attempted = False

            # Check if file exists and is readable
            if not file_path.exists():
                logger.error(f"Video file does not exist: {file_path}")
                return False

            # Check file permissions and access
            if not self._check_file_access(file_path):
                logger.error(
                    f"Cannot access video file (permission denied): {file_path}"
                )
                self._show_permission_error(file_path)
                return False

            # Validate format before attempting to load
            format_supported = self._is_format_likely_supported(file_path)
            if not format_supported:
                logger.warning(
                    f"Format may not be supported by QMediaPlayer: {file_path.suffix}"
                )

            # Log codec information for debugging
            codec_info = self._get_codec_info(file_path)
            if codec_info:
                logger.info(f"Video codec info for {file_path.name}: {codec_info}")

                # Check if codec is likely supported
                if (
                    not self._is_codec_likely_supported(codec_info)
                    and not format_supported
                ):
                    logger.warning(
                        f"Codec and format may not be supported, attempting fallback first"
                    )
                    self._attempt_fallback()
                    return False

            # Set the new source
            self.media_player.setSource(QUrl.fromLocalFile(str(file_path)))

            # Apply playback speed from config (reload in case config changed)
            self.playback_speed = self._load_playback_speed()
            self.media_player.setPlaybackRate(self.playback_speed)

            # Update UI to reflect the playback speed (block signals to avoid recursion)
            speed_text = f"{self.playback_speed:g}x"
            self.speed_selector.blockSignals(True)
            index = self.speed_selector.findText(speed_text)
            if index >= 0:
                self.speed_selector.setCurrentIndex(index)
            else:
                # If exact match not found, just set the text (shouldn't happen after snapping)
                self.speed_selector.setCurrentText(speed_text)
                logger.warning(
                    f"Speed text '{speed_text}' not found in dropdown options"
                )
            self.speed_selector.blockSignals(False)

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
        # Apply current playback speed (from UI or config)
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

        # Try to provide more helpful error information
        from PyQt6.QtMultimedia import QMediaPlayer

        if error == QMediaPlayer.Error.ResourceError:
            if self._current_file_path and not self._fallback_attempted:
                # Check if this might be a permission issue
                if not self._check_file_access(self._current_file_path):
                    logger.info(
                        f"ResourceError due to file access issue: {self._current_file_path}"
                    )
                    self._show_permission_error(self._current_file_path)
                else:
                    logger.info(
                        f"ResourceError for supported file, attempting fallback: {self._current_file_path}"
                    )
                    self._attempt_fallback()
            else:
                logger.warning(f"Could not play video file: {self._current_file_path}")
                self._show_format_error()

    def handle_media_status(self, status):
        """Handle media status changes."""
        from PyQt6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            logger.info("Media loaded successfully")
            # Video is ready, show first frame
            self.media_player.pause()
            self.media_player.setPosition(0)
            # Reset fallback flag on successful load
            self._fallback_attempted = False
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            logger.error("Invalid media format")
            if self._current_file_path and not self._fallback_attempted:
                logger.info(
                    f"Invalid media format, attempting fallback: {self._current_file_path}"
                )
                self._attempt_fallback()
            else:
                self._show_format_error()
        elif status == QMediaPlayer.MediaStatus.NoMedia:
            logger.info("No media")

    def _find_closest_speed(self, target_speed: float) -> float:
        """Find the closest available speed from the dropdown options."""
        if not self.available_speeds:
            return 1.0

        # Find the speed with minimum distance
        closest: float = min(self.available_speeds, key=lambda x: abs(x - target_speed))
        return closest

    def _save_playback_speed(self, speed: float) -> None:
        """Save playback speed to config file."""
        try:
            config_path = get_config_path()
            config = {}

            # Load existing config
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)

            # Update speed
            config["video_playback_speed"] = speed

            # Save config
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            logger.info(f"Saved playback speed {speed}x to config")
        except Exception as e:
            logger.warning(f"Failed to save playback speed to config: {e}")

    def _load_playback_speed(self) -> float:
        """Load playback speed from config file and snap to nearest available speed."""
        try:
            config_path = get_config_path()
            if config_path.exists():
                with open(config_path, "r") as f:
                    config = json.load(f)
                    # Return configured speed, default to 1.0 if not set
                    speed = config.get("video_playback_speed", 1.0)
                    configured_speed = (
                        float(speed) if isinstance(speed, (int, float)) else 1.0
                    )

                    # Snap to closest available speed
                    snapped_speed = self._find_closest_speed(configured_speed)

                    # If speed was adjusted, save the new value back to config
                    if abs(snapped_speed - configured_speed) > 0.01:
                        logger.info(
                            f"Adjusting playback speed from {configured_speed}x to nearest available {snapped_speed}x"
                        )
                        self._save_playback_speed(snapped_speed)

                    return snapped_speed
        except Exception as e:
            logger.warning(f"Failed to load playback speed from config: {e}")
        return 1.0  # Default to normal speed

    def _get_codec_info(self, file_path: Path) -> Optional[str]:
        """Get codec information for a video file using ffprobe."""
        try:
            import ffmpeg

            probe = ffmpeg.probe(str(file_path))
            video_streams = [
                stream for stream in probe["streams"] if stream["codec_type"] == "video"
            ]
            if video_streams:
                codec = video_streams[0].get("codec_name", "unknown")
                return f"Codec: {codec}"
        except Exception as e:
            logger.debug(f"Could not get codec info for {file_path}: {e}")
        return None

    def _attempt_fallback(self):
        """Attempt to open video in system default player as fallback."""
        if not self._current_file_path or self._fallback_attempted:
            return

        self._fallback_attempted = True
        logger.info(
            f"Opening video in system default player: {self._current_file_path}"
        )

        try:
            if platform.system() == "Darwin":  # macOS
                subprocess.run(["open", str(self._current_file_path)], check=False)
            elif platform.system() == "Windows":
                subprocess.run(
                    ["start", str(self._current_file_path)], shell=True, check=False
                )
            else:  # Linux
                subprocess.run(["xdg-open", str(self._current_file_path)], check=False)

            logger.info(
                f"Opened media file in default viewer: {self._current_file_path}"
            )
        except Exception as e:
            logger.error(f"Failed to open video in system player: {e}")

    def _show_format_error(self):
        """Show user-friendly error message for unsupported format."""
        if self._current_file_path:
            codec_info = self._get_codec_info(self._current_file_path)
            error_msg = f"Unsupported video format: {self._current_file_path.name}"
            if codec_info:
                error_msg += f" ({codec_info})"
            error_msg += "\nTrying external player..."
            logger.warning(error_msg)

    def _is_format_likely_supported(self, file_path: Path) -> bool:
        """Check if the file format is likely supported by QMediaPlayer."""
        return file_path.suffix.lower() in self._preferred_formats

    def _is_codec_likely_supported(self, codec_info: str) -> bool:
        """Check if the codec is likely supported by QMediaPlayer."""
        if not codec_info:
            return False
        codec_lower = codec_info.lower()
        return any(
            supported_codec in codec_lower for supported_codec in self._preferred_codecs
        )

    def _check_file_access(self, file_path: Path) -> bool:
        """Check if the file is accessible for reading."""
        try:
            # Try to open the file for reading
            with open(file_path, "rb") as f:
                # Read just a small chunk to verify access
                f.read(1024)
            return True
        except (PermissionError, OSError, IOError) as e:
            logger.warning(f"File access check failed for {file_path}: {e}")
            return False

    def _show_permission_error(self, file_path: Path):
        """Handle permission errors with helpful user messaging."""
        logger.error(f"Permission denied accessing file: {file_path}")

        # Check for common macOS security restrictions
        if platform.system() == "Darwin":
            logger.info("On macOS, this might be due to:")
            logger.info(
                "- File quarantine attributes (try: xattr -r -d com.apple.quarantine file)"
            )
            logger.info("- System security restrictions")
            logger.info("- File located on restricted volume")

        # Still attempt fallback to external player
        logger.info("Attempting to open with system default player...")
        self._attempt_fallback()

    def change_volume(self, value):
        """Change the volume level."""
        volume = value / 100.0
        self.audio_output.setVolume(volume)
        # Update mute button icon based on volume
        if volume == 0:
            self.mute_button.setText("🔇")
        elif volume < 0.5:
            self.mute_button.setText("🔉")
        else:
            self.mute_button.setText("🔊")

    def toggle_mute(self):
        """Toggle mute on/off."""
        is_muted = self.audio_output.isMuted()
        self.audio_output.setMuted(not is_muted)

        if not is_muted:
            self.mute_button.setText("🔇")
        else:
            # Restore volume icon based on current volume
            volume = self.audio_output.volume()
            if volume < 0.5:
                self.mute_button.setText("🔉")
            else:
                self.mute_button.setText("🔊")

    def change_playback_speed(self, speed_text):
        """Change the playback speed."""
        # Extract numeric value from text like "1.5x"
        try:
            speed = float(speed_text.replace("x", ""))
            self.playback_speed = speed

            # Always set the playback rate - it will take effect immediately
            # for playing videos and be stored for paused videos
            self.media_player.setPlaybackRate(speed)

            # Save the new speed to config so it persists
            self._save_playback_speed(speed)

            logger.info(f"Playback speed changed to {speed}x")
        except ValueError:
            logger.error(f"Invalid speed format: {speed_text}")

    def previous_frame(self):
        """Go back one frame (approximately)."""
        # Pause playback for frame stepping
        was_playing = (
            self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if was_playing:
            self.pause()

        # Estimate frame duration (assuming 30fps, adjust as needed)
        frame_duration_ms = 33  # ~33ms per frame at 30fps
        current_pos = self.media_player.position()
        new_pos = max(0, current_pos - frame_duration_ms)
        self.media_player.setPosition(new_pos)

    def next_frame(self):
        """Go forward one frame (approximately)."""
        # Pause playback for frame stepping
        was_playing = (
            self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if was_playing:
            self.pause()

        # Estimate frame duration (assuming 30fps, adjust as needed)
        frame_duration_ms = 33  # ~33ms per frame at 30fps
        current_pos = self.media_player.position()
        duration = self.media_player.duration()
        new_pos = min(duration, current_pos + frame_duration_ms)
        self.media_player.setPosition(new_pos)

    def show_keyboard_shortcuts(self):
        """Display keyboard shortcuts help overlay."""
        shortcuts_text = """
        <h3 style='margin-top: 0;'>Video Player Keyboard Shortcuts</h3>
        <table style='color: white;'>
        <tr><td><b>Space</b></td><td>Play/Pause</td></tr>
        <tr><td><b>, (comma)</b></td><td>Previous Frame</td></tr>
        <tr><td><b>. (period)</b></td><td>Next Frame</td></tr>
        <tr><td><b>M</b></td><td>Toggle Mute</td></tr>
        <tr><td><b>↑</b></td><td>Volume Up</td></tr>
        <tr><td><b>↓</b></td><td>Volume Down</td></tr>
        <tr><td><b>←</b></td><td>Previous Media</td></tr>
        <tr><td><b>→</b></td><td>Next Media</td></tr>
        <tr><td><b>F</b></td><td>Toggle Favorite</td></tr>
        <tr><td><b>Ctrl+D</b></td><td>Delete Media</td></tr>
        <tr><td><b>H or ?</b></td><td>Show This Help</td></tr>
        <tr><td><b>Esc</b></td><td>Close Viewer</td></tr>
        </table>
        """

        # Show as tooltip at the center of the video widget
        QToolTip.showText(
            self.video_widget.mapToGlobal(self.video_widget.rect().center()),
            shortcuts_text,
            self.video_widget,
            self.video_widget.rect(),
            5000,  # Show for 5 seconds
        )

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        """Handle keyboard shortcuts for video control."""
        if event is None:
            return

        key = event.key()

        # Volume control
        if key == Qt.Key.Key_Up:
            current_volume = self.volume_slider.value()
            self.volume_slider.setValue(min(100, current_volume + 5))
            event.accept()
            return
        elif key == Qt.Key.Key_Down:
            current_volume = self.volume_slider.value()
            self.volume_slider.setValue(max(0, current_volume - 5))
            event.accept()
            return

        # Mute toggle
        elif key == Qt.Key.Key_M:
            self.toggle_mute()
            event.accept()
            return

        # Frame navigation
        elif key == Qt.Key.Key_Comma:
            self.previous_frame()
            event.accept()
            return
        elif key == Qt.Key.Key_Period:
            self.next_frame()
            event.accept()
            return

        # Help shortcuts
        elif key == Qt.Key.Key_H or key == Qt.Key.Key_Question:
            self.show_keyboard_shortcuts()
            event.accept()
            return

        # Let parent handle other keys (Space, arrows, etc.)
        super().keyPressEvent(event)

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

        # Video-specific shortcuts (delegated to video player)
        # M to toggle mute
        mute_shortcut = QShortcut(QKeySequence(Qt.Key.Key_M), self)
        mute_shortcut.activated.connect(self._toggle_video_mute)

        # Up arrow to increase volume
        vol_up_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        vol_up_shortcut.activated.connect(self._increase_volume)

        # Down arrow to decrease volume
        vol_down_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        vol_down_shortcut.activated.connect(self._decrease_volume)

        # Comma to go to previous frame
        prev_frame_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Comma), self)
        prev_frame_shortcut.activated.connect(self._video_previous_frame)

        # Period to go to next frame
        next_frame_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Period), self)
        next_frame_shortcut.activated.connect(self._video_next_frame)

        # H to show keyboard shortcuts
        help_shortcut = QShortcut(QKeySequence(Qt.Key.Key_H), self)
        help_shortcut.activated.connect(self._show_help)

        # Question mark to show keyboard shortcuts
        help_shortcut2 = QShortcut(QKeySequence(Qt.Key.Key_Question), self)
        help_shortcut2.activated.connect(self._show_help)

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

    def _toggle_video_mute(self):
        """Toggle video mute if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                self.video_player.toggle_mute()

    def _increase_volume(self):
        """Increase video volume if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                current_volume = self.video_player.volume_slider.value()
                self.video_player.volume_slider.setValue(min(100, current_volume + 5))

    def _decrease_volume(self):
        """Decrease video volume if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                current_volume = self.video_player.volume_slider.value()
                self.video_player.volume_slider.setValue(max(0, current_volume - 5))

    def _video_previous_frame(self):
        """Go to previous frame if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                self.video_player.previous_frame()

    def _video_next_frame(self):
        """Go to next frame if a video is displayed."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                self.video_player.next_frame()

    def _show_help(self):
        """Show keyboard shortcuts help."""
        if self.current_media and self.current_media.is_video:
            if self.stacked_widget.currentWidget() == self.video_player:
                self.video_player.show_keyboard_shortcuts()

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
