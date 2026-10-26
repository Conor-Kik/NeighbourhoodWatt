"""choice_view.py
Panel that picks one record from a list, such as the run the app shows."""

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.ui.views.form_view import FORM_MAX_WIDTH
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import ChoicesPayload

IN_USE_SUFFIX = "  (in use)"


class ChoiceView(PanelView):
    """List of choices with a "Use selected" button. Draws a ChoicesPayload; the
    saved pick is marked in the list and named above it.

    Submits {"key": setting_key, "value": id}, ready for an app setting setter."""

    PAYLOAD_TYPE = ChoicesPayload

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        setting_key: str | None = None,
        min_width: int = 0,
        min_height: int = 0,
        value_key: str | None = None,
        compact: bool = False,
    ):
        self.setting_key = setting_key
        self.value_key = value_key

        self._current = QLabel()
        self._current.setTextFormat(Qt.TextFormat.PlainText)
        self._current.setWordWrap(True)
        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._picker = QComboBox() if compact else None
        use = QPushButton("Use selected")

        form = QWidget()
        if compact:
            form.setMaximumWidth(FORM_MAX_WIDTH)
        else:
            self._list.setMinimumHeight(240)
        # Object names are targeted by style.qss
        form.setObjectName("form")
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._picker is None:
            layout.addWidget(self._current)
            layout.addWidget(self._list, stretch=1)
            layout.addWidget(use)
        else:
            self._picker.setAccessibleName(title)
            layout.addWidget(self._picker)
            self._picker.activated.connect(lambda _: self._submit())
            self._current.hide()
            self._list.hide()
            use.hide()

        super().__init__(
            form,
            title,
            help_text,
            empty_text,
            min_width=min_width,
            min_height=min_height,
        )

        use.clicked.connect(self._submit)
        # Lambda, since itemDoubleClicked would pass its item to _submit
        self._list.itemDoubleClicked.connect(lambda _: self._submit())

    def _draw(self, payload: ChoicesPayload) -> None:
        """Refill the list, keeping the selected id, or else selecting the
        saved pick."""
        if self._picker is not None:
            with QSignalBlocker(self._picker):
                self._picker.clear()
                for record_id, label in payload.choices.items():
                    self._picker.addItem(label, record_id)
                self._picker.setCurrentIndex(self._picker.findData(payload.current))
            return
        selected = self._selected_id() or payload.current

        self._list.clear()
        for record_id, label in payload.choices.items():
            in_use = record_id == payload.current
            item = QListWidgetItem(label + (IN_USE_SUFFIX if in_use else ""))
            item.setData(Qt.ItemDataRole.UserRole, record_id)
            item.setToolTip(f"Saved reference: {record_id}")
            if in_use:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._list.addItem(item)
            if record_id == selected:
                self._list.setCurrentItem(item)

        if payload.current in payload.choices:
            self._current.setText(f"In use: {payload.choices[payload.current]}")
        elif payload.current is None:
            self._current.setText("None in use yet.")
        else:
            self._current.setText(f"In use: {payload.current} (no longer saved)")

    def _selected_id(self) -> str | None:
        if self._picker is not None:
            return self._picker.currentData()
        item = self._list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _submit(self) -> None:
        """Emit the selected id under setting_key."""
        record_id = self._selected_id()
        if record_id is None:
            self.show_error("Select one first.")
            return
        row = (
            {self.value_key: record_id}
            if self.value_key is not None
            else {"key": self.setting_key, "value": record_id}
        )
        self.submitted.emit(row)
