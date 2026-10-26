"""replay_bar.py
Panel showing where the replay has got to, how long it runs for, and how far
the optimiser has stepped. The user drives the clock from here."""

from datetime import datetime

from PyQt6.QtCore import QSignalBlocker, QSize, Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QWidget,
)

from frontend_v2.sources.replay_clock import (
    PLAYBACK_SPEEDS,
    STEP_DURATION,
    UNSET_START_TIME,
    ReplayClock,
)
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import TabularPayload

# Headers the replay selector puts in its payload, in the order the bar reads
# them back
TIME_HEADER = "Replay time"
DURATION_HEADER = "Replay length"
OPTIMISER_HEADER = "Optimiser step"
PAYLOAD_HEADERS = (TIME_HEADER, DURATION_HEADER, OPTIMISER_HEADER)

TIME_FORMAT = "%Y-%m-%d %H:%M %Z"
OPTIMISER_PREFIX = "Optimiser at step "
DURATION_PREFIX = " Total time: "
TITLE = "Replay"
EMPTY_TEXT = "No replay running."
HELP_TEXT = (
    "Play/Pause controls viewing of the saved run. Dragging the timeline pauses "
    "playback; release it to seek and resume if it was playing. Arrow keys move "
    "one five-minute interval. Speed changes the viewing pace up to 2×. "
    "These controls do not rerun the simulation."
)
ICON_SIZE = 20

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 60 * 24
MINUTES_PER_YEAR = 60 * 24 * 365


class ReplayBarView(PanelView):
    """Show the replay's clock face: the time it has got to, the length of the
    replay, and how far the optimiser has stepped. Play, pause and seek move the
    clock it is given.

    Requirements:
        Given the ReplayClock it replays; without one it has nothing to show.
        Shows nothing until the clock has been given a day to replay.
        Hides the replay length once the optimiser head is known."""

    PAYLOAD_TYPE = TabularPayload

    def __init__(self, clock: ReplayClock | None = None):
        if clock is None:
            raise ValueError("ReplayBarView needs a ReplayClock")
        self._clock = clock
        self._time: datetime | None = None
        self._duration: int = 0
        self._optimiser_t: int | None = None

        self._time_label = QLabel()
        self._head_label = QLabel()
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.setObjectName("replayTimeline")
        self._scrubber.setAccessibleName("Replay timeline")
        self._scrubber.setToolTip("Drag to seek; arrow keys move five minutes")
        self._scrubber.setTracking(False)
        self._scrubber.setSingleStep(1)
        self._scrubber.setPageStep(12)
        self._scrubber.valueChanged.connect(self._seek_from_control)
        self._scrubber.sliderPressed.connect(self._begin_scrub)
        self._scrubber.sliderMoved.connect(self._show_position)
        self._scrubber.sliderReleased.connect(self._end_scrub)
        self._resume_after_scrub = False
        self._position_label = QLabel()
        self._position_label.setAccessibleName("Elapsed and total simulation time")
        self._restart_button = QPushButton()
        self._restart_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._restart_button.setAccessibleName("Skip to start")
        self._restart_button.setToolTip("Return to the start of this replay")
        self._restart_button.clicked.connect(lambda: self.seek(0))
        self._play_button = QPushButton()
        self._play_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._play_button.setAccessibleName("Play replay")
        self._play_button.setToolTip("Start or pause the replay")
        self._play_button.clicked.connect(self._toggle_playback)
        self._skip_end_button = QPushButton()
        self._skip_end_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._skip_end_button.setAccessibleName("Skip to end")
        self._skip_end_button.setToolTip("Skip to the end of this replay")
        self._skip_end_button.clicked.connect(lambda: self.seek(self._clock.max_t - 1))
        self._speed_picker = QComboBox()
        self._speed_picker.setAccessibleName("Playback speed")
        self._speed_picker.setToolTip(
            "1× is the normal viewing pace; 1.5× and 2× advance the saved replay "
            "faster. Changing speed does not start paused playback."
        )
        for speed in PLAYBACK_SPEEDS:
            self._speed_picker.addItem(f"{speed:g}×", speed)
        self._speed_picker.activated.connect(
            lambda _: self._clock.set_playback_speed(self._speed_picker.currentData())
        )
        self._speed_control = QWidget()
        speed_layout = QHBoxLayout(self._speed_control)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_label = QLabel("Speed")
        speed_label.setBuddy(self._speed_picker)
        speed_layout.addWidget(speed_label)
        speed_layout.addWidget(self._speed_picker)
        self._time_label.setWordWrap(True)
        self._head_label.setWordWrap(True)
        content = QWidget()
        content.setObjectName("form")
        self._controls = QGridLayout(content)
        self._controls.setContentsMargins(0, 0, 0, 0)
        self._controls.addWidget(self._time_label, 0, 0, 1, 3)
        self._controls.addWidget(self._head_label, 0, 3, 1, 2)
        self._controls.addWidget(self._restart_button, 1, 0)
        self._controls.addWidget(self._play_button, 1, 1)
        self._controls.addWidget(self._skip_end_button, 1, 2)
        self._controls.addWidget(self._speed_control, 1, 3)
        self._controls.addWidget(self._scrubber, 1, 4)
        self._controls.addWidget(self._position_label, 1, 5)
        self._controls.setColumnStretch(4, 1)
        self._compact: bool | None = None
        super().__init__(content, TITLE, HELP_TEXT, EMPTY_TEXT)
        self._restart_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward)
        )
        self._skip_end_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward)
        )
        self._sync_play_button()
        self._arrange_controls()
        self._clock.playback_changed.connect(self._sync_play_button)
        self._clock.speed_changed.connect(self._sync_speed)
        self._clock.window_changed.connect(self._cancel_scrub)
        self._read_clock()
        self._configure_scrubber()
        self._sync_speed()

    def _arrange_controls(self) -> None:
        """Give the timeline its own row when controls become too crowded."""
        compact = self.width() < 800
        if compact == self._compact:
            return
        self._compact = compact
        self._controls.removeWidget(self._position_label)
        self._controls.removeWidget(self._scrubber)
        if compact:
            self._controls.addWidget(self._scrubber, 2, 0, 1, 5)
            self._controls.addWidget(self._position_label, 2, 5)
        else:
            self._controls.addWidget(self._scrubber, 1, 4)
            self._controls.addWidget(self._position_label, 1, 5)

    def _sync_speed(self, _speed: float = 1.0) -> None:
        """Follow the clock without turning a redraw into another speed change."""
        with QSignalBlocker(self._speed_picker):
            self._speed_picker.setCurrentIndex(
                self._speed_picker.findData(self._clock.playback_speed)
            )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._arrange_controls()

    def _sync_play_button(self, _running: bool = False) -> None:
        """Follow manual playback changes and the clock stopping at the end."""
        running = self._clock.is_running
        icon = (
            QStyle.StandardPixmap.SP_MediaPause
            if running
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self._play_button.setIcon(self.style().standardIcon(icon))
        self._play_button.setAccessibleName(f"{'Pause' if running else 'Play'} replay")

    def _toggle_playback(self) -> None:
        if self._clock.is_running:
            self.pause()
        else:
            self.play()

    def _begin_scrub(self) -> None:
        """Pause playback while the user previews a position on the timeline."""
        self._resume_after_scrub = self._clock.is_running
        self.pause()

    def _end_scrub(self) -> None:
        """Seek once on release and resume only if playback was running."""
        if self._time is None:
            return
        self.seek(self._scrubber.sliderPosition())
        resume = self._resume_after_scrub
        self._resume_after_scrub = False
        if resume:
            self._clock.play()

    def _cancel_scrub(self) -> None:
        """Discard a gesture when the clock is reset, even for identical dates."""
        self._resume_after_scrub = False
        with QSignalBlocker(self._scrubber):
            self._scrubber.setSliderDown(False)
        self._read_clock()
        self._configure_scrubber()
        self._show_lines()

    def _seek_from_control(self, position: int) -> None:
        if not self._scrubber.isSliderDown() and position != self._clock.t:
            self.seek(position)

    def _show_position(self, position: int) -> None:
        """Show elapsed simulation hours/minutes without fetching while dragging."""
        elapsed = position * STEP_DURATION
        hours, minutes = divmod(elapsed, 60)
        total_hours, total_minutes = divmod(self._duration, 60)
        self._position_label.setText(
            f"{hours:02}:{minutes:02} / {total_hours:02}:{total_minutes:02}"
        )
        self._position_label.setToolTip(
            "Elapsed simulation time / run length (hours:minutes). "
            "Each timeline position starts a five-minute interval."
        )

    def _duration_line(self) -> str:
        """Show the replay length in the largest unit that fits it."""
        minutes = self._duration
        if minutes >= MINUTES_PER_YEAR:
            value = minutes / MINUTES_PER_YEAR
            unit = "year"
        elif minutes >= MINUTES_PER_DAY:
            value = minutes / MINUTES_PER_DAY
            unit = "day"
        elif minutes >= MINUTES_PER_HOUR:
            value = minutes / MINUTES_PER_HOUR
            unit = "hour"
        else:
            value = minutes
            unit = "minute"
        if value == int(value):
            value = int(value)
        plural = "" if value == 1 else "s"
        return f"{DURATION_PREFIX}{value:g} {unit}{plural}"

    def _read_clock(self) -> None:
        """Snapshot the clock the bar replays."""
        self._duration = self._clock.duration_minutes
        self._optimiser_t = self._clock.t_optimiser
        if self._clock.start_time == UNSET_START_TIME:
            self._time = None
        else:
            self._time = self._clock.get_time()

    def _read_payload(self, payload: TabularPayload) -> None:
        """Snapshot the replay fields DataManager published for this replay.

        Raises:
            ValueError: the payload carries no row to read."""
        if not payload.rows:
            raise ValueError(
                f"ReplayBarView got a payload with no row to read: {payload.rows}"
            )
        self._time, self._duration, self._optimiser_t = payload.rows[-1]

    def _lines(self) -> list[str]:
        """Compose the lines the bar shows, in display order.

        Returns:
            [] when the bar has nothing to show; otherwise the clock time, then
            the optimiser head, or the replay length while the head is unknown."""
        if self._time is None:
            return []

        lines = [self._time.strftime(TIME_FORMAT)]
        if self._optimiser_t is not None:
            lines.append(f"{OPTIMISER_PREFIX}{self._optimiser_t}")
        else:
            lines.append(self._duration_line())
        return lines

    def _show_lines(self) -> None:
        """Show the replay time and head, or the empty state when there is no
        replay to show."""
        lines = self._lines()
        if not lines:
            self.show_empty()
            return
        self._time_label.setText(lines[0])
        self._head_label.setText(lines[1])

    def _configure_scrubber(self) -> None:
        """Follow playback without turning a redraw into a user seek."""
        last_step = self._duration // STEP_DURATION - 1
        with QSignalBlocker(self._scrubber):
            self._scrubber.setRange(0, max(last_step, 0))
            if not self._scrubber.isSliderDown():
                self._scrubber.setValue(self._clock.t)
        self._show_position(self._scrubber.sliderPosition())
        for control in (
            self._scrubber,
            self._play_button,
            self._restart_button,
            self._skip_end_button,
            self._speed_picker,
        ):
            control.setEnabled(self._time is not None)
        self._sync_play_button()

    def _draw(self, payload: TabularPayload) -> None:
        """Draw the replay fields DataManager published for this replay."""
        self._read_payload(payload)
        self._configure_scrubber()
        self._show_lines()

    def text(self) -> str:
        """Return the text the bar shows, or empty_text when it shows nothing."""
        lines = self._lines()
        if not lines:
            return self.empty_text
        return "\n".join(lines)

    def seek(self, t: int) -> None:
        """Move the replay to step t, then show the clock's new face.

        Only the optimiser publishes steps the clock has not replayed itself, so
        the view re-reads the clock on each of its own moves."""
        self._clock.seek(t)
        self._read_clock()
        self._configure_scrubber()
        self._show_lines()

    def play(self) -> None:
        """Start advancing the replay."""
        if self._clock.t == self._clock.max_t - 1:
            self.seek(0)
        self._clock.play()

    def pause(self) -> None:
        """Stop advancing the replay."""
        self._clock.pause()
