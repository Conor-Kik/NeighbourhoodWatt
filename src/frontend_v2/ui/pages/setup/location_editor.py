"""location_editor.py
Panel that views existing locations and adds new ones."""

from functools import cache

import australian_postcodes  # type: ignore[import-untyped]
from PyQt6.QtCore import QRegularExpression, QSignalBlocker
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from timezonefinder import TimezoneFinder

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.bindings.bindings import WriteBinding
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status

ADD_NEW_TEXT = "New location"

STATE_MAPPING = {
    "QLD": ("Australia/Brisbane", "QLD1"),
    "ACT": ("Australia/Sydney", "NSW1"),
    "NSW": ("Australia/Sydney", "NSW1"),
    "VIC": ("Australia/Melbourne", "VIC1"),
    "TAS": ("Australia/Hobart", "TAS1"),
    "SA": ("Australia/Adelaide", "SA1"),
}


@cache
def _timezone_finder() -> TimezoneFinder:
    return TimezoneFinder()


class LocationEditor(QWidget):
    """View existing locations and add new ones.

    Fields are the set_location setter's dtype fields, so they follow the schema.
    Existing locations are read-only; only new ones are saved. A State pick fills
    in the timezone and NEM region."""

    def __init__(self, data_manager: DataManager):
        super().__init__()
        self.data_manager = data_manager
        self.records_cache: dict[str, dict] = {}

        self.write = WriteBinding(data_manager, "set_location")
        self.write.result.connect(self.on_save_result)

        self.setMaximumWidth(720)
        layout = QVBoxLayout(self)

        self.selector_combo = QComboBox()
        self.selector_combo.addItem(ADD_NEW_TEXT)
        self.selector_combo.currentTextChanged.connect(self.on_selection_changed)
        self.create_btn = QPushButton("Create new", self)
        self.create_btn.clicked.connect(self._new_location)
        picker_row = QHBoxLayout()
        picker_row.addWidget(self.selector_combo, stretch=1)
        picker_row.addWidget(self.create_btn)
        layout.addLayout(picker_row)
        self.mode_label = QLabel("New location — enter a name and save.")
        self.mode_label.setWordWrap(True)
        layout.addWidget(self.mode_label)

        self.lookup_widget = QWidget()
        lookup_layout = QFormLayout(self.lookup_widget)
        lookup_layout.setContentsMargins(0, 0, 0, 0)
        lookup_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.postcode = QLineEdit()
        self.postcode.setPlaceholderText("e.g. 4000 (optional)")
        self.postcode.setMaxLength(4)
        self.postcode.setValidator(
            QRegularExpressionValidator(QRegularExpression("[0-9]{0,4}"), self)
        )
        lookup_layout.addRow("Postcode", self.postcode)
        self.locality = QComboBox()
        self.locality.setAccessibleName("Suburb for postcode")
        self.locality.addItem("Enter postcode first", None)
        self.locality.setEnabled(False)
        lookup_layout.addRow("Suburb", self.locality)
        self.lookup_status = QLabel(
            "Enter a postcode to fill the location, or enter it manually."
        )
        self.lookup_status.setWordWrap(True)
        lookup_layout.addRow(self.lookup_status)
        layout.addWidget(self.lookup_widget)

        self.form_widget = QWidget()
        self.form_layout = QFormLayout(self.form_widget)
        self.form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        # Read off the class, since the DataManager registry fills in off-thread
        self.dtype = FrontendDao.set_location.dtype
        self.inputs = {}
        for field in self.dtype.names:
            self.inputs[field] = QLineEdit()
            label = field.replace("_", " ").title()
            self.form_layout.addRow(label, self.inputs[field])
            self.form_layout.labelForField(self.inputs[field]).setWordWrap(True)

        # A State pick fills in the timezone and NEM region, so hide their inputs
        self._hide_field("timezone")
        self._hide_field("nem_region")

        self.state_combo = QComboBox()
        self.state_combo.addItems(list(STATE_MAPPING))
        self.state_combo.currentTextChanged.connect(self.on_state_changed)
        self.form_layout.addRow("State", self.state_combo)

        layout.addWidget(self.form_widget)

        self.save_btn = QPushButton("Save location")
        self.save_btn.clicked.connect(self.on_save)
        layout.addWidget(self.save_btn)
        layout.addStretch()

        self.data_manager.updated_keys.connect(self.on_data_updated)
        self.postcode.textChanged.connect(self.on_postcode_changed)
        self.locality.currentIndexChanged.connect(self.on_locality_changed)
        self.on_state_changed(self.state_combo.currentText())

    def _new_location(self) -> None:
        with QSignalBlocker(self.selector_combo):
            self.selector_combo.setCurrentIndex(0)
        self.on_selection_changed(ADD_NEW_TEXT)
        self.inputs["location_name"].setFocus()

    def on_postcode_changed(self, postcode: str) -> None:
        """Use the package's offline lookup without changing a draft until selected."""
        self.locality.blockSignals(True)
        self.locality.clear()
        self.locality.addItem("Choose suburb", None)
        matches = (
            [
                row
                for row in australian_postcodes.find_by_postcode(postcode)
                if row.category == "Delivery Area"
            ]
            if len(postcode) == 4
            else []
        )
        for row in sorted(matches, key=lambda row: (row.suburb, row.state)):
            self.locality.addItem(f"{row.suburb.title()} · {row.state}", row)
        self.locality.setEnabled(bool(matches))
        self.locality.blockSignals(False)
        self.lookup_status.setText(
            "Choose the suburb to fill its approximate coordinates."
            if matches
            else "Postcode not found. You can enter the location manually."
            if len(postcode) == 4
            else "Enter a four-digit postcode, or enter the location manually."
        )
        if len(matches) == 1:
            self.locality.setCurrentIndex(1)

    def on_locality_changed(self, index: int) -> None:
        """Prefill only existing location fields; postcode is never submitted."""
        record = self.locality.itemData(index)
        if record is None:
            return
        if record.state not in STATE_MAPPING:
            self.lookup_status.setText(
                "This state is outside the app's supported NEM regions."
            )
            return
        self.state_combo.setCurrentText(record.state)
        self.on_state_changed(record.state)
        self.inputs["location_name"].setText(record.suburb.title())
        self.inputs["latitude"].setText(record.lat)
        self.inputs["longitude"].setText(record.lon)
        if not record.lat or not record.lon:
            self.lookup_status.setText(
                "Coordinates unavailable for this suburb. "
                "Enter latitude and longitude manually."
            )
            return
        timezone = _timezone_finder().timezone_at(
            lat=float(record.lat), lng=float(record.lon)
        )
        if timezone:
            self.inputs["timezone"].setText(timezone)
        self.lookup_status.setText(
            "Approximate coordinates; adjust if needed. "
            f"{self.inputs['timezone'].text()} · {self.inputs['nem_region'].text()}"
        )

    def _hide_field(self, field: str) -> None:
        """Hide one input and its label."""
        self.inputs[field].hide()
        label = self.form_layout.labelForField(self.inputs[field])
        if label:
            label.hide()

    def on_data_updated(self, updated: set[str]) -> None:
        """Rebuild the location picker when the saved locations change."""
        if "get_all_locations" not in updated:
            return

        records = self.data_manager.get("get_all_locations")

        # Plain dicts, since buffer rows are views that later fetches overwrite
        names = records.dtype.names
        self.records_cache = {
            str(row["location_name"]): {name: row[name].item() for name in names}
            for row in records
        }

        current_selection = self.selector_combo.currentText()
        self.selector_combo.blockSignals(True)
        self.selector_combo.clear()
        self.selector_combo.addItem(ADD_NEW_TEXT)
        self.selector_combo.addItems(list(self.records_cache))
        index = self.selector_combo.findText(current_selection)
        if index >= 0:
            self.selector_combo.setCurrentIndex(index)
        self.selector_combo.blockSignals(False)

        # Updating choices must not clear an unfinished new-location draft.
        if self.selector_combo.currentText() != ADD_NEW_TEXT:
            self.on_selection_changed(self.selector_combo.currentText())

    def on_selection_changed(self, text: str) -> None:
        """Show the picked location read-only, or clear the fields to add a new
        one."""
        if not text:
            return

        is_new = text == ADD_NEW_TEXT
        record = self.records_cache.get(text)
        self.mode_label.setText(
            "New location — enter a name and save."
            if is_new
            else "Saved locations are read-only. Choose Create new to add another."
        )

        for field, widget in self.inputs.items():
            widget.setReadOnly(not is_new)
            if is_new:
                widget.clear()
            else:
                widget.setText(str(record[field]) if record else "")

        self.save_btn.setEnabled(is_new)
        self.lookup_widget.setVisible(is_new)
        self.postcode.clear()
        self._show_state(is_new, record)

    def _show_state(self, is_new: bool, record: dict | None) -> None:
        """Enable the State pick for a new location; for an existing one, show the
        state its timezone and NEM region map to."""
        self.state_combo.setEnabled(is_new)
        if is_new:
            self.on_state_changed(self.state_combo.currentText())
            return
        if record is None:
            return

        timezone = record.get("timezone")
        nem_region = record.get("nem_region")
        for state, (mapped_timezone, mapped_nem_region) in STATE_MAPPING.items():
            # ACT shares NSW's timezone and region, so match it before NSW
            if timezone == mapped_timezone and nem_region == mapped_nem_region:
                self.state_combo.blockSignals(True)
                self.state_combo.setCurrentText(state)
                self.state_combo.blockSignals(False)
                return
        # Timezone exceptions such as Broken Hill still belong to the NSW region.
        self.state_combo.blockSignals(True)
        self.state_combo.setCurrentText(str(nem_region).removesuffix("1"))
        self.state_combo.blockSignals(False)

    def on_state_changed(self, state: str) -> None:
        """Fill the hidden timezone and NEM region fields from the picked state."""
        if state in STATE_MAPPING:
            timezone, nem_region = STATE_MAPPING[state]
            self.inputs["timezone"].setText(timezone)
            self.inputs["nem_region"].setText(nem_region)

    def on_save(self) -> None:
        """Save the new location's fields."""
        row = {field: widget.text() or None for field, widget in self.inputs.items()}
        self.write.submit(row)

    def on_save_result(self, result: Result) -> None:
        """A successful save refetches the list, so only failures need showing."""
        if result.status == Status.FAILURE:
            QMessageBox.critical(
                self, "Error Saving", f"Failed to save:\n\n{result.message}"
            )
