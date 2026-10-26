"""Community keeps objective context, recorded totals and responsive charts."""

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QScrollArea

from frontend_v2.ui.grid import Grid
from frontend_v2.ui.pages.community_page import community_page
from frontend_v2.ui.scroll_area import scrollable_page
from tests.frontend_v2.bindings.selectors.test_community_objectives import make_data
from tests.frontend_v2.ui.test_settings_page import FakeManager

APP = QApplication.instance() or QApplication([])


def settle():
    for _ in range(20):
        APP.processEvents()


def make_page(mode="Cost"):
    manager = FakeManager()
    manager.values.update(make_data(mode))
    page = community_page(manager)
    scroll = scrollable_page(page, horizontal=False)
    scroll.resize(1400, 900)
    scroll.show()
    manager.updated_keys.emit(set(manager.values))
    settle()
    grids = {grid.id: grid for grid in page.findChildren(Grid)}
    panels = {
        item.controller.id: item.controller
        for grid in grids.values()
        for item in grid.items
    }
    return manager, page, scroll, grids, panels


def test_page_reuses_grids_and_one_scroll_owner_and_reflows():
    manager, page, scroll, grids, panels = make_page()
    assert not page.findChildren(QScrollArea)
    assert len(panels) == 7
    assert grids["community-summary"].n_cols == 3
    assert grids["community-charts"].n_cols == 2
    scroll.resize(760, 680)
    settle()
    assert grids["community-charts"].n_cols == 1
    assert grids["community-summary"].n_cols == 2
    assert scroll.horizontalScrollBar().maximum() == 0
    assert (
        grids["community-charts"].items[0].controller.id == "community_goal_comparison"
    )
    scroll.close()


def test_reactive_comparison_wraps_and_clears_when_run_is_unavailable():
    manager, page, scroll, grids, panels = make_page()
    chart = panels["community_goal_comparison"].view
    assert "$30.00 lower financial cost than reactive control" in chart.detail.text()
    assert chart.detail.wordWrap() and chart.detail.isVisible()
    manager.values["get_current_request"] = manager.values["get_current_request"][:0]
    manager.updated_keys.emit({"get_current_request"})
    settle()
    assert chart.detail.isHidden() and chart.detail.text() == ""
    scroll.close()


def test_missing_averages_and_baseline_do_not_hide_total_cards():
    manager, page, scroll, grids, panels = make_page()
    manager.values["get_current_run_houses"] = manager.values["get_current_run_houses"][
        :0
    ]
    manager.values["get_current_operating_costs"] = manager.values[
        "get_current_operating_costs"
    ][:2]
    manager.updated_keys.emit({"get_current_run_houses", "get_current_operating_costs"})
    settle()
    financial = panels["community_financial_cost"].view
    assert financial.label.text() == "$100.00"
    assert "Average unavailable" in financial.detail.text()
    assert financial._body.currentWidget() is financial._content
    assert panels["community_goal_benefit"].view.label.text() == "—"
    scroll.close()


def test_objective_switches_refresh_banner_card_and_chart_units():
    manager, page, scroll, grids, panels = make_page("Balanced")
    banner = panels["community_goal_context"].view
    chart = panels["community_goal_comparison"].view
    assert banner.labels["result"].text() == "$120.00"
    assert "$50.00/t CO₂e" in banner.labels["carbon_rate"].text()
    assert not banner.calculation.isHidden()
    assert not banner.carbon_source.isVisible()
    banner.more.click()
    settle()
    assert banner.carbon_source.isVisible()
    assert "Australian Energy Regulator" in banner.carbon_source.text()
    assert "2023 AUD" in banner.carbon_source.text()
    assert "aer.gov.au/system/files/2024-05/" in banner.carbon_source.text()
    assert banner.carbon_source.openExternalLinks()
    assert chart.ax.get_ylabel() == "Balanced score (AUD equivalent)"
    banner.more.click()
    settle()
    assert not banner.carbon_source.isVisible()
    banner.more.click()
    manager.values.update(make_data("Emissions"))
    manager.updated_keys.emit(set(manager.values))
    settle()
    assert banner.labels["goal"].text() == "Emissions"
    assert not banner.calculation.isHidden()
    assert banner.labels["financial"].text() == "400.0 kg"
    assert banner.labels["result"].text() == "400.0 kg"
    assert banner.components[1].isHidden()
    assert not banner.carbon_source.isVisible()
    assert banner.labels["carbon_rate"].isHidden()
    assert chart.ax.get_ylabel() == "Grid emissions (kg CO₂e)"
    assert panels["community_goal_benefit"].view.label.text() == "+200.0 kg"
    scroll.close()


def test_hidden_page_catches_up_and_missing_chart_recovers():
    manager, page, scroll, grids, panels = make_page()
    chart = panels["community_goal_comparison"].view
    scroll.hide()
    manager.values.update(make_data("Balanced"))
    manager.updated_keys.emit(set(manager.values))
    settle()
    assert panels["community_goal_comparison"].is_invalidated
    scroll.show()
    settle()
    assert chart.ax.get_ylabel() == "Balanced score (AUD equivalent)"
    manager.values["get_current_operating_costs"]["emissions_cost_aud"] = float("nan")
    manager.updated_keys.emit({"get_current_operating_costs"})
    settle()
    assert chart._body.currentWidget() is chart._empty
    manager.values.update(make_data("Cost"))
    manager.updated_keys.emit(set(manager.values))
    settle()
    assert chart._body.currentWidget() is chart._content
    assert chart.ax.get_ylabel() == "Financial cost (AUD)"
    scroll.close()


def test_goal_banner_survives_missing_results_and_none_selected_is_empty():
    manager, page, scroll, grids, panels = make_page("Balanced")
    manager.values["get_current_operating_costs"] = manager.values[
        "get_current_operating_costs"
    ][:0]
    manager.updated_keys.emit({"get_current_operating_costs"})
    settle()
    banner = panels["community_goal_context"].view
    assert banner._body.currentWidget() is banner._content
    assert banner.labels["goal"].text() == "Balanced"
    assert banner.labels["result"].text() == "—"
    manager.values["get_current_request"] = manager.values["get_current_request"][:0]
    manager.updated_keys.emit({"get_current_request"})
    settle()
    assert all(
        controller.view._body.currentWidget() is controller.view._empty
        for controller in panels.values()
    )
    scroll.close()


def test_goal_disclosure_and_calculation_reflow_preserve_facts():
    manager, page, scroll, grids, panels = make_page("Balanced")
    banner = panels["community_goal_context"].view
    assert banner.labels["details"].isHidden()
    assert banner.labels["context"].toolTip() == "Run run-a"
    assert banner._columns == 4
    assert banner.labels["financial"].text() == "$100.00"
    assert banner.labels["carbon"].text() == "$20.00"
    assert banner.labels["terminal"].text() == "$0.00"
    banner.more.click()
    settle()
    assert not banner.labels["details"].isHidden()
    assert "<b>Remaining energy</b>" in banner.labels["details"].text()
    assert "<b>Comparison basis</b>" in banner.labels["details"].text()
    assert "<b>Carbon weighting</b>" in banner.labels["details"].text()
    assert banner.carbon_source.isVisible()
    assert "not a fixed 50/50 split" in banner.labels["details"].text()
    scroll.resize(640, 680)
    settle()
    assert banner._columns == 2
    assert scroll.horizontalScrollBar().maximum() == 0
    assert banner.labels["result"].text() == "$120.00"
    assert "$50.00/t CO₂e" in banner.labels["carbon_rate"].text()
    manager.values.update(make_data("Cost"))
    manager.updated_keys.emit(set(manager.values))
    settle()
    assert not banner.calculation.isHidden()
    assert banner.labels["financial"].text() == "$100.00"
    assert banner.labels["result"].text() == "$100.00"
    assert banner.components[1].isHidden()
    assert not banner.carbon_source.isVisible()
    assert not banner.labels["note"].isHidden()
    assert not banner.labels["details"].isHidden()
    assert "50/50" not in banner.labels["details"].text()
    scroll.close()


def test_disclosure_does_not_rebuild_unchanged_calculation_columns():
    manager, page, scroll, grids, panels = make_page("Balanced")
    banner = panels["community_goal_context"].view
    with patch.object(
        banner.calculation_layout,
        "removeWidget",
        wraps=banner.calculation_layout.removeWidget,
    ) as removed:
        for _ in range(4):
            banner.more.click()
            settle()
        removed.assert_not_called()
    scroll.close()
