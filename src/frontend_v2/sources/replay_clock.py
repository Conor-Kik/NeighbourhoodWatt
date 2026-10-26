"""replay_clock.py
Keep track of time within simulated replay. Supports play, pause, and seek."""

from datetime import UTC, datetime, time, timedelta
from math import ceil

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

STEP_DURATION = 5  # minutes
STEP = timedelta(minutes=STEP_DURATION)
DEFAULT_MAX_T = 24 * 60 // STEP_DURATION
PLAYBACK_SPEEDS = (1.0, 1.5, 2.0)
MIN_INTERVAL_MS = 250  # At most four replay updates per second.
# A clock nobody has given a day to replay yet sits here; consumers show
# nothing until start_time moves off it.
UNSET_START_TIME = datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC)
# The optimiser head sits here until OptimiserGateway reports a completed step.
# Step 0 is a real step, so consumers cannot use 0 to mean "not reported yet".
UNSET_T_OPTIMISER = None


class ReplayClock(QObject):
    """Keep track of time within simulated replay. Supports play, pause, and
    seek. Note: start_time variable is 1-initialised."""

    time_changed = pyqtSignal(datetime)
    optimiser_changed = pyqtSignal(object)
    playback_changed = pyqtSignal(bool)
    window_changed = pyqtSignal()
    speed_changed = pyqtSignal(float)

    def __init__(
        self,
        start_time: datetime | None = None,
        max_t: int = DEFAULT_MAX_T,
        is_live: bool = False,
        interval_ms: int = 500,
    ):
        super().__init__()
        if start_time is None:
            start_time = UNSET_START_TIME
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        self.start_time = start_time
        self.max_t = max_t
        self.is_live = is_live
        self.t = 0
        self.t_optimiser: int | None = UNSET_T_OPTIMISER
        self._speed = 1.0
        self._base_interval_ms = max(MIN_INTERVAL_MS, interval_ms)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._base_interval_ms)
        self._timer.timeout.connect(self._advance_step)

    @property
    def duration_minutes(self) -> int:
        """Return the length of the replay window in minutes."""
        return self.max_t * STEP_DURATION

    @property
    def end_time(self) -> datetime:
        """Return end time. Removed from static variables to allow for dynamic max_t."""
        return self._add_dt(self.start_time, self.max_t)

    @property
    def is_running(self) -> bool:
        """Return true if running, false otherwise."""
        return self._timer.isActive()

    @property
    def playback_speed(self) -> float:
        """The selected speed relative to the normal replay pace."""
        return self._speed

    def set_playback_speed(self, speed: float) -> None:
        """Change pace without seeking, skipping intervals or starting playback."""
        if speed not in PLAYBACK_SPEEDS:
            raise ValueError(f"Unsupported playback speed: {speed}")
        if speed == self._speed:
            return
        self._speed = speed
        self._timer.setInterval(
            max(MIN_INTERVAL_MS, round(self._base_interval_ms / speed))
        )
        self.speed_changed.emit(speed)

    def play(self) -> None:
        """Start advancing time."""
        if not self.is_running:
            self._timer.start()
            self.playback_changed.emit(True)

    def pause(self) -> None:
        """Stop advancing time."""
        if self.is_running:
            self._timer.stop()
            self.playback_changed.emit(False)

    def toggle(self) -> None:
        """Toggle play / pause state."""
        if self.is_running:
            self.pause()
        else:
            self.play()

    def seek(self, t: int) -> None:
        """Jumps to time at time-step t."""
        # Using t and max_t to determine if the seek is valid
        if not (0 <= t < self.max_t):
            raise ValueError(f"Cannot seek less than 0, or beyond max t: t = {t}")
        if self.t != t:
            self.t = t
            self.time_changed.emit(self.get_time())  # Add emit for seek time selection

    def set_t_optimiser(self, t: int) -> None:
        """Record the optimiser's completed step.
        Owned by OptimiserGateway; the clock never advances it."""
        if t is None or not (0 <= t <= self.max_t):
            raise ValueError(
                "Cannot record an optimiser step outside the replay:"
                f" t = {t}, max t = {self.max_t}"
            )
        if self.t_optimiser != t:
            self.t_optimiser = t
            self.optimiser_changed.emit(t)

    def step_of(self, timestamp: datetime) -> int:
        """Return the step index of a timestamp within this replay window.

        Raises:
            ValueError: the timestamp falls outside the replay window."""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        else:
            timestamp = timestamp.astimezone(UTC)

        if timestamp < self.start_time or timestamp > self.end_time:
            raise ValueError(
                "Timestamp falls outside the replay:"
                f" {timestamp}, replay runs {self.start_time} to {self.end_time}"
            )

        steps = (timestamp - self.start_time).total_seconds()
        return int(steps // (STEP_DURATION * 60))

    def get_time(self) -> datetime:
        """Get current time."""
        return self._add_dt(self.start_time, self.t)

    def set_window(self, start: datetime, end: datetime) -> None:
        """Reset playback and optimiser progress to a simulation's UTC window."""
        start = (
            start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        )
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
        steps, remainder = divmod(end - start, timedelta(minutes=STEP_DURATION))
        if steps <= 0 or remainder:
            raise ValueError(
                "Replay window must be positive and a multiple of five minutes"
            )

        self.pause()
        self.start_time = start
        self.max_t = steps
        self.t = 0
        self.set_t_optimiser(0)
        self.window_changed.emit()
        self.time_changed.emit(self.get_time())

    def set_day(self, date: datetime) -> None:
        if date.time() != time():
            raise ValueError(
                f"Expected start of day at 00:00:00, instead got: {date.time()}"
            )
        self.t = 0
        self.start_time = date.replace(tzinfo=UTC) if date.tzinfo is None else date
        self.max_t = DEFAULT_MAX_T
        self.window_changed.emit()
        self.time_changed.emit(self.get_time())  # Add emit for set day selection

    def set_run(self, start_time_utc: str, end_time_utc: str) -> None:
        """Replay one saved run, from its own start to its own end.

        The run decides the window, so a run longer than a day replays in full
        instead of being cut off at DEFAULT_MAX_T. max_t rounds up, so a run that
        is not a whole number of steps long still reaches its end time.

        Raises:
            ValueError: the run ends before it starts, or its end is not a whole
            number of steps after its start."""
        start_time = datetime.fromisoformat(start_time_utc)
        end_time = datetime.fromisoformat(end_time_utc)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)
        length = end_time - start_time
        if length <= timedelta(0):
            raise ValueError(
                "Run must end after it starts:"
                f" start = {start_time_utc}, end = {end_time_utc}"
            )
        if length % STEP:
            raise ValueError(
                f"Run must last whole steps of {STEP_DURATION} minutes:"
                f" start = {start_time_utc}, end = {end_time_utc}"
            )

        self.pause()
        self.start_time = start_time
        self.max_t = ceil(length / STEP)
        self.t = 0
        self.t_optimiser = UNSET_T_OPTIMISER
        self.window_changed.emit()
        self.time_changed.emit(self.get_time())
        self.optimiser_changed.emit(self.t_optimiser)

    def reset(self) -> None:
        """Give the clock nobody's day, back at its first step.

        Used when the app switches to another run: the replay the clock was
        holding belongs to the run being replaced. Emits both faces the clock
        publishes, so a listener redraws from this one call."""
        self.pause()
        self.t = 0
        self.start_time = UNSET_START_TIME
        self.max_t = DEFAULT_MAX_T
        self.t_optimiser = UNSET_T_OPTIMISER
        self.window_changed.emit()
        self.time_changed.emit(self.get_time())
        self.optimiser_changed.emit(self.t_optimiser)

    def _advance_step(self) -> None:
        """Emits signal and updates t per step."""
        if self.t + 1 >= self.max_t:
            self.pause()
            return

        self.t += 1
        self.time_changed.emit(self.get_time())

    @staticmethod
    def _add_dt(dt: datetime, delta: int) -> datetime:
        return dt + timedelta(minutes=delta * STEP_DURATION)
