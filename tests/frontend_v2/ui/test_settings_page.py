"""Settings navigation preserves editors and gives each section one scroll owner."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea, QTabWidget, QWidget

from database_v2.data_access_objects.frontend_dao import FrontendDao
from frontend_v2.sources.replay_clock import ReplayClock
from frontend_v2.ui.pages.setup.cache_controls import CacheControls
from frontend_v2.ui.pages.setup.location_editor import ADD_NEW_TEXT, LocationEditor
from frontend_v2.ui.pages.setup.setup import setup_page
from frontend_v2.ui.pages.setup.simulation_form import SimulationForm
from frontend_v2.ui.views.form_view import FormView
from frontend_v2.utils.payload import RecordsPayload
from frontend_v2.utils.result import Result

APP = QApplication.instance() or QApplication([])


class FakeManager(QObject):
    updated_keys = pyqtSignal(set)
    result = pyqtSignal(Result)

    def __init__(self):
        super().__init__()
        self.replay_clock = ReplayClock()
        self.writes = []
        self.values = {
            key: np.array([], dtype=getattr(FrontendDao, key).dtype)
            for key in (
                "get_all_locations",
                "get_all_battery_configs",
                "get_all_house_configs",
            )
        }

    def get(self, key):
        return self.values.get(key)

    def set(self, form):
        self.writes.append(form)
        return len(self.writes)


class FakeGateway(QObject):
    simulation_state_changed = pyqtSignal(bool)
    is_running = False
    db_path = "unused.db"
    simulation_progress = pyqtSignal(str)
    simulation_cancelled = pyqtSignal(str)
    simulation_finished = pyqtSignal(str)
    simulation_error = pyqtSignal(str)


def test_sections_have_one_scroll_owner_and_preserve_editors(monkeypatch, tmp_path):
    from backend_v2.aemo import cache_storage

    monkeypatch.setattr(cache_storage, "AEMO_CACHE_DIR", tmp_path / "aemo")
    manager = FakeManager()
    gateway = FakeGateway()
    page = setup_page(manager, gateway)
    tabs = QTabWidget()
    tabs.addTab(page, "Settings")
    tabs.addTab(QWidget(), "Other")
    tabs.resize(1024, 680)
    tabs.show()
    APP.processEvents()
    assert [page.navigation.item(i).text() for i in range(6)] == [
        "Run simulation",
        "Previous runs",
        "Locations",
        "Battery configs",
        "House configs",
        "Storage",
    ]
    assert page.sections.currentIndex() == 0
    page.navigation.setFocus()
    QTest.keyClick(page.navigation, Qt.Key.Key_Down)
    assert page.sections.currentIndex() == 1
    page.navigation.setCurrentRow(0)
    simulation = page.findChild(SimulationForm)
    simulation.duration_spin.setValue(3)
    for index in range(6):
        page.navigation.setCurrentRow(index)
        APP.processEvents()
        section = page.sections.currentWidget()
        assert isinstance(section, QScrollArea)
        assert not section.widget().findChildren(QScrollArea)
    battery = page.sections.widget(3).findChild(FormView)
    battery.show_payload(RecordsPayload(records={}))
    battery._picker.setEditText("draft-battery")
    first = next(iter(battery._inputs.values()))
    first.setValue(25)
    page.navigation.setCurrentRow(3)
    tabs.setCurrentIndex(1)
    tabs.setCurrentIndex(0)
    APP.processEvents()
    assert page.sections.currentIndex() == 3
    assert battery._picker.currentText() == "draft-battery"
    assert first.value() == 25
    assert battery._content.objectName() == "form"
    assert simulation.duration_spin.value() == 3
    battery._apply.click()
    assert (
        manager.writes[-1]["set_battery_config"][0]["battery_config_id"]
        == "draft-battery"
    )
    page.findChild(CacheControls).manager.close()
    APP.processEvents()
    tabs.close()


def test_location_draft_survives_choice_refresh():
    manager = FakeManager()
    editor = LocationEditor(manager)
    editor.inputs["location_name"].setText("Unfinished location")
    manager.updated_keys.emit({"get_all_locations"})
    assert editor.selector_combo.currentText() == ADD_NEW_TEXT
    assert editor.inputs["location_name"].text() == "Unfinished location"
    editor.close()


def test_operation_keeps_controls_outside_chart_scrolling():
    from frontend_v2.ui.grid import Grid
    from frontend_v2.ui.pages.operation_page import operation_page
    from frontend_v2.ui.views.replay_bar import ReplayBarView

    manager = FakeManager()
    page = operation_page(manager)
    page.resize(1400, 850)
    page.show()
    APP.processEvents()
    scrolls = page.findChildren(QScrollArea)
    assert len(scrolls) == 1
    charts = scrolls[0].widget()
    assert charts.n_cols == 2
    assert [i.controller.id for i in charts.items] == [
        "community_energy_balance",
        "storage_and_curtailment",
        "plan_vs_live_battery_action",
        "community_battery_soc_history",
        "predicted_community_pv",
        "predicted_buy_and_sell_prices",
    ]
    assert not charts.findChildren(ReplayBarView)
    controls = page.findChild(ReplayBarView)
    assert controls.isVisible()
    scrolls[0].verticalScrollBar().setValue(1000)
    assert controls.isVisible()
    page.resize(1024, 680)
    APP.processEvents()
    assert charts.n_cols == 1
    assert len(page.findChildren(Grid)) == 3
    assert controls.geometry().height() > 0
    page.close()


def test_compact_duration_choice_uses_existing_submission_contract():
    from frontend_v2.ui.views.choice_view import ChoiceView
    from frontend_v2.utils.payload import ChoicesPayload

    view = ChoiceView(
        "Chart duration", "", "", setting_key="time_duration", compact=True
    )
    submitted = []
    view.submitted.connect(submitted.append)
    view.show_payload(
        ChoicesPayload(
            choices={"full": "Whole run", "24h": "Last 24 hours"}, current="full"
        )
    )
    assert submitted == []
    assert view._picker.currentData() == "full"
    view._picker.setCurrentIndex(1)
    view._picker.activated.emit(1)
    assert submitted == [{"key": "time_duration", "value": "24h"}]
    view.close()
