"""Read-only configuration features and modelled battery wear information."""

from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from frontend_v2.ui.views.form_view import FormField


class BatteryWearRate(QWidget):
    """Display a supplied rate with an optional explanation of its units."""

    def __init__(self):
        super().__init__()
        self.rate = QLabel()
        self.rate.setTextFormat(Qt.TextFormat.PlainText)
        self.rate.setWordWrap(True)
        self.explanation = QLabel(
            "Replacement cost ÷ (capacity × cycle life × 2). "
            "Throughput counts energy charged into and discharged from the "
            "battery."
        )
        self.explanation.setWordWrap(True)
        self.explanation.hide()
        self.explain = QToolButton()
        self.explain.setObjectName("configDisclosure")
        self.explain.setText("How this is calculated")
        self.explain.setCheckable(True)
        self.explain.toggled.connect(self.explanation.setVisible)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.rate)
        layout.addWidget(self.explain, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.explanation)
        self.set_rate(None)

    def set_rate(self, rate: float | None) -> None:
        """Show cents per kWh without rounding small positive rates to zero."""
        if rate is None:
            self.rate.setText(
                "Estimated battery wear rate: unavailable. "
                "Enter a positive capacity and cycle life, and a "
                "valid replacement cost."
            )
        else:
            self.rate.setText(
                f"Estimated battery wear rate: {rate * 100:.3g} c/kWh throughput"
            )


class ConfigSummary(QWidget):
    """Show essential features beside a choice, with secondary fields on demand."""

    def __init__(
        self,
        fields: list[FormField],
        primary_keys: set[str],
        *,
        wear_rate: bool = False,
    ):
        super().__init__()
        # Expanded facts must grow the surrounding form instead of being squeezed.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.fields = fields
        self.primary_keys = primary_keys
        self.summary = QLabel()
        self.details = QLabel()
        for label in (self.summary, self.details):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.details.hide()
        self.more = QToolButton()
        self.more.setObjectName("configDisclosure")
        self.more.setText("More details")
        self.more.setCheckable(True)
        self.more.toggled.connect(self._show_details)
        self.wear = BatteryWearRate() if wear_rate else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.summary)
        if self.wear is not None:
            layout.addWidget(self.wear)
        layout.addWidget(self.more, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.details)
        self.set_record(None)

    def _show_details(self, expanded: bool) -> None:
        self.details.setVisible(expanded)
        self.more.setText("Fewer details" if expanded else "More details")

    def set_record(self, record: dict | None) -> None:
        """Replace displayed facts; this widget never fetches or edits records."""
        if record is None:
            self.summary.setText("Choose a configuration to see its features.")
            self.more.hide()
            self.details.hide()
            if self.wear is not None:
                self.wear.hide()
            return
        primary, secondary = [], []
        for field in self.fields:
            value = record.get(field.key)
            if value is None:
                shown = "Not available"
            elif field.kind is bool:
                shown = "Yes" if value else "No"
            else:
                shown = f"{value * field.scale:,.{field.decimals}f}"
                if field.kind is int:
                    shown = f"{int(value * field.scale):,}"
                if field.unit:
                    shown += f" {field.unit}"
            target = primary if field.key in self.primary_keys else secondary
            target.append(f"{field.label}: {shown}")
        # Wrap between facts, keeping labels, values and units together.
        self.summary.setText(
            " · ".join(
                f'<span style="white-space: nowrap">{escape(fact)}</span>'
                for fact in primary
            )
        )
        self.details.setText("\n".join(secondary))
        self.more.setVisible(bool(secondary))
        self.details.setVisible(bool(secondary) and self.more.isChecked())
        if self.wear is not None:
            self.wear.set_rate(record.get("degradation_aud_per_kwh"))
            self.wear.show()
