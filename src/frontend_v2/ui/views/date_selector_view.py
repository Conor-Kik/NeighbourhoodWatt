"""date_selector_view.py
Panel that enters a duration as a number plus a unit (minutes, hours, days,
years, or all time) and saves it as the chart-duration setting. Draws a
ChoicesPayload; the current pick sets the field and unit. "All time" saves the
whole run and locks the text field."""

from math import isfinite

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.bindings.selectors.common import (
    TIME_DURATIONS,
    WHOLE_RUN_DURATION,
    custom_duration_key,
    duration_entry,
)
from frontend_v2.ui.views.form_view import FORM_MAX_WIDTH
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import ChoicesPayload

UNIT_LABELS = ("Minutes", "Hours", "Days", "Years", "All time")
MINUTES_PER = {
    "Minutes": 1,
    "Hours": 60,
    "Days": 60 * 24,
    "Years": 60 * 24 * 365,
}


def _span_minutes(key: str) -> float:
    """Minutes one duration key reaches back, or zero for the whole run."""
    span = TIME_DURATIONS[key].span
    return 0.0 if span is None else span.total_seconds() / 60


# Fixed duration keys offered by the selector, with their reach in minutes.
_KEY_SPANS = [
    (key, _span_minutes(key))
    for key in TIME_DURATIONS
    if TIME_DURATIONS[key].span is not None
]


class DateSelectorView(PanelView):
    """Enter a duration as a number and unit, saved as the chart duration
    setting.

    Submits {"key": setting_key, "value": <duration key>}, ready for an app
    setting setter. Values that match a fixed duration use its key; other
    values are saved as custom minutes. "All time" saves the whole-run key and
    locks the text field."""

    PAYLOAD_TYPE = ChoicesPayload

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        setting_key: str | None = None,
        min_width: int = 0,
        min_height: int = 0,
    ):
        self.setting_key = setting_key
        self._locked = False
        self._current: str | None = None
        self._shown_unit = UNIT_LABELS[0]

        self._field = QLineEdit()
        self._field.setAccessibleName("Duration value")
        self._field.setToolTip("Enter a duration; choose its unit from the list")
        self._field.editingFinished.connect(self._submit)

        self._unit = QComboBox()
        self._unit.addItems(list(UNIT_LABELS))
        self._unit.setAccessibleName("Duration unit")
        self._unit.currentTextChanged.connect(self._unit_changed)

        self._apply = QPushButton("Apply")
        self._apply.setToolTip("Save this duration as the chart window")
        self._apply.clicked.connect(self._submit)

        form = QWidget()
        form.setMaximumWidth(FORM_MAX_WIDTH)
        form.setObjectName("form")
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Value"))
        row.addWidget(self._field, stretch=1)
        row.addWidget(QLabel("Unit"))
        row.addWidget(self._unit)
        layout.addLayout(row)
        layout.addWidget(self._apply)

        super().__init__(
            form,
            title,
            help_text,
            empty_text,
            min_width=min_width,
            min_height=min_height,
        )

    def _unit_changed(self, unit: str) -> None:
        """All time saves immediately and locks the field; anything else
        unlocks it, converting the number on screen into the new unit."""
        if unit == "All time":
            self._locked = True
            self._shown_unit = unit
            self._field.clear()
            self._field.setEnabled(False)
            self._apply.setEnabled(True)
            self.submitted.emit({"key": self.setting_key, "value": WHOLE_RUN_DURATION})
            return
        previous = self._shown_unit
        self._shown_unit = unit
        self._locked = False
        self._field.setEnabled(True)
        self._apply.setEnabled(True)
        try:
            value = float(self._field.text().strip())
        except ValueError:
            self._refresh_field()
            return
        if previous in MINUTES_PER and isfinite(value) and value > 0:
            value = value * MINUTES_PER[previous] / MINUTES_PER[unit]
            self._field.setText(self._format_value(value))
        else:
            self._refresh_field()

    def _refresh_field(self) -> None:
        """Mirror the current duration into the field unless locked."""
        if self._locked or self._current is None:
            return
        self._field.setText(self._duration_text())

    def _duration_text(self) -> str:
        """Current duration value expressed in the chosen unit."""
        minutes = self._current_minutes()
        unit = self._unit.currentText()
        if minutes <= 0 or unit not in MINUTES_PER:
            return ""
        return self._format_value(minutes / MINUTES_PER[unit])

    @staticmethod
    def _format_value(value: float) -> str:
        """Format a duration value without a needless decimal part."""
        if value == int(value):
            value = int(value)
        return f"{value:.12g}"

    @staticmethod
    def _display_unit(minutes: float) -> tuple[str, float]:
        """The largest unit that shows minutes without rounding the value."""
        for unit in ("Years", "Days", "Hours"):
            per = MINUTES_PER[unit]
            value = round(minutes / per, 6)
            if minutes >= per and abs(value * per - minutes) < 1e-9:
                return unit, value
        return "Minutes", minutes

    def _current_minutes(self) -> float:
        """Minutes represented by the current payload pick, or zero."""
        if self._current is None:
            return 0.0
        entry = duration_entry(self._current)
        if entry is None or entry.span is None:
            return 0.0
        return entry.span.total_seconds() / 60

    def _draw(self, payload: ChoicesPayload) -> None:
        """Refill the field and unit from the saved duration pick."""
        self._current = payload.current
        entry = None if self._current is None else duration_entry(self._current)
        if entry is None:
            self._locked = False
            self._shown_unit = self._unit.currentText()
            self._field.clear()
            self._field.setEnabled(True)
            self._apply.setEnabled(True)
            return
        if entry.span is None:
            with QSignalBlocker(self._unit):
                self._unit.setCurrentText("All time")
            self._shown_unit = "All time"
            self._locked = True
            self._field.clear()
            self._field.setEnabled(False)
            self._apply.setEnabled(True)
            return
        minutes = entry.span.total_seconds() / 60
        # Show the largest unit that represents the duration exactly.
        unit, value = self._display_unit(minutes)
        with QSignalBlocker(self._unit):
            self._unit.setCurrentText(unit)
        self._shown_unit = unit
        self._locked = False
        self._field.setEnabled(True)
        self._apply.setEnabled(True)
        self._field.setText(self._format_value(value))

    def _submit(self) -> None:
        """Save the entered duration as the chart-duration setting."""
        unit = self._unit.currentText()
        if unit == "All time":
            self.submitted.emit({"key": self.setting_key, "value": WHOLE_RUN_DURATION})
            return
        if self._locked:
            return
        text = self._field.text().strip()
        if not text:
            return
        try:
            value = float(text)
        except ValueError:
            return
        if not isfinite(value) or value <= 0:
            return
        if unit not in MINUTES_PER:
            return
        minutes = value * MINUTES_PER[unit]
        key = self._duration_key(minutes)
        if duration_entry(key) is None:
            return
        self.submitted.emit({"key": self.setting_key, "value": key})

    @staticmethod
    def _duration_key(minutes: float) -> str:
        """A matching fixed key, or else the custom length stored in minutes."""
        for key, span in _KEY_SPANS:
            if abs(minutes - span) < 1e-9:
                return key
        return custom_duration_key(minutes)
