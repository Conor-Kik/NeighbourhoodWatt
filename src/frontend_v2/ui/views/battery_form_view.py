"""Existing battery editor with a live, read-only modelled wear rate."""

from PyQt6.QtCore import pyqtSignal

from frontend_v2.ui.form_fields import BATTERY_CONFIG_FIELDS
from frontend_v2.ui.views.config_summary import BatteryWearRate
from frontend_v2.ui.views.form_view import FormView


class BatteryFormView(FormView):
    """Keep the shared form's persistence/validation and derive display-only wear."""

    values_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__(
            title="Battery configs",
            help_text="Capacity in kWh is stored energy; charge/discharge power in kW "
            "limits how quickly it moves. Minimum and maximum SOC bound "
            "usable storage. Efficiency applies separately to charging and "
            "discharging. Replacement cost, capacity and cycle life "
            "determine the modelled wear rate below. The daily cycle limit "
            "applies over a rolling 24 hours. Saved configs are read-only; "
            "choose Create new to copy the values, enter a unique name and "
            "save.",
            empty_text="No configs saved yet.",
            id_key="battery_config_id",
            id_label="Battery config",
            fields=BATTERY_CONFIG_FIELDS,
            existing_read_only=True,
            scrollable=False,
        )
        self.wear = BatteryWearRate()
        self._form_layout.insertRow(self._form_layout.rowCount() - 1, self.wear)
        for widget in self._inputs.values():
            widget.valueChanged.connect(self._notify_values)

    def _notify_values(self) -> None:
        record: dict = {"battery_config_id": self._record_id()}
        record.update({field.key: self._value(field) for field in self.fields})
        self.values_changed.emit(record)

    def show_wear_rate(self, rate: float | None) -> None:
        self.wear.set_rate(rate)
