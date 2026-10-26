""" """

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox

from database_v2.models.request_models import SimulationRequest
from frontend_v2.ui.grid import Grid
from frontend_v2.ui.pages.setup.simulation_form import SimulationForm
from frontend_v2.ui.views.form_view import FormView


def entire_grid_is_empty(grid: Grid) -> bool:
    """
    True if al items in the grid are displaying the empty message.
    False otherwise.
    """
    return all(item.controller.view.is_empty() for item in grid.items)


def entire_grid_is_non_empty(grid: Grid) -> bool:
    """
    True if al items in the grid are not displaying the empty message.
    False otherwise.
    """
    return all(not item.controller.view.is_empty() for item in grid.items)


def all_grids_are_empty(tabs: list[Grid]):
    """
    True if all girds in the tabs are all empty.
    False otherwise.
    """
    return all(entire_grid_is_empty(grid) for grid in tabs)


def all_grids_are_non_empty(tabs: list[Grid]):
    """
    True if all the grids in the tabs are all non-empty.
    False otherwise.
    """
    return all(entire_grid_is_non_empty(grid) for grid in tabs)


def switch_tab(qtbot, tabs, idx: int) -> None:
    """Switch the tab to the given tab index using qtbot."""
    tab_bar = tabs.tabBar()

    qtbot.mouseClick(
        tab_bar, Qt.MouseButton.LeftButton, pos=tab_bar.tabRect(idx).center()
    )
    qtbot.waitUntil(lambda: tabs.currentIndex() == idx)

    return None


def fill_in_double_spin_box(qtbot, widget, value: float) -> None:
    """Fill in the text value widget with the given QDoubleSpinBox using qtbot."""
    clear_text_in_widget(qtbot, widget)
    qtbot.keyClicks(widget, str(value))
    qtbot.keyClick(widget, Qt.Key.Key_Enter)

    qtbot.waitUntil(lambda: widget.value() == value)

    return None


def fill_text_to_widget(qtbot, widget, text: str) -> None:
    """Fill in the text widget with the given text using qtbot."""
    qtbot.keyClicks(widget, text)
    qtbot.waitUntil(
        lambda: widget.currentText() == text
        if hasattr(widget, "currentText")
        else widget.text() == text
    )

    return None


def clear_text_in_widget(qtbot, widget) -> None:
    """Clear the text in the current widget using qtbot."""
    qtbot.keyClick(widget, Qt.Key.Key_A, modifier=Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(widget, Qt.Key.Key_Delete)

    return None


def fill_in_combobox(qtbot, combo_box: QComboBox, option: str) -> None:
    """Choose the given option in the combo box using qtbot."""
    qtbot.keyClicks(combo_box, option)
    qtbot.keyClick(combo_box, Qt.Key.Key_Enter)
    qtbot.waitUntil(lambda: combo_box.currentText() == option)

    return None


def check_box(qtbot, check_box: QCheckBox, check: bool) -> None:
    """
    Check the given checkbox if check if True and otherwise do nothing using qtbot.
    """
    if check:
        qtbot.mouseClick(check_box, Qt.MouseButton.LeftButton)

    return None


def get_simulation_request(simulation_form: SimulationForm) -> SimulationRequest:
    """
    Read in the inputs from the simulation from and create a simulation request with
    the corresponding inputs that have been given in the form using qtbot.
    """
    simulation_kwargs = simulation_form.get_simulation_kwargs()
    return SimulationRequest(
        objective_mode=simulation_kwargs["objective_mode"],
        start_time_utc=simulation_kwargs["start_time_utc"],
        end_time_utc=simulation_kwargs["end_time_utc"],
        location_name=simulation_kwargs["location_name"],
        battery_config_id=simulation_kwargs["battery_config_id"],
        houses=simulation_kwargs["houses"],
        fixed_buy_aud_per_kwh=simulation_kwargs["fixed_buy_aud_per_kwh"],
        fixed_sell_aud_per_kwh=simulation_kwargs["fixed_sell_aud_per_kwh"],
    )


def fill_in_location_tab(
    qtbot,
    location_tab,
    location_name: str,
    latitude: str,
    longitude: str,
    state: str,
) -> None:
    """
    Fill in the locations form or tab in the GUI with the given inputs using qtbot.
    """
    inputs = location_tab.inputs

    fill_text_to_widget(qtbot, inputs["location_name"], location_name)
    fill_text_to_widget(qtbot, inputs["latitude"], latitude)
    fill_text_to_widget(qtbot, inputs["longitude"], longitude)
    fill_in_combobox(qtbot, location_tab.state_combo, state)

    return None


def fill_in_battery_view(
    qtbot,
    batteries_view: FormView,
    id: str,
    capacity_kwh: float,
    max_power_kw: float,
    efficiency: float,
    min_soc_frac: float,
    max_soc_frac: float,
    replacement_cost_aud: float,
    lifetime_cycles: float,
    max_cycles_24h: float,
) -> None:
    """
    Fill in the battery form or tab in the GUI with the given inputs using qtbot.
    """
    # press the create new button
    qtbot.mouseClick(batteries_view._create, Qt.MouseButton.LeftButton)
    fill_text_to_widget(qtbot, batteries_view._picker, id)

    inputs = batteries_view._inputs

    fill_in_double_spin_box(qtbot, inputs["capacity_kwh"], capacity_kwh)
    fill_in_double_spin_box(qtbot, inputs["max_power_kw"], max_power_kw)
    fill_in_double_spin_box(qtbot, inputs["efficiency"], efficiency)
    fill_in_double_spin_box(qtbot, inputs["min_soc_frac"], min_soc_frac)
    fill_in_double_spin_box(qtbot, inputs["max_soc_frac"], max_soc_frac)
    fill_in_double_spin_box(qtbot, inputs["replacement_cost_aud"], replacement_cost_aud)
    fill_in_double_spin_box(qtbot, inputs["lifetime_cycles"], lifetime_cycles)
    fill_in_double_spin_box(qtbot, inputs["max_cycles_24h"], max_cycles_24h)

    return None


def fill_in_house_config_tab(
    qtbot,
    house_view: FormView,
    id: str,
    pv_peak_capacity: float,
    panel_tilt: float,
    panel_azimuth: float,
    pv_inverter_ac_kw: float,
    pv_inverter_efficiency: float,
    has_aircon: bool,
    has_poolpump: bool,
    has_ev: bool,
) -> None:
    """
    Fill in the house config for or tab with the given inputs using qtbot.
    """
    # press the create new button
    qtbot.mouseClick(house_view._create, Qt.MouseButton.LeftButton)
    fill_text_to_widget(qtbot, house_view._picker, id)

    inputs = house_view._inputs

    fill_in_double_spin_box(qtbot, inputs["pv_peak_capacity"], pv_peak_capacity)
    fill_in_double_spin_box(qtbot, inputs["panel_tilt"], panel_tilt)
    fill_in_double_spin_box(qtbot, inputs["panel_azimuth"], panel_azimuth)
    fill_in_double_spin_box(qtbot, inputs["pv_inverter_ac_kw"], pv_inverter_ac_kw)
    fill_in_double_spin_box(
        qtbot, inputs["pv_inverter_efficiency"], pv_inverter_efficiency
    )

    assert isinstance(inputs["has_aircon"], QCheckBox)
    assert isinstance(inputs["has_poolpump"], QCheckBox)
    assert isinstance(inputs["has_ev"], QCheckBox)
    check_box(qtbot, inputs["has_aircon"], has_aircon)
    check_box(qtbot, inputs["has_poolpump"], has_poolpump)
    check_box(qtbot, inputs["has_ev"], has_ev)

    return None


def clear_tab(
    qtbot,
    tab,
) -> None:
    """
    Clear all of the inputs in the form / tab to reset the text in them to all be
    empty string using qtbot.
    """
    for widget in tab.inputs.values():
        clear_text_in_widget(qtbot, widget)

    return None
