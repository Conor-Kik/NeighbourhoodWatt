from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QMessageBox

import tests.integration.integration_helper_functions as helper_functions
from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.db_constants import DB_PATH
from database_v2.models.optimiser_models import Solver
from frontend_v2.engine.database_reset import apply_pending_database_reset
from frontend_v2.main import MainWindow
from frontend_v2.ui.grid import Grid
from frontend_v2.ui.pages.setup.setup import SettingsPage
from frontend_v2.ui.views.form_view import FormView


## FIXTURES ############################################################################
@pytest.fixture(autouse=True)
def fix_qt_paths():
    correct_path = str(Path(__file__).resolve().parent.parent.parent / "src")
    QCoreApplication.addLibraryPath(correct_path)


@pytest.fixture
def window(qtbot):
    try:
        apply_pending_database_reset(DB_PATH)
    except Exception:
        pass

    window = MainWindow()
    qtbot.addWidget(window)
    with qtbot.waitExposed(window):
        window.show()

    yield window


## TESTS FUNCTIONS #####################################################################
#### REST THE DATABASE #################################################################
def test_resetting_database(qtbot, window: MainWindow, monkeypatch):
    """
    Reset the database from the GUI.
    """
    settings_tab = window.tabs.widget(0)
    storage_page = settings_tab.findChild(Grid, "setup-storage")
    cached_controls = storage_page.items[-1].controller.view._content

    # batch the confirm reset to just always reset instead of throwing a message box
    # warning.
    error_popup_appeared = False

    def mock_error_popup(parent, title, text, *args, **kwargs):
        nonlocal error_popup_appeared
        error_popup_appeared = True
        return QMessageBox.StandardButton.Yes  # simulates the user clicking yes.

    monkeypatch.setattr(QMessageBox, "warning", mock_error_popup)

    # Click the button
    qtbot.mouseClick(cached_controls.reset_button, Qt.MouseButton.LeftButton)

    # Confirm popup appeared
    qtbot.waitUntil(lambda: error_popup_appeared)


#### SIMULATION RUNNING TESTING ########################################################
def test_run_default_simulation_results_showing(qtbot, window: MainWindow):
    """
    Runs a simulations with the default settings and asserts that the operations,
    community and debrief tabs are all initially empty (i.e. nothing is being
    displayed). Then after running the simulation check that all of the results are
    being displayed on the tabs.
    """

    # check to make sure that the operations, community and debrief tab are initially
    # empty (i.e. they are not displaying information from a previous run).
    # assert that the operations tab is empty.
    helper_functions.switch_tab(qtbot, window.tabs, 1)
    operations_tab = window.tabs.widget(1)
    operations_summary_grid = operations_tab.findChild(Grid, "operation-summary")
    operations_charts_grid = operations_tab.findChild(Grid, "operation-charts")
    assert helper_functions.all_grids_are_empty(
        [operations_summary_grid, operations_charts_grid]
    )

    # assert that the community tab is empty.
    helper_functions.switch_tab(qtbot, window.tabs, 2)
    community_tab = window.tabs.widget(2)
    community_summary_grid = community_tab.findChild(Grid, "community-summary")
    community_charts_grid = community_tab.findChild(Grid, "community-charts")
    assert helper_functions.all_grids_are_empty(
        [community_summary_grid, community_charts_grid]
    )

    # assert that the debrief tab is empty.
    helper_functions.switch_tab(qtbot, window.tabs, 3)
    debrief_tab = window.tabs.widget(3).widget()
    debrief_summary_grid = debrief_tab.findChild(Grid, "debrief-summary")
    debrief_totals_grid = debrief_tab.findChild(Grid, "debrief-totals")
    assert helper_functions.all_grids_are_empty(
        [debrief_summary_grid, debrief_totals_grid]
    )

    helper_functions.switch_tab(qtbot, window.tabs, 0)
    settings_tab = window.tabs.widget(0)
    run_simulation_tab = settings_tab.findChild(Grid, "setup-simulation")
    simulation_form = run_simulation_tab.items[0].controller.view._content

    # ensure that this simulation has not already been run.
    request = helper_functions.get_simulation_request(simulation_form)
    simulation_dao = SimulationDao(DB_PATH, request)
    assert not simulation_dao.run_exists()

    # now we start the simulation.
    qtbot.mouseClick(simulation_form.run_btn, Qt.MouseButton.LeftButton)

    # wait until the simulation finishes.
    qtbot.waitUntil(
        lambda: simulation_form.status_label.text() == "Simulation finished.",
        timeout=300_000,  # ensure this simulation must take less than 5mins.
    )

    helper_functions.switch_tab(qtbot, window.tabs, 1)
    qtbot.waitUntil(
        lambda: helper_functions.all_grids_are_non_empty(
            [operations_summary_grid, operations_charts_grid]
        )
    )

    helper_functions.switch_tab(qtbot, window.tabs, 2)
    qtbot.waitUntil(
        lambda: helper_functions.all_grids_are_non_empty(
            [community_summary_grid, community_charts_grid]
        )
    )

    helper_functions.switch_tab(qtbot, window.tabs, 3)
    qtbot.waitUntil(
        lambda: helper_functions.all_grids_are_non_empty(
            [debrief_summary_grid, debrief_totals_grid]
        )
    )

    # make sure the simulation results have been added to the database.
    assert simulation_dao.run_exists()


def test_run_repeated_simulation_using_cached_results(qtbot, window: MainWindow):
    """
    Run the same simulation as the previous test (see above) and assert that it uses
    the cached result from the previous run.
    """
    # wait until all of the defaulted values are filled into the comboboxes in the
    # simulation form.
    settings_tab = window.tabs.widget(0)
    run_simulation_tab = settings_tab.findChild(Grid, "setup-simulation")
    simulation_form = run_simulation_tab.items[0].controller.view._content

    # check to make sure the simulation that we are trying to run is already in the
    # database.
    request = helper_functions.get_simulation_request(simulation_form)
    simulation_dao = SimulationDao(DB_PATH, request)
    assert simulation_dao.run_exists()

    # now we start the simulation.
    qtbot.mouseClick(simulation_form.run_btn, Qt.MouseButton.LeftButton)
    # wait until the simulation finishes.
    qtbot.waitUntil(
        lambda: simulation_form.status_label.text() == "Simulation finished.",
        timeout=1_000,  # assert that the simulation must take less than a second.
    )


def test_simulation_using_highs(qtbot, window: MainWindow, monkeypatch):
    """
    Test a simulation using the HIGHS optimiser instead of Gurobi.
    """
    # wait until all of the defaulted values are filled into the comboboxes in the
    # simulation form.
    settings_tab = window.tabs.widget(0)
    run_simulation_tab = settings_tab.findChild(Grid, "setup-simulation")
    simulation_form = run_simulation_tab.items[0].controller.view._content

    # change the start date to 2 to get a different simulation (from what we have run
    # before).
    helper_functions.fill_in_combobox(qtbot, simulation_form.date_day_combo, "2")

    # get the request for the simulation we are trying to run.
    request = helper_functions.get_simulation_request(simulation_form)
    simulation_dao = SimulationDao(DB_PATH, request)
    # assert that this simulation has not been run before we we know we are not just
    # going be be using cached results from a simulation with a gurobi license.
    assert not simulation_dao.run_exists()

    # now we patch the initialisation of the OptimiserRunner so that we always use
    # the HIGHS solver (not matter the inputted Solver).
    original_optimiser_runner_init = OptimiserRunner.__init__

    def mock_optimiser_runner_init(
        self, solver_name: Solver = Solver.GUROBI, tee: bool = False
    ) -> None:
        """Same as before but now we hard-code it to use the HIGHS optimiser."""
        return original_optimiser_runner_init(self, Solver.HIGHS, tee)

    monkeypatch.setattr(OptimiserRunner, "__init__", mock_optimiser_runner_init)

    # now we start the simulation.
    qtbot.mouseClick(simulation_form.run_btn, Qt.MouseButton.LeftButton)
    # wait until the simulation finishes.
    qtbot.waitUntil(
        lambda: simulation_form.status_label.text() == "Simulation finished.",
        timeout=300_000,  # assert that the simulation must up to 5mins.
    )

    # ensure the simulation results have been added back into the database.
    qtbot.waitUntil(lambda: simulation_dao.run_exists())


#### LOCATION INPUT TESTING ############################################################
def test_adding_valid_location_to_database(qtbot, window: MainWindow):
    """
    Try adding a new valid location (Canberra) to to the database using the locations
    tab and then checking that its is added to the database.
    """
    settings_tab = window.tabs.widget(0)
    locations_tab = settings_tab.findChild(Grid, "setup-locations")
    locations_editor = locations_tab.items[0].controller.view._content

    # fill in the form with the details to add in Canberra as a new location.
    helper_functions.fill_in_location_tab(
        qtbot,
        locations_editor,
        location_name="Canberra",
        latitude="-36.282001",
        longitude="148.128998",
        state="ACT",
    )
    # check to make sure the location is not currently in the database.
    location_dao = LocationDao(DB_PATH)
    assert location_dao.get_location("Canberra") is None

    save_button = locations_editor.save_btn
    assert save_button.isEnabled()
    qtbot.mouseClick(save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: save_button.isEnabled())

    # check to make sure the new location is saved into the database.
    qtbot.waitUntil(lambda: location_dao.get_location("Canberra") is not None)


def test_adding_invalid_latitude_to_location(qtbot, window: MainWindow, monkeypatch):
    """
    Try adding an location with an invalid latitude to the database.
    Check to make sure no location were added to the database.
    """
    settings_tab = window.tabs.widget(0)
    locations_tab = settings_tab.findChild(Grid, "setup-locations")
    locations_editor = locations_tab.items[0].controller.view._content

    # fill in the location form with a location with an invalid latitude.
    helper_functions.fill_in_location_tab(
        qtbot,
        locations_editor,
        location_name="Unknown",
        latitude="not a number",
        longitude="0.0",
        state="QLD",
    )

    error_popup_appeared = False
    error_popup_info = {}

    def mock_error_popup(parent, title, text, *args, **kwargs):
        nonlocal error_popup_appeared, error_popup_info
        error_popup_appeared = True
        error_popup_info["title"] = title
        error_popup_info["text"] = text
        return QMessageBox.StandardButton.Ok  # simulates the user clicking Ok.

    monkeypatch.setattr(QMessageBox, "critical", mock_error_popup)

    save_button = locations_editor.save_btn
    assert save_button.isEnabled()
    qtbot.mouseClick(save_button, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: error_popup_appeared)
    assert error_popup_info["title"] == "Error Saving"
    assert error_popup_info["text"].startswith("Failed to save:\n")

    # check to make sure the invalid location is not saved into the database.
    location_dao = LocationDao(DB_PATH)
    unknown_location = location_dao.get_location("Unknown")
    assert unknown_location is None


def test_adding_invalid_longitude_to_location(qtbot, window: MainWindow, monkeypatch):
    """
    Try adding an location with an invalid longitude to the database.
    Check to make sure no location were added to the database.
    """
    settings_tab = window.tabs.widget(0)
    locations_tab = settings_tab.findChild(Grid, "setup-locations")
    locations_editor = locations_tab.items[0].controller.view._content

    # fill in the location form with a location with an invalid latitude.
    helper_functions.fill_in_location_tab(
        qtbot,
        locations_editor,
        location_name="Unknown",
        latitude="0.0",
        longitude="not a number",
        state="QLD",
    )

    error_popup_appeared = False
    error_popup_info = {}

    def mock_error_popup(parent, title, text, *args, **kwargs):
        nonlocal error_popup_appeared, error_popup_info
        error_popup_appeared = True
        error_popup_info["title"] = title
        error_popup_info["text"] = text
        return QMessageBox.StandardButton.Ok  # simulates the user clicking Ok.

    monkeypatch.setattr(QMessageBox, "critical", mock_error_popup)

    save_button = locations_editor.save_btn
    assert save_button.isEnabled()
    qtbot.mouseClick(save_button, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: error_popup_appeared)
    assert error_popup_info["title"] == "Error Saving"
    assert error_popup_info["text"].startswith("Failed to save:\n")

    # check to make sure the invalid location is not saved into the database.
    location_dao = LocationDao(DB_PATH)
    unknown_location = location_dao.get_location("Unknown")
    assert unknown_location is None


#### BATTERY INPUT TESTING #############################################################
def test_adding_valid_battery(qtbot, window: MainWindow):
    """
    add a valid battery configuration to the database.
    check that the new battery does indeed appear in the database.
    """
    settings_page: SettingsPage = window.tabs.widget(0)
    batteries_grid = settings_page.findChild(Grid, "setup-battery_configs")
    batteries_config_item = settings_page.navigation.item(3)

    # switch to the batters config option.
    qtbot.mouseClick(
        settings_page.navigation.viewport(),
        Qt.MouseButton.LeftButton,
        pos=settings_page.navigation.visualItemRect(batteries_config_item).center(),
    )
    qtbot.waitUntil(
        lambda: settings_page.navigation.currentItem() == batteries_config_item
    )

    battery_view = batteries_grid.items[0].controller.view
    assert isinstance(battery_view, FormView)

    # fill in the form with the details to add in a new valid battery
    helper_functions.fill_in_battery_view(
        qtbot,
        battery_view,
        id="some valid battery",
        capacity_kwh=1.0,
        max_power_kw=1.0,
        efficiency=1.0,
        min_soc_frac=1.0,
        max_soc_frac=1.0,
        replacement_cost_aud=1.0,
        lifetime_cycles=1,
        max_cycles_24h=1,
    )

    # ensure this battery is not already in the database.
    battery_dao = BatteryDao(DB_PATH)
    assert battery_dao.get_battery_config("some valid battery") is None

    # save the battery to the database.
    save_button = battery_view._apply
    assert save_button.isEnabled()
    qtbot.mouseClick(save_button, Qt.MouseButton.LeftButton)

    # check to make sure the new battery is saved into the database.
    qtbot.waitUntil(
        lambda: battery_dao.get_battery_config("some valid battery") is not None
    )


###### HOUSE CONFIG TESTING ############################################################
def test_adding_valid_house_config(qtbot, window: MainWindow):
    """
    add a valid house configuration to the database.
    check that the new house config does indeed appear in the database.
    """
    settings_page: SettingsPage = window.tabs.widget(0)
    house_config_grid = settings_page.findChild(Grid, "setup-house_configs")
    house_config_item = settings_page.navigation.item(4)

    # switch to the house config option.
    qtbot.mouseClick(
        settings_page.navigation.viewport(),
        Qt.MouseButton.LeftButton,
        pos=settings_page.navigation.visualItemRect(house_config_item).center(),
    )
    qtbot.waitUntil(lambda: settings_page.navigation.currentItem() == house_config_item)

    house_view = house_config_grid.items[0].controller.view
    assert isinstance(house_view, FormView)

    # fill in the form with the details to add in a new valid house config.
    helper_functions.fill_in_house_config_tab(
        qtbot,
        house_view,
        id="some valid house config",
        pv_peak_capacity=1.0,
        panel_tilt=1.0,
        panel_azimuth=1.0,
        pv_inverter_ac_kw=1.0,
        pv_inverter_efficiency=1.0,
        has_aircon=True,
        has_poolpump=False,
        has_ev=False,
    )

    # ensure this house config is not already in the database.
    house_config_dao = HouseConfigDao(DB_PATH)
    assert house_config_dao.get_house_config("some valid house config") is None

    # save the house config to the database.
    save_button = house_view._apply
    assert save_button.isEnabled()
    qtbot.mouseClick(save_button, Qt.MouseButton.LeftButton)

    # check to make sure the new house config is saved into the database.
    qtbot.waitUntil(
        lambda: house_config_dao.get_house_config("some valid house config") is not None
    )
