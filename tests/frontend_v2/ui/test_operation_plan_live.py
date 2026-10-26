"""Operation renders long-run comparisons through its normal refresh path."""

import numpy as np

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.engine.data_manager import UI_DURATION_KEY, UI_OPTIMISER_KEY
from frontend_v2.ui.grid import Grid
from frontend_v2.ui.pages.operation_page import operation_page
from tests.frontend_v2.bindings.selectors.test_plan_live_history import history
from tests.frontend_v2.ui.test_settings_page import APP, FakeManager


def operation_data(days=14):
    data = history(days)
    for key in dir(FrontendDao):
        getter = getattr(FrontendDao, key)
        if key.startswith("get_") and hasattr(getter, "dtype"):
            data.setdefault(key, np.array([], dtype=getter.dtype))
    data[UI_DURATION_KEY] = days * 24 * 60
    data[UI_OPTIMISER_KEY] = None
    for key in (
        "get_current_optimiser_outputs",
        "get_current_optimised_battery_outputs",
    ):
        rows = data[key]
        expanded = np.zeros(len(rows), dtype=getattr(FrontendDao, key).dtype)
        for field in rows.dtype.names:
            if field in expanded.dtype.names:
                expanded[field] = rows[field]
        data[key] = expanded
    return data


def test_sampled_live_chart_stays_visible_when_schedule_is_unavailable():
    manager = FakeManager()
    manager.values.update(operation_data())
    page = operation_page(manager)
    page.resize(1400, 900)
    page.show()
    panels = {
        item.controller.id: item.controller
        for grid in page.findChildren(Grid)
        for item in grid.items
    }
    manager.updated_keys.emit(set(manager.values))
    for _ in range(30):
        APP.processEvents()
    chart = panels["plan_vs_live_battery_action"].view
    assert chart._body.currentWidget() is chart._content
    assert len(chart._lines["Plan charge (kW)"].get_xdata()) > 0
    assert np.isfinite(chart._lines["Plan charge (kW)"].get_ydata()).any()
    manager.values["get_current_optimiser_outputs"] = manager.values[
        "get_current_optimiser_outputs"
    ][:0]
    manager.updated_keys.emit({"get_current_optimiser_outputs"})
    for _ in range(30):
        APP.processEvents()
    assert chart._body.currentWidget() is chart._content
    assert np.isnan(chart._lines["Plan charge (kW)"].get_ydata()).all()
    np.testing.assert_array_equal(chart._lines["Live charge (kW)"].get_ydata(), 8)
    page.close()
