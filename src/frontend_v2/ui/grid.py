"""Lay out panel controllers and refresh invalidated visible panels."""

from dataclasses import dataclass, replace
from math import ceil

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QResizeEvent, QShowEvent
from PyQt6.QtWidgets import QGridLayout, QWidget

from frontend_v2.engine.data_manager import DataManager
from frontend_v2.ui.panel_controller import PanelController


@dataclass(frozen=True)
class GridItem:
    """A controller's position and span within a Grid."""

    row: int
    col: int
    width: int
    height: int
    controller: PanelController


def validate_layout(items: list[GridItem], n_rows: int, n_cols: int) -> None:
    """Raise ValueError naming the offending item if the items overlap or fall
    outside an n_rows by n_cols grid."""
    occupied = set()

    for item in items:
        if item.width <= 0 or item.height <= 0:
            raise ValueError(f"Item {item.controller.id} must have a positive size")
        for r in range(item.row, item.row + item.height):
            for c in range(item.col, item.col + item.width):
                if not (0 <= r < n_rows and 0 <= c < n_cols):
                    raise ValueError(
                        f"Item {item.controller.id} is out of bounds at "
                        f"row={item.row}, col={item.col} with "
                        f"height={item.height}, width={item.width}"
                    )

                if (r, c) in occupied:
                    raise ValueError(
                        f"Item {item.controller.id} is overlapping at "
                        f"row={item.row}, col={item.col} with "
                        f"height={item.height}, width={item.width}"
                    )
                occupied.add((r, c))


class Grid(QWidget):
    """Place panels and refresh dirty controllers only while visible.

    Holds no data. Optional minimum_column_width reflows unit-sized items
    in reading order; other grids retain their declared positions.
    """

    def __init__(
        self,
        data_manager: DataManager,
        id: str,
        items: list[GridItem],
        n_rows: int = 8,
        n_cols: int = 6,
        minimum_column_width: int | None = None,
    ):
        super().__init__()
        validate_layout(items, n_rows, n_cols)
        if minimum_column_width is not None and (
            minimum_column_width <= 0
            or any(item.width != 1 or item.height != 1 for item in items)
        ):
            raise ValueError("Responsive grids need unit panels and a positive width")
        self.id = id
        self.setObjectName(id)
        self.data_manager = data_manager
        self.items = list(items)
        self.n_rows = n_rows
        self.n_cols = n_cols
        self._maximum_columns = n_cols
        self._minimum_column_width = minimum_column_width
        self.needs = set().union(*(item.controller.needs for item in items))
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_invalidated)
        self._grid = QGridLayout(self)
        for row in range(n_rows):
            self._grid.setRowStretch(row, 1)
        for col in range(n_cols):
            self._grid.setColumnStretch(col, 1)
        for item in self.items:
            self._grid.addWidget(
                item.controller.view, item.row, item.col, item.height, item.width
            )
        self.data_manager.updated_keys.connect(self.refresh)

    def refresh(self, updated: set[str]) -> None:
        """Combine notifications until the event loop can refresh the latest state."""
        if not self.needs & updated:
            return
        for item in self.items:
            if item.controller.needs & updated:
                item.controller.invalidate()
        if self.isVisible() and not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def _refresh_invalidated(self) -> None:
        """Refresh dirty controllers, keeping hidden pages dirty until shown."""
        if not self.isVisible():
            return
        for item in self.items:
            controller = item.controller
            if controller.is_invalidated:
                controller.refresh(
                    {key: self.data_manager.get(key) for key in controller.needs}
                )
                # Yield between panels so pause/input events stay responsive.
                if any(item.controller.is_invalidated for item in self.items):
                    self._refresh_timer.start(0)
                return

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._minimum_column_width is None:
            return
        margins = self._grid.contentsMargins()
        available = self.width() - margins.left() - margins.right()
        columns = max(
            1,
            min(
                self._maximum_columns,
                (available + self._grid.horizontalSpacing())
                // (self._minimum_column_width + self._grid.horizontalSpacing()),
            ),
        )
        if columns == self.n_cols:
            return
        for item in self.items:
            self._grid.removeWidget(item.controller.view)
        for row in range(self.n_rows):
            self._grid.setRowStretch(row, 0)
        for col in range(self.n_cols):
            self._grid.setColumnStretch(col, 0)
        self.n_cols = columns
        self.n_rows = ceil(len(self.items) / columns)
        self.items = [
            replace(item, row=index // columns, col=index % columns)
            for index, item in enumerate(self.items)
        ]
        for row in range(self.n_rows):
            self._grid.setRowStretch(row, 1)
        for col in range(columns):
            self._grid.setColumnStretch(col, 1)
        for item in self.items:
            self._grid.addWidget(item.controller.view, item.row, item.col)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Catch up on notifications received while the page was hidden."""
        super().showEvent(event)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(0)
