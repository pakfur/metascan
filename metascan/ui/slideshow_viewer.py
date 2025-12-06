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
    QGraphicsOpacityEffect,
    QGraphicsBlurEffect,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    QTimer,
    QPoint,
    QCoreApplication,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
)
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
        self.transition_effect = "None"  # "None", "Fade", or "Slide"
        self.transition_duration = 1000  # milliseconds (default 1.0s)

        # Transition animation
        self.transition_animation: Optional[QPropertyAnimation] = None
        # Store widgets for two-stage slide transition
        self.slide_new_widget: Optional[QWidget] = None

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
        # Ensure stacked widget expands to fill available space
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Create image viewer
        self.image_viewer = ImageViewer()
        self.stacked_widget.addWidget(self.image_viewer)

        # Video player will be created on demand to avoid state issues

        # Add stacked widget to main layout with stretch factor 1 (takes all available space)
        main_layout.addWidget(self.stacked_widget, 1)

        # Create setup panel at the bottom (not overlaid) with stretch factor 0 (fixed size)
        self.setup_panel = self._create_setup_panel()
        # Ensure setup panel has fixed vertical size
        self.setup_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        main_layout.addWidget(self.setup_panel, 0)

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
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QRadioButton {
                color: white;
                font-size: 12px;
                spacing: 5px;
            }
            QComboBox {
                background-color: #424242;
                color: white;
                border: 1px solid #666;
                border-radius: 1px;
                padding: 2px 8px;
                font-size: 12px;
                min-width: 120px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 2px solid transparent;
                border-right: 2px solid transparent;
                border-top: 2px solid white;
                margin-right: 2px;
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

        # Spacer
        layout.addStretch()

        # Effects selector
        effects_label = QLabel("Effects:")
        self.effects_combo = QComboBox()
        self.effects_combo.addItems(["None", "Fade", "Blur", "Zoom"])
        self.effects_combo.setCurrentIndex(1)  # Default to Fade

        layout.addWidget(effects_label)
        layout.addWidget(self.effects_combo)

        # Spacer
        layout.addStretch()

        # Transition duration selector
        transition_duration_label = QLabel("Transition Duration:")
        self.transition_duration_combo = QComboBox()
        self.transition_duration_combo.addItems(
            ["0.25 seconds", "0.5 seconds", "0.75 seconds", "1 second", "1.5 seconds"]
        )
        self.transition_duration_combo.setCurrentIndex(3)  # Default to 1 second

        layout.addWidget(transition_duration_label)
        layout.addWidget(self.transition_duration_combo)

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

        # Get transition effect
        self.transition_effect = self.effects_combo.currentText()

        # Get transition duration
        duration_text = self.transition_duration_combo.currentText()
        if "second" in duration_text:
            # Parse the duration (e.g., "0.5 seconds" or "1 second")
            duration_value = float(duration_text.split()[0])
            self.transition_duration = int(duration_value * 1000)

        # Hide setup panel and constrain stacked widget to prevent layout expansion
        panel_height = self.setup_panel.height()
        self.setup_panel.hide()

        # Prevent stacked widget from expanding into panel's space
        # Set max height to window height minus panel height
        max_height = self.height() - panel_height
        self.stacked_widget.setMaximumHeight(max_height)

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

    def _apply_fade_transition(self, widget: QWidget):
        """Apply fade-in transition to a widget."""
        if self.transition_effect != "Fade" or not self.is_running:
            return

        # Stop any existing animation
        if self.transition_animation is not None:
            self.transition_animation.stop()
            self.transition_animation.deleteLater()

        # Create opacity effect
        opacity_effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(opacity_effect)

        # Create animation
        self.transition_animation = QPropertyAnimation(opacity_effect, b"opacity")
        self.transition_animation.setDuration(self.transition_duration)
        self.transition_animation.setStartValue(0.0)
        self.transition_animation.setEndValue(1.0)
        self.transition_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Clean up effect after animation completes
        def cleanup():
            widget.setGraphicsEffect(None)
            if self.transition_animation is not None:
                self.transition_animation.deleteLater()
                self.transition_animation = None

        self.transition_animation.finished.connect(cleanup)
        self.transition_animation.start()

    def _apply_blur_transition(self, widget: QWidget):
        """Apply blur transition to a widget."""
        if self.transition_effect != "Blur" or not self.is_running:
            return

        # Stop any existing animation
        if self.transition_animation is not None:
            self.transition_animation.stop()
            self.transition_animation.deleteLater()

        # Create blur effect
        blur_effect = QGraphicsBlurEffect(widget)
        widget.setGraphicsEffect(blur_effect)

        # Create animation - start blurred, end sharp
        self.transition_animation = QPropertyAnimation(blur_effect, b"blurRadius")
        self.transition_animation.setDuration(self.transition_duration)
        self.transition_animation.setStartValue(20.0)  # Start blurred
        self.transition_animation.setEndValue(0.0)  # End sharp
        self.transition_animation.setEasingCurve(QEasingCurve.Type.OutQuad)

        # Clean up effect after animation completes
        def cleanup():
            widget.setGraphicsEffect(None)
            if self.transition_animation is not None:
                self.transition_animation.deleteLater()
                self.transition_animation = None

        self.transition_animation.finished.connect(cleanup)
        self.transition_animation.start()

    def _apply_zoom_transition(self, widget: QWidget):
        """Apply zoom transition to a widget."""
        if self.transition_effect != "Zoom" or not self.is_running:
            return

        # Stop any existing animation
        if self.transition_animation is not None:
            self.transition_animation.stop()
            self.transition_animation.deleteLater()

        # Create opacity effect
        opacity_effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(opacity_effect)

        # Create animation - fade in (simulates zoom in effect)
        self.transition_animation = QPropertyAnimation(opacity_effect, b"opacity")
        self.transition_animation.setDuration(self.transition_duration)
        self.transition_animation.setStartValue(0.0)
        self.transition_animation.setEndValue(1.0)
        self.transition_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )  # Different easing for zoom feel

        # Clean up effect after animation completes
        def cleanup():
            widget.setGraphicsEffect(None)
            if self.transition_animation is not None:
                self.transition_animation.deleteLater()
                self.transition_animation = None

        self.transition_animation.finished.connect(cleanup)
        self.transition_animation.start()

    def _old_slide_transition(self, old_widget: QWidget, new_widget: QWidget):
        """OLD SLIDE - TO BE REMOVED."""
        if self.transition_effect != "Slide" or not self.is_running:
            return

        # Stop any existing animation
        if self.transition_animation is not None:
            self.transition_animation.stop()
            self.transition_animation.deleteLater()

        # Store new widget for second stage
        self.slide_new_widget = new_widget

        # Get the stacked widget geometry
        stacked_geom = self.stacked_widget.geometry()

        # Stage 1: Slide old widget out to the left
        old_pos = old_widget.pos()
        end_x = -stacked_geom.width()

        self.transition_animation = QPropertyAnimation(old_widget, b"pos")
        self.transition_animation.setDuration(
            self.transition_duration // 2
        )  # Half duration for slide out
        self.transition_animation.setStartValue(old_pos)
        self.transition_animation.setEndValue(QPoint(end_x, old_pos.y()))
        self.transition_animation.setEasingCurve(QEasingCurve.Type.InCubic)

        # When slide-out completes, start slide-in
        self.transition_animation.finished.connect(self._slide_in_new_widget)
        self.transition_animation.start()

    def _slide_in_new_widget(self):
        """Stage 2 of slide transition: slide in the new widget from the right."""
        if self.slide_new_widget is None:
            return

        new_widget = self.slide_new_widget
        self.slide_new_widget = None

        # Reset old widget position (it's now hidden by stacked widget)
        old_widget = self.stacked_widget.currentWidget()
        if old_widget is not None:
            old_widget.move(0, old_widget.pos().y())

        # Get the stacked widget geometry
        stacked_geom = self.stacked_widget.geometry()

        # Switch to new widget
        self.stacked_widget.setCurrentWidget(new_widget)

        # Position new widget off-screen to the right
        new_pos = new_widget.pos()
        start_x = stacked_geom.width()
        new_widget.move(start_x, new_pos.y())

        # Clean up old animation
        if self.transition_animation is not None:
            self.transition_animation.deleteLater()

        # Stage 2: Slide new widget in from the right
        self.transition_animation = QPropertyAnimation(new_widget, b"pos")
        self.transition_animation.setDuration(
            self.transition_duration // 2
        )  # Half duration for slide in
        self.transition_animation.setStartValue(QPoint(start_x, new_pos.y()))
        self.transition_animation.setEndValue(new_pos)
        self.transition_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Clean up after animation
        def cleanup():
            if self.transition_animation is not None:
                self.transition_animation.deleteLater()
                self.transition_animation = None

        self.transition_animation.finished.connect(cleanup)
        self.transition_animation.start()

    def _display_current_media(self):
        """Display the current media item."""
        if not self.media_list or self.current_index >= len(self.media_list):
            return

        current_media = self.media_list[self.current_index]

        # Stop any existing timer
        self.advance_timer.stop()

        # Capture the current widget before switching (for slide transition)
        old_widget = self.stacked_widget.currentWidget()

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

            # Load video first (before showing)
            try:
                success = self.video_player.load_video(
                    current_media.file_path, current_media
                )
                if not success:
                    print(f"Failed to load video: {current_media.file_path}")
            except Exception as e:
                print(f"Error loading video: {e}")

            # Switch to video player
            self.stacked_widget.setCurrentWidget(self.video_player)

            # Apply transition effect if enabled
            if self.is_running:
                if self.transition_effect == "Fade":
                    self._apply_fade_transition(self.video_player)
                elif self.transition_effect == "Blur":
                    self._apply_blur_transition(self.video_player)
                elif self.transition_effect == "Zoom":
                    self._apply_zoom_transition(self.video_player)

            # Start playback after showing
            if self.is_running:
                try:
                    self.video_player.play()
                except Exception:
                    pass

            # No auto-advance for videos
        else:
            # If there's a video player, clean it up when switching to image
            if self.video_player is not None:
                self._cleanup_video_player()

            # Load image first (before showing)
            try:
                self.image_viewer.load_image(str(current_media.file_path))
            except Exception as e:
                print(f"Error loading image: {e}")

            # Switch to image viewer
            self.stacked_widget.setCurrentWidget(self.image_viewer)

            # Apply transition effect if enabled
            if self.is_running:
                if self.transition_effect == "Fade":
                    self._apply_fade_transition(self.image_viewer)
                elif self.transition_effect == "Blur":
                    self._apply_blur_transition(self.image_viewer)
                elif self.transition_effect == "Zoom":
                    self._apply_zoom_transition(self.image_viewer)

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
            self.setup_panel.raise_()  # Ensure it's on top
            # Force aggressive layout recalculation
            layout = self.layout()
            if layout:
                layout.invalidate()
            self.setup_panel.updateGeometry()
            self.stacked_widget.updateGeometry()
            if layout:
                layout.activate()
            self.updateGeometry()
            QCoreApplication.processEvents()
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

        # Remove maximum height constraint from stacked widget to restore normal layout
        self.stacked_widget.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX

        # Force layout recalculation before closing to reset geometry
        layout = self.layout()
        if layout:
            layout.invalidate()
        self.setup_panel.updateGeometry()
        self.stacked_widget.updateGeometry()
        if layout:
            layout.activate()
        QCoreApplication.processEvents()

        # Emit closed signal
        self.closed.emit()

        super().closeEvent(event)
