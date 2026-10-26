from datetime import datetime
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QTableView

from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import RecordsPayload, TabularPayload


def _display_value(value: Any) -> str:
    """Convert table value into a string for display."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class ColumnOrientedModel(QAbstractTableModel):
    """Table model for records keyed by an ID (and column-oriented)."""

    def __init__(self, payload: RecordsPayload):
        super().__init__()
        self._headers = list(next(iter(payload.records.values()), {}).keys())
        self._record_ids = list(payload.records)
        self._records = payload.records

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None

        record = self._records[self._record_ids[index.row()]]
        return _display_value(record[self._headers[index.column()]])

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._headers[section]
        return self._record_ids[section]

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._record_ids)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._headers)


class RowOrientedModel(QAbstractTableModel):
    """Table model for a row-oriented payload."""

    def __init__(self, payload: TabularPayload):
        super().__init__()
        self._headers = payload.headers
        self._rows = payload.rows

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        return _display_value(self._rows[index.row()][index.column()])

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._headers[section]
        return str(section + 1)

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._headers)


class TableView(PanelView):
    """Draws row-oriented or column-oriented table."""

    PAYLOAD_TYPE = (RecordsPayload, TabularPayload)

    def __init__(self, title: str, help_text: str, empty_text: str) -> None:
        self.table = QTableView()
        self.model: QAbstractTableModel | None = None

        super().__init__(self.table, title, help_text, empty_text)

    def _draw(self, payload: RecordsPayload | TabularPayload) -> None:
        if isinstance(payload, RecordsPayload):
            self.model = ColumnOrientedModel(payload)
        else:
            self.model = RowOrientedModel(payload)

        self.table.setModel(self.model)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        # Row numbers only mean something for records, which are named by id
        self.table.verticalHeader().setVisible(isinstance(payload, RecordsPayload))
