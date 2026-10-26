"""Dirty refresh coalescing, hidden catch-up, and fixed/default panel layouts."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import TabularPayload

APP = QApplication.instance() or QApplication([])


class FakeManager(QObject):
    updated_keys = pyqtSignal(set)

    def __init__(self):
        super().__init__()
        self.value = 1
        self.reads = []

    def get(self, key):
        self.reads.append(key)
        assert not key.startswith("layout_")
        return self.value


class CountingView(PanelView):
    def __init__(self):
        super().__init__(QWidget(), "Counter", "", "")
        self.values = []

    def _draw(self, payload):
        self.values.append(payload.rows[0][0])


@needs({"value"})
def select_value(data):
    return TabularPayload(headers=["value"], rows=[(data["value"],)])


def make_grid(manager, count=1, responsive=False):
    controllers = [
        PanelController(str(i), CountingView(), ReadBinding(select_value))
        for i in range(count)
    ]
    columns = 2 if responsive else 1
    grid = Grid(
        manager,
        "test",
        [
            GridItem(i // columns, i % columns, 1, 1, c)
            for i, c in enumerate(controllers)
        ],
        n_rows=(count + columns - 1) // columns,
        n_cols=columns,
        minimum_column_width=480 if responsive else None,
    )
    return grid, controllers


def test_notifications_coalesce_and_render_latest_value():
    manager = FakeManager()
    grid, controllers = make_grid(manager)
    grid.show()
    APP.processEvents()
    for value in (1, 2, 3):
        manager.value = value
        manager.updated_keys.emit({"value"})
    assert controllers[0].view.values == []
    APP.processEvents()
    assert controllers[0].view.values == [3]
    grid.close()


def test_hidden_grid_catches_up_once_and_ignores_old_layout_preferences():
    manager = FakeManager()
    grid, controllers = make_grid(manager)
    manager.updated_keys.emit({"layout_test", "value"})
    APP.processEvents()
    assert controllers[0].view.values == []
    grid.show()
    APP.processEvents()
    assert controllers[0].view.values == [1]
    grid.hide()
    manager.value = 9
    manager.updated_keys.emit({"value"})
    APP.processEvents()
    assert controllers[0].view.values == [1]
    grid.show()
    APP.processEvents()
    assert controllers[0].view.values == [1, 9]
    grid.close()


def test_responsive_grid_preserves_reading_order_across_widths():
    manager = FakeManager()
    grid, controllers = make_grid(manager, count=6, responsive=True)
    grid.resize(1200, 1000)
    grid.show()
    APP.processEvents()
    assert grid.n_cols == 2
    grid.resize(700, 1000)
    APP.processEvents()
    assert grid.n_cols == 1
    assert [(i.row, i.col) for i in grid.items] == [(i, 0) for i in range(6)]
    assert [i.controller for i in grid.items] == controllers
    grid.resize(1200, 1000)
    APP.processEvents()
    assert grid.n_cols == 2
    assert [(i.row, i.col) for i in grid.items] == [(i // 2, i % 2) for i in range(6)]
    grid.close()


def test_refresh_yields_between_panels_for_input_events():
    from PyQt6.QtCore import QTimer

    manager = FakeManager()
    grid, controllers = make_grid(manager, count=3)
    grid.show()
    APP.processEvents()
    manager.updated_keys.emit({"value"})
    counts = []
    probe = QTimer(grid)
    probe.setSingleShot(True)
    probe.timeout.connect(
        lambda: counts.append(sum(len(c.view.values) for c in controllers))
    )
    probe.start(0)
    for _ in range(5):
        APP.processEvents()
    assert counts == [1]
    assert all(c.view.values == [1] for c in controllers)
    grid.close()
