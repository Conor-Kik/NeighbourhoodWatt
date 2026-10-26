"""form_view.py
Panel that edits one record at a time, picked by id."""

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import RecordsPayload

# Inputs stay this wide, so a large range doesn't widen the form
INPUT_WIDTH = 140
# Forms stay this wide in wide panels, so buttons and pickers don't stretch
FORM_MAX_WIDTH = 520


@dataclass(frozen=True)
class FormField:
    """One input on a FormView. kind is float, int or bool. minimum, maximum and
    step are in shown units; shown value = stored value Ã— scale (e.g. scale=100
    shows a 0-1 fraction as a percentage)."""

    key: str
    label: str
    kind: type = float
    unit: str = ""
    minimum: float = 0.0
    maximum: float = 1_000_000.0
    step: float = 1.0
    decimals: int = 2
    scale: float = 1.0


class FormView(PanelView):
    """Editor for one record at a time. Draws a RecordsPayload.

    Picking an id fills the fields with its saved values. Typing a new id keeps
    the shown values, so Apply saves them as a new record. Later payloads only
    update the id list, so edits in progress are never overwritten. When
    existing_read_only is set, a picked existing record is shown read-only, so
    only new records are saved."""

    PAYLOAD_TYPE = RecordsPayload

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        id_key: str,
        id_label: str,
        fields: list[FormField],
        existing_read_only: bool = False,
        min_width: int = 0,
        min_height: int = 0,
        scrollable: bool = True,
    ):
        self.id_key = id_key
        self.fields = fields
        self.existing_read_only = existing_read_only
        self._records: dict[str, dict] = {}
        self._creating = False

        form = QWidget()
        self._picker = QComboBox()
        self._picker.setEditable(True)
        self._picker.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._picker.setMinimumWidth(INPUT_WIDTH)
        self._inputs = {field.key: self._make_input(field) for field in fields}
        self._apply = QPushButton("Apply changes")
        self._create = QPushButton("Create new", form)
        self._create.setVisible(existing_read_only)
        self._create.setToolTip(
            "Enter a new name, adjust the selected configuration's values, then save."
        )
        self._mode = QLabel(form)
        self._mode.setWordWrap(True)
        self._mode.setVisible(existing_read_only)
        if existing_read_only:
            self._apply.setText("Save configuration")
            self._picker.lineEdit().setPlaceholderText("Enter a new configuration name")

        form.setObjectName("form")
        form.setMaximumWidth(FORM_MAX_WIDTH)
        layout = QFormLayout(form)
        self._form_layout = layout
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        picker_row = QHBoxLayout()
        picker_row.addWidget(self._picker, stretch=1)
        picker_row.addWidget(self._create)
        layout.addRow(id_label, picker_row)
        if existing_read_only:
            layout.addRow(self._mode)
        for field in fields:
            layout.addRow(field.label, self._inputs[field.key])
            layout.labelForField(self._inputs[field.key]).setWordWrap(True)
        layout.addRow(self._apply)

        content = form
        if scrollable:
            scroll = QScrollArea()
            scroll.setObjectName("form")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(form)
            content = scroll

        super().__init__(
            content,
            title,
            help_text,
            empty_text,
            min_width=min_width,
            min_height=min_height,
        )

        self._picker.currentTextChanged.connect(self._load)
        self._apply.clicked.connect(self._submit)
        self._create.clicked.connect(self._new_record)

    def _draw(self, payload: RecordsPayload) -> None:
        """Refresh the id list, keeping the id being edited. The first payload
        also loads the first record."""
        current = self._record_id()
        self._records = payload.records

        # Refilling the list would otherwise load a record over the edits
        self._picker.blockSignals(True)
        self._picker.clear()
        for record_id in payload.records:
            self._picker.addItem(payload.labels.get(record_id, record_id), record_id)
        if current in self._records:
            self._picker.setCurrentIndex(self._picker.findData(current))
        elif current or self._creating:
            self._picker.setEditText(current)
        self._picker.blockSignals(False)

        if not current and not self._creating:
            self._load(self._picker.currentText())
        elif self.existing_read_only:
            self._set_editable(current not in self._records)
            if current in self._records:
                self._create.setVisible(True)

    def show_empty(self) -> None:
        """Keep the editor available so the first record can be created."""
        self.show_payload(RecordsPayload(records={}))

    def _new_record(self) -> None:
        """Start a named copy using the current values and existing save path."""
        self._creating = True
        self._picker.setCurrentIndex(-1)
        self._picker.clearEditText()
        self._set_editable(True)
        self._picker.setFocus()

    def _load(self, _text: str) -> None:
        """Fill the fields with the selected record's saved values. An
        existing record is shown read-only when this form saves new records
        only."""
        record_id = self._record_id()
        record = self._records.get(record_id)
        if record is not None:
            self._creating = False
            for field in self.fields:
                value = record.get(field.key)
                if value is None:
                    continue

                widget = self._inputs[field.key]
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(round(value * field.scale))
                else:
                    widget.setValue(value * field.scale)

        if self.existing_read_only:
            self._set_editable(record is None)
        self._picker.setToolTip(
            f"Saved configuration ID: {record_id}"
            if record is not None
            else "Enter a unique name for the new configuration."
        )

    def _record_id(self) -> str:
        """Resolve a saved label to its ID; new names stay exactly as entered."""
        index = self._picker.currentIndex()
        if index >= 0 and self._picker.currentText() == self._picker.itemText(index):
            return self._picker.currentData() or self._picker.currentText()
        return self._picker.currentText()

    def _set_editable(self, editable: bool) -> None:
        """Enable or disable every input and the Apply button."""
        for widget in self._inputs.values():
            widget.setEnabled(editable)
        self._apply.setEnabled(editable)
        self._mode.setText(
            "New configuration: enter a unique name, adjust the values and save."
            if editable
            else "Saved configuration (read-only). Choose Create new to make a copy."
        )

    def _submit(self) -> None:
        """Emit the shown record as one row, in stored units."""
        record_id = self._record_id().strip()
        if not record_id:
            self.show_error("Enter an id to save.")
            return
        if self.existing_read_only and record_id in self._records:
            self.show_error(
                "Saved configurations are read-only. Choose Create new to make a copy."
            )
            return

        row = {self.id_key: record_id}
        for field in self.fields:
            row[field.key] = self._value(field)
        self.submitted.emit(row)
        if self.existing_read_only:
            # Saved configurations are immutable; wait for the refetch read-only.
            self._creating = False
            self._set_editable(False)
            self._create.setVisible(True)

    def _value(self, field: FormField) -> float | int | bool:
        """Field's shown value, converted back to stored units."""
        widget = self._inputs[field.key]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return round(widget.value() / field.scale)
        return widget.value() / field.scale

    @staticmethod
    def _make_input(field: FormField) -> QDoubleSpinBox | QSpinBox | QCheckBox:
        """Check box for bool, spin box for int or float."""
        if field.kind is bool:
            checkbox = QCheckBox("No")
            checkbox.toggled.connect(
                lambda checked: checkbox.setText("Yes" if checked else "No")
            )
            return checkbox

        if field.kind is int:
            spin = QSpinBox()
            spin.setRange(int(field.minimum), int(field.maximum))
            spin.setSingleStep(int(field.step))
        else:
            spin = QDoubleSpinBox()
            spin.setDecimals(field.decimals)
            spin.setRange(field.minimum, field.maximum)
            spin.setSingleStep(field.step)

        if field.unit:
            spin.setSuffix(f" {field.unit}")
        spin.setGroupSeparatorShown(True)
        spin.setFixedWidth(INPUT_WIDTH)
        return spin
