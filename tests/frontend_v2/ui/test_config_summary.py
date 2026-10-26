"""Feature summaries stay accurate without changing configuration submissions."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QScrollArea

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.bindings.selectors.records import battery_wear_rate
from frontend_v2.ui.pages.setup.setup import setup_page
from frontend_v2.ui.pages.setup.simulation_form import SimulationForm
from frontend_v2.ui.views.battery_form_view import BatteryFormView
from frontend_v2.ui.views.config_summary import ConfigSummary
from tests.factories import BatteryConfigFactory, HouseConfigFactory
from tests.frontend_v2.ui.test_settings_page import FakeGateway, FakeManager

APP = QApplication.instance() or QApplication([])


def publish(manager, key, records):
    dtype = getattr(FrontendDao, key).dtype
    manager.values[key] = np.array(
        [tuple(record[name] for name in dtype.names) for record in records], dtype=dtype
    )
    manager.updated_keys.emit({key})


def battery_record(**changes):
    return (
        BatteryConfigFactory.build(
            capacity_kwh=257,
            replacement_cost_aud=77100,
            lifetime_cycles=6000,
        )
        .model_copy(update=changes)
        .model_dump()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"capacity_kwh": 0},
        {"lifetime_cycles": 0},
        {"capacity_kwh": float("inf")},
        {"replacement_cost_aud": -1},
        {"replacement_cost_aud": float("nan")},
    ],
)
def test_invalid_wear_inputs_are_unavailable(changes):
    assert battery_wear_rate(battery_record(**changes)) is None


def test_wear_uses_existing_model_and_zero_allowance_is_zero():
    record = battery_record()
    assert battery_wear_rate(record) == pytest.approx(0.025)
    assert battery_wear_rate(battery_record(replacement_cost_aud=0)) == 0
    assert battery_wear_rate({}) is None


def test_selection_summaries_show_yes_and_no_and_refresh_same_ids():
    manager = FakeManager()
    gateway = FakeGateway()
    form = SimulationForm(manager, gateway)
    house = HouseConfigFactory.build().model_dump()
    battery = battery_record()
    publish(manager, "get_all_house_configs", [house])
    publish(manager, "get_all_battery_configs", [battery])
    combo, quantity, row = form.house_rows[0]
    quantity.setValue(7)
    summary = row.findChild(ConfigSummary)
    assert "Air conditioning: Yes" in summary.summary.text()
    assert "Pool pump: No" in summary.summary.text()
    assert "Electric vehicle: Yes" in summary.summary.text()
    assert "Inverter size: 5.00 kW" in summary.summary.text()
    assert "Panel tilt" not in summary.summary.text()
    assert "Panel tilt" in summary.details.text()
    assert summary.details.isHidden()
    summary.more.click()
    assert not summary.details.isHidden()
    assert "2.5 c/kWh throughput" in form.battery_summary.wear.rate.text()
    assert "Capacity: 257.0 kWh" in form.battery_summary.summary.text()
    publish(manager, "get_all_house_configs", [{**house, "has_ev": False}])
    publish(
        manager,
        "get_all_battery_configs",
        [{**battery, "replacement_cost_aud": 154200}],
    )
    assert combo.currentData() == house["house_config_id"]
    assert quantity.value() == 7
    assert "Electric vehicle: No" in summary.summary.text()
    assert "5 c/kWh throughput" in form.battery_summary.wear.rate.text()
    form.close()


def test_multiple_house_rows_keep_independent_summaries_and_quantities():
    manager = FakeManager()
    gateway = FakeGateway()
    form = SimulationForm(manager, gateway)
    houses = [
        HouseConfigFactory.build().model_dump(),
        HouseConfigFactory.build(
            house_config_id="no-solar", pv_peak_capacity=0, has_aircon=False
        ).model_dump(),
    ]
    publish(manager, "get_all_house_configs", houses)
    form.add_house_row()
    first, second = form.house_rows
    first[0].setCurrentIndex(first[0].findData(houses[0]["house_config_id"]))
    second[0].setCurrentIndex(second[0].findData("no-solar"))
    second[1].setValue(12)
    assert "Air conditioning: Yes" in first[2].findChild(ConfigSummary).summary.text()
    assert "Air conditioning: No" in second[2].findChild(ConfigSummary).summary.text()
    form.remove_house_row(first[2])
    assert form.house_rows[0][1].value() == 12
    publish(manager, "get_all_house_configs", [])
    assert "Choose a configuration" in second[2].findChild(ConfigSummary).summary.text()
    form.close()


def test_battery_editor_updates_wear_and_does_not_submit_derived_field():
    manager = FakeManager()
    gateway = FakeGateway()
    page = setup_page(manager, gateway)
    page.resize(1024, 680)
    page.show()
    page.navigation.setCurrentRow(3)
    record = battery_record()
    publish(manager, "get_all_battery_configs", [record])
    APP.processEvents()
    editor = page.findChild(BatteryFormView)
    assert "2.5 c/kWh throughput" in editor.wear.rate.text()
    editor._picker.setEditText("new-battery")
    editor._inputs["replacement_cost_aud"].setValue(154200)
    assert "5 c/kWh throughput" in editor.wear.rate.text()
    editor._inputs["lifetime_cycles"].setValue(0)
    assert "unavailable" in editor.wear.rate.text()
    editor._inputs["lifetime_cycles"].setValue(6000)
    editor._apply.click()
    saved = manager.writes[-1]["set_battery_config"][0]
    assert saved["replacement_cost_aud"] == 154200
    assert "degradation_aud_per_kwh" not in saved
    assert not page.sections.widget(3).widget().findChildren(QScrollArea)
    editor.wear.explain.click()
    assert not editor.wear.explanation.isHidden()
    page.close()


def test_house_and_battery_choices_use_natural_numeric_order():
    manager = FakeManager()
    form = SimulationForm(manager, FakeGateway())
    houses = [
        HouseConfigFactory.build(house_config_id=f"house-{number}--solar").model_dump()
        for number in (10, 2, 1)
    ]
    batteries = [
        battery_record(battery_config_id=f"battery-{number}") for number in (10, 2, 1)
    ]
    publish(manager, "get_all_house_configs", houses)
    publish(manager, "get_all_battery_configs", batteries)
    house_combo = form.house_rows[0][0]
    assert [house_combo.itemData(i) for i in range(3)] == [
        "house-1--solar",
        "house-2--solar",
        "house-10--solar",
    ]
    assert [form.battery_combo.itemData(i) for i in range(3)] == [
        "battery-1",
        "battery-2",
        "battery-10",
    ]
    house_combo.setCurrentIndex(house_combo.findData("house-2--solar"))
    publish(manager, "get_all_house_configs", list(reversed(houses)))
    assert house_combo.currentData() == "house-2--solar"
    assert house_combo.currentText().startswith("House 2 · ")
    form.close()


def test_readable_labels_do_not_change_simulation_ids_or_custom_names():
    manager = FakeManager()
    gateway = FakeGateway()
    started = []
    gateway.start_simulation = started.append
    form = SimulationForm(manager, gateway)
    house = HouseConfigFactory.build(
        house_config_id="house-2--6.6KW-5KW-INV", pv_peak_capacity=6.6
    ).model_dump()
    battery = battery_record(
        battery_config_id="regional-5mw", capacity_kwh=10000, max_power_kw=5000
    )
    publish(manager, "get_all_house_configs", [house])
    publish(manager, "get_all_battery_configs", [battery])
    combo, quantity, _ = form.house_rows[0]
    quantity.setValue(3)
    assert combo.currentText() == "House 2 · 6.6 kW solar · 5 kW inverter"
    assert form.battery_combo.currentText() == "Regional battery · 10 MWh · 5 MW"
    form.on_run()
    assert started[0]["houses"] == {house["house_config_id"]: 3}
    assert started[0]["battery_config_id"] == "regional-5mw"
    custom = {**house, "house_config_id": "My EV home"}
    publish(manager, "get_all_house_configs", [custom])
    assert combo.currentText().startswith("My EV home · ")
    assert combo.currentData() == "My EV home"
    form.close()


def test_previous_runs_show_on_startup_without_a_selected_run():
    from frontend_v2.ui.views.choice_view import ChoiceView
    from tests.factories import LocationFactory, SimulationRequestFactory

    manager = FakeManager()
    battery = battery_record()
    request = SimulationRequestFactory.build(
        location_name="Adelaide", battery_config_id=battery["battery_config_id"]
    )
    run = {
        **request.model_dump(),
        "house_count": 1,
        "objective_mode": request.objective_mode.value,
        "start_time_utc": "2025-12-31T13:30:00+00:00",
        "end_time_utc": "2026-01-01T13:30:00+00:00",
    }
    publish(manager, "get_all_battery_configs", [battery])
    publish(
        manager,
        "get_all_locations",
        [
            LocationFactory.build(
                location_name="Adelaide", timezone="Australia/Adelaide"
            ).model_dump()
        ],
    )
    publish(manager, "get_all_simulation_requests", [run])
    publish(manager, "get_current_request", [])
    page = setup_page(manager, FakeGateway())
    page.resize(1024, 680)
    page.show()
    page.navigation.setCurrentRow(1)
    manager.updated_keys.emit(set(manager.values))
    for _ in range(10):
        APP.processEvents()
    view = page.sections.widget(1).findChild(ChoiceView)
    assert view._list.count() == 1
    assert view._body.currentWidget() is view._content
    assert view._current.text() == "None in use yet."
    assert "01 Jan 2026" in view._list.item(0).text()
    assert "02 Jan 2026" in view._list.item(0).text()
    assert "1 house · " in view._list.item(0).text()
    assert f"Run {request.request_id[:8]}" in view._list.item(0).text()
    assert not manager.writes
    view._list.setCurrentRow(0)
    view._submit()
    assert manager.writes[-1] == {
        "set_current_request": [{"request_id": request.request_id}]
    }
    page.close()
