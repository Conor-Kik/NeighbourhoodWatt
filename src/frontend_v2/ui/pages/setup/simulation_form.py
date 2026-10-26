"""simulation_form.py
Form that sets up and runs a simulation, and makes the finished run the one the
other tabs show."""

from calendar import month_name, monthrange
from datetime import UTC, datetime, timedelta
from math import isnan
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.bindings.bindings import WriteBinding
from frontend_v2.bindings.selectors.common import (
    current_request_id,
    current_request_rows,
)
from frontend_v2.bindings.selectors.records import (
    select_battery_config_records,
    select_house_config_records,
)
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.engine.optimiser_gateway import OptimiserGateway
from frontend_v2.ui.form_fields import BATTERY_CONFIG_FIELDS, HOUSE_CONFIG_FIELDS
from frontend_v2.ui.views.config_summary import ConfigSummary
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status


class SimulationForm(QWidget):
    def __init__(self, data_manager: DataManager, optimiser_gateway: OptimiserGateway):
        super().__init__()
        self.data_manager = data_manager
        self.optimiser_gateway = optimiser_gateway
        self._house_labels: dict[str, str] = {}
        self._house_records: dict[str, dict] = {}
        self._battery_records: dict[str, dict] = {}
        self.house_rows = []
        self._loaded_request_id: str | None = None

        # Picks a finished run as the one the other tabs show
        self.select_run = WriteBinding(data_manager, "set_current_request")
        self.select_run.result.connect(self.on_select_run_result)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        # Create form (objective, dates, location, battery)
        self.setMaximumWidth(720)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        # Objective
        self.objective_combo = QComboBox()
        self.objective_combo.addItems(["Cost", "Emissions", "Balanced"])
        form.addRow("Objective Mode", self.objective_combo)

        # Dates
        self.selected_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

        self.date_day_combo = QComboBox()
        self.date_month_combo = QComboBox()
        self.date_year_combo = QComboBox()

        for month in range(1, 13):
            self.date_month_combo.addItem(month_name[month], month)
        for year in range(2026, 2023, -1):
            self.date_year_combo.addItem(str(year), year)

        self.date_day_combo.currentIndexChanged.connect(self.on_date_component_changed)
        self.date_month_combo.currentIndexChanged.connect(
            self.on_date_component_changed
        )
        self.date_year_combo.currentIndexChanged.connect(self.on_date_component_changed)

        start_date_layout = QHBoxLayout()
        start_date_layout.addWidget(self.date_day_combo)
        start_date_layout.addWidget(self.date_month_combo)
        start_date_layout.addWidget(self.date_year_combo)
        form.addRow("Start Date", start_date_layout)

        # Duration
        self.duration_spin = QSpinBox()
        self.duration_spin.setMinimum(1)
        self.duration_spin.setMaximum(28)
        self.duration_spin.setValue(1)

        self.duration_help = self._create_tool_tip(
            "Duration", "Simulations are limited to 28 days."
        )

        self.duration_row = QHBoxLayout()
        self.duration_row.addWidget(self.duration_help)
        self.duration_row.addWidget(self.duration_spin)
        form.addRow("Duration (Days)", self.duration_row)

        # Initialise days
        self.sync_date_controls()

        # Location
        self.location_combo = QComboBox()
        form.addRow("Location", self.location_combo)

        # Battery
        self.battery_combo = QComboBox()
        form.addRow("Community Battery", self.battery_combo)
        self.battery_summary = ConfigSummary(
            BATTERY_CONFIG_FIELDS,
            {
                "capacity_kwh",
                "max_power_kw",
                "efficiency",
                "min_soc_frac",
                "max_soc_frac",
            },
            wear_rate=True,
        )
        form.addRow(self.battery_summary)
        self.battery_combo.currentIndexChanged.connect(self._update_battery_summary)

        # Baseline tariffs
        self.tariff_combo = QComboBox()
        self.tariff_combo.addItem("Wholesale (AEMO market prices)", "wholesale")
        self.tariff_combo.addItem("Fixed (flat buy and sell rates)", "fixed")
        self.tariff_combo.currentIndexChanged.connect(self.on_tariff_changed)

        self.tariff_help = self._create_tool_tip(
            "Baseline Tariffs",
            "Applies only to no-battery baseline comparisons. All battery methods "
            "use AEMO market prices with modelled network tariffs. Wholesale "
            "baselines use those changing tariffs; Fixed baselines use the buy "
            "and sell rates entered below for the whole run, in AUD/kWh. Network "
            "tariffs use representative capital-city profiles. Custom locations "
            "use their NEM region's capital-city profile, rather than a "
            "site-specific quote.",
        )

        self.tariff_row = QHBoxLayout()
        self.tariff_row.addWidget(self.tariff_help)
        self.tariff_row.addWidget(self.tariff_combo)
        form.addRow("Baseline Tariff", self.tariff_row)

        self.fixed_buy_edit = QLineEdit()
        self.fixed_buy_edit.setPlaceholderText("e.g. 0.30")
        self.fixed_buy_edit.setValidator(QDoubleValidator())
        form.addRow("Buy Price (AUD/kWh)", self.fixed_buy_edit)

        self.fixed_sell_edit = QLineEdit()
        self.fixed_sell_edit.setPlaceholderText("e.g. 0.02")
        self.fixed_sell_edit.setValidator(QDoubleValidator())
        form.addRow("Sell Price (AUD/kWh)", self.fixed_sell_edit)

        # Rows shown only for the fixed tariff
        self.form = form
        self.fixed_rows = [self.fixed_buy_edit, self.fixed_sell_edit]
        self.on_tariff_changed()

        outer.addLayout(form)

        # Create house inputs (configs, quantities)
        outer.addWidget(QLabel("Houses"))
        self.house_rows_layout = QVBoxLayout()
        outer.addLayout(self.house_rows_layout)

        add_btn = QPushButton("+ Add House Type")
        add_btn.clicked.connect(self.add_house_row)
        outer.addWidget(add_btn)
        self.add_house_row()

        # Create run button and status label
        self.status_label = QLabel("")
        self.is_running = False
        self.run_btn = QPushButton("Run Simulation")
        self.run_btn.clicked.connect(self.on_run_btn_pressed)
        outer.addWidget(self.status_label)
        outer.addWidget(self.run_btn)

        outer.addStretch()

        # Connect to data manager and optimiser gateway
        self.data_manager.updated_keys.connect(self.on_data_updated)
        self.optimiser_gateway.simulation_progress.connect(self.status_label.setText)
        self.optimiser_gateway.simulation_cancelled.connect(self.on_cancelled)
        self.optimiser_gateway.simulation_finished.connect(self.on_finished)
        self.optimiser_gateway.simulation_error.connect(self.on_error)

        self.update_duration_max()

    def _create_tool_tip(self, help_title: str, help_text: str) -> QToolButton:
        """Returns a QToolButton that will show the provided help text on click."""

        tool_tip = QToolButton()
        tool_tip.setObjectName("panelHelp")
        tool_tip.setCursor(Qt.CursorShape.PointingHandCursor)
        tool_tip.setText("?")
        tool_tip.clicked.connect(
            lambda: QMessageBox.information(self, help_title, help_text)
        )

        return tool_tip

    def get_max_end_date(self) -> datetime:
        """Max allowed end date is 2 months before current time."""
        return datetime.now(UTC) - timedelta(days=62)

    def update_duration_max(self) -> None:
        """Update duration spinbox max so end date is within the max end date."""
        max_end = self.get_max_end_date()
        days_available = (max_end - self.selected_date).days + 1
        max_duration = max(1, min(28, days_available))
        self.duration_spin.setMaximum(max_duration)
        if self.duration_spin.value() > max_duration:
            self.duration_spin.setValue(max_duration)

    def sync_date_controls(self) -> None:
        """Update the date combos based on the selected date and the max end date."""

        for combo in (self.date_day_combo, self.date_month_combo, self.date_year_combo):
            combo.blockSignals(True)

        # Bound the selected date
        max_end = self.get_max_end_date()
        if self.selected_date > max_end:
            self.selected_date = max_end

        # Get days from selected year and month
        days_in_month = monthrange(self.selected_date.year, self.selected_date.month)[1]

        # Update day combo if the number of days changed
        if self.date_day_combo.count() != days_in_month:
            self.date_day_combo.clear()
            for day in range(1, days_in_month + 1):
                self.date_day_combo.addItem(str(day), day)

        # Update day and month
        self.date_day_combo.setCurrentIndex(self.selected_date.day - 1)
        self.date_month_combo.setCurrentIndex(self.selected_date.month - 1)

        # Update year
        year_index = self.date_year_combo.findData(self.selected_date.year)
        if year_index >= 0:
            self.date_year_combo.setCurrentIndex(year_index)

        for combo in (self.date_day_combo, self.date_month_combo, self.date_year_combo):
            combo.blockSignals(False)

        self.update_duration_max()

    def on_date_component_changed(self) -> None:
        """Update the stored selected date. Call when the date combos are updated."""

        # Read requested date
        year = self.date_year_combo.currentData()
        month = self.date_month_combo.currentData()
        day = self.date_day_combo.currentData()

        if not all(isinstance(value, int) for value in (year, month, day)):
            return

        # Set selected date
        days_in_month = monthrange(year, month)[1]
        valid_day = min(day, days_in_month)

        candidate_date = datetime(year, month, valid_day, 0, 0, 0, tzinfo=UTC)
        max_end = self.get_max_end_date()

        if candidate_date > max_end:
            candidate_date = max_end

        self.selected_date = candidate_date

        # If combos need updating, sync them
        if (
            candidate_date.year != year
            or candidate_date.month != month
            or candidate_date.day != valid_day
            or valid_day != day
            or self.date_day_combo.count() != days_in_month
        ):
            self.sync_date_controls()

        self.update_duration_max()

    def on_data_updated(self, updated: set[str]) -> None:
        """Update the form info when the data manager signals new data."""

        if "get_all_locations" in updated:
            records = self.data_manager.get("get_all_locations")
            if records is not None:
                new_locations = [str(row["location_name"]) for row in records]
                current_locations = [
                    self.location_combo.itemText(i)
                    for i in range(self.location_combo.count())
                ]
                if new_locations != current_locations:
                    current = self.location_combo.currentText()
                    self.location_combo.clear()
                    for row in records:
                        self.location_combo.addItem(
                            str(row["location_name"]), str(row["timezone"])
                        )
                    self.location_combo.setCurrentText(current)

        if "get_all_battery_configs" in updated:
            records = self.data_manager.get("get_all_battery_configs")
            if records is not None:
                payload = select_battery_config_records(
                    {"get_all_battery_configs": records}
                )
                self._battery_records = payload.records
                self._fill_config_choices(self.battery_combo, payload.labels)
                self._update_battery_summary()

        if "get_all_house_configs" in updated:
            records = self.data_manager.get("get_all_house_configs")
            if records is not None:
                payload = select_house_config_records(
                    {"get_all_house_configs": records}
                )
                self._house_records = payload.records
                self._house_labels = payload.labels
                for combo, _, row in self.house_rows:
                    self._fill_config_choices(combo, self._house_labels)
                    self._update_house_summary(row, combo.currentData())

        self._load_selected_run()

    def _load_selected_run(self) -> None:
        """Restore a newly selected run once its form data has arrived."""
        data = {
            key: self.data_manager.get(key)
            for key in (
                "get_current_request",
                "get_all_simulation_requests",
                "get_current_run_houses",
            )
        }
        if any(rows is None for rows in data.values()):
            return
        request_id = current_request_id(data)
        if request_id is None or request_id == self._loaded_request_id:
            return
        requests = current_request_rows(data, "get_all_simulation_requests")
        houses = current_request_rows(data, "get_current_run_houses")
        if not len(requests) or not len(houses):
            return
        request = requests[0]
        location_index = self.location_combo.findText(str(request["location_name"]))
        battery_index = self.battery_combo.findData(str(request["battery_config_id"]))
        if (
            location_index < 0
            or battery_index < 0
            or any(
                str(row["house_config_id"]) not in self._house_labels for row in houses
            )
        ):
            return

        self.location_combo.setCurrentIndex(location_index)
        self.battery_combo.setCurrentIndex(battery_index)
        self.objective_combo.setCurrentText(str(request["objective_mode"]))
        start = datetime.fromisoformat(str(request["start_time_utc"]))
        end = datetime.fromisoformat(str(request["end_time_utc"]))
        zone = ZoneInfo(self.location_combo.currentData())
        local_start = start.replace(tzinfo=start.tzinfo or UTC).astimezone(zone)
        self.selected_date = datetime(
            local_start.year, local_start.month, local_start.day, tzinfo=UTC
        )
        self.sync_date_controls()
        self.duration_spin.setValue((end - start).days)
        buy = float(request["fixed_buy_aud_per_kwh"])
        sell = float(request["fixed_sell_aud_per_kwh"])
        self.tariff_combo.setCurrentIndex(
            self.tariff_combo.findData(
                "fixed" if not isnan(buy) or not isnan(sell) else "wholesale"
            )
        )
        self.fixed_buy_edit.setText("" if isnan(buy) else str(buy))
        self.fixed_sell_edit.setText("" if isnan(sell) else str(sell))
        for _, _, widget in list(self.house_rows):
            self.remove_house_row(widget)
        for row in houses:
            self.add_house_row()
            combo, spin, _ = self.house_rows[-1]
            combo.setCurrentIndex(combo.findData(str(row["house_config_id"])))
            spin.setValue(int(row["quantity"]))
        self._loaded_request_id = request_id

    @staticmethod
    def _fill_config_choices(combo: QComboBox, labels: dict[str, str]) -> None:
        """Keep selections by saved ID while refreshing readable labels."""
        current = combo.currentData()
        with QSignalBlocker(combo):
            combo.clear()
            for config_id, label in labels.items():
                combo.addItem(label, config_id)
                combo.setItemData(
                    combo.count() - 1,
                    f"Saved configuration ID: {config_id}",
                    Qt.ItemDataRole.ToolTipRole,
                )
            if current in labels:
                combo.setCurrentIndex(combo.findData(current))

    def _update_battery_summary(self, _index: int = -1) -> None:
        self.battery_summary.set_record(
            self._battery_records.get(self.battery_combo.currentData())
        )

    def _update_house_summary(self, row: QWidget, config_id: str) -> None:
        row.findChild(ConfigSummary).set_record(self._house_records.get(config_id))

    def add_house_row(self):
        """Add a new row of house inputs to the form."""

        row_widget = QWidget()
        group = QVBoxLayout(row_widget)
        group.setContentsMargins(0, 0, 0, 0)
        row_layout = QHBoxLayout()
        group.addLayout(row_layout)

        combo = QComboBox()
        self._fill_config_choices(combo, self._house_labels)
        combo.setMinimumContentsLength(18)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )

        spin = QSpinBox()
        spin.setMinimum(1)
        spin.setMaximum(10_000)
        spin.setValue(1)
        spin.setPrefix("Houses: ")
        spin.setAccessibleName("Number of houses")

        remove_btn = QPushButton("Remove")

        row_layout.addWidget(combo, stretch=1)
        spin.setFixedWidth(120)
        remove_btn.setFixedWidth(100)
        row_layout.addWidget(spin)
        row_layout.addWidget(remove_btn)

        summary = ConfigSummary(
            HOUSE_CONFIG_FIELDS,
            {
                "pv_peak_capacity",
                "pv_inverter_ac_kw",
                "has_aircon",
                "has_poolpump",
                "has_ev",
            },
        )
        group.addWidget(summary)
        summary.set_record(self._house_records.get(combo.currentData()))
        combo.currentIndexChanged.connect(
            lambda _: self._update_house_summary(row_widget, combo.currentData())
        )

        self.house_rows.append((combo, spin, row_widget))
        self.house_rows_layout.addWidget(row_widget)

        remove_btn.clicked.connect(lambda: self.remove_house_row(row_widget))

    def remove_house_row(self, row_widget: QWidget):
        self.house_rows = [r for r in self.house_rows if r[2] is not row_widget]
        self.house_rows_layout.removeWidget(row_widget)
        row_widget.deleteLater()

    def is_fixed_tariff(self) -> bool:
        return self.tariff_combo.currentData() == "fixed"

    def on_tariff_changed(self):
        for widget in self.fixed_rows:
            self.form.setRowVisible(widget, self.is_fixed_tariff())

    def optional_price(self, edit: QLineEdit) -> float | None:
        """None means use the AEMO market price."""
        if not self.is_fixed_tariff():
            return None
        text = edit.text().strip()
        return edit.validator().locale().toDouble(text)[0] if text else None

    def on_run_btn_pressed(self):
        if not self.is_running:
            self.on_run()
        else:
            self.on_cancel()
            self.run_btn.setText("Run Simulation")
            self.is_running = False

    def get_simulation_kwargs(self) -> dict:
        houses: dict[str, int] = {}
        for combo, spin, _ in self.house_rows:
            config_id = combo.currentData()
            if config_id is None:
                self.status_label.setText("Choose a saved house configuration.")
                return
            houses[config_id] = houses.get(config_id, 0) + spin.value()

        tz_str = self.location_combo.currentData()
        local_tz = ZoneInfo(tz_str) if tz_str else UTC

        start_time_utc = datetime(
            self.selected_date.year,
            self.selected_date.month,
            self.selected_date.day,
            0,
            0,
            0,
            tzinfo=local_tz,
        ).astimezone(UTC)

        end_time_utc = start_time_utc + timedelta(days=self.duration_spin.value())

        return {
            "objective_mode": self.objective_combo.currentText(),
            "start_time_utc": start_time_utc,
            "end_time_utc": end_time_utc,
            "location_name": self.location_combo.currentText(),
            "battery_config_id": self.battery_combo.currentData(),
            "houses": houses,
            "fixed_buy_aud_per_kwh": self.optional_price(self.fixed_buy_edit),
            "fixed_sell_aud_per_kwh": self.optional_price(self.fixed_sell_edit),
        }

    def on_run(self):
        if self.is_fixed_tariff() and not (
            self.fixed_buy_edit.text().strip() and self.fixed_sell_edit.text().strip()
        ):
            self.status_label.setText("Enter both a buy and sell price.")
            return

        self.status_label.setText("Starting...")
        self.run_btn.setText("Cancel Simulation")
        self.is_running = True
        self.optimiser_gateway.start_simulation(self.get_simulation_kwargs())

    def on_cancel(self):
        self.status_label.setText("Cancelling gracefully...(This may take a moment)")
        self.run_btn.setEnabled(False)
        self.optimiser_gateway.cancel_simulation()

    def on_cancelled(self):
        self.status_label.setText("Simulation cancelled.")
        self.run_btn.setText("Run Simulation")
        self.is_running = False
        self.run_btn.setEnabled(True)

    def on_finished(self, request_id: str):
        """Show the finished run on the other tabs. The write also refetches every
        getter, so the run appears in the saved requests."""
        self.status_label.setText("Simulation finished.")
        self.run_btn.setText("Run Simulation")
        self.is_running = False
        self.run_btn.setEnabled(True)
        self.select_run.submit({"request_id": request_id})

    def on_error(self, message: str):
        self.status_label.setText(f"Error: {message}")
        self.run_btn.setText("Run Simulation")
        self.is_running = False
        self.run_btn.setEnabled(True)

    def on_select_run_result(self, result: Result):
        if result.status == Status.FAILURE:
            self.status_label.setText(
                f"Simulation finished, but it could not be shown: {result.message}"
            )
