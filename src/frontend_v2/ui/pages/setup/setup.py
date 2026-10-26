"""Settings navigation and persistent editors for simulations and configs."""

from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QStackedWidget, QWidget

from frontend_v2.bindings.bindings import ReadBinding, WriteBinding
from frontend_v2.bindings.selectors import (
    select_battery_config_records,
    select_house_config_records,
    select_request_choices,
)
from frontend_v2.bindings.selectors.records import battery_wear_rate
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.engine.optimiser_gateway import OptimiserGateway
from frontend_v2.ui.form_fields import HOUSE_CONFIG_FIELDS
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.pages.setup.cache_controls import CacheControls
from frontend_v2.ui.pages.setup.location_editor import LocationEditor
from frontend_v2.ui.pages.setup.simulation_form import SimulationForm
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.scroll_area import scrollable_page
from frontend_v2.ui.views.battery_form_view import BatteryFormView
from frontend_v2.ui.views.choice_view import ChoiceView
from frontend_v2.ui.views.form_view import FormView
from frontend_v2.ui.views.widget_view import WidgetView

NO_CONFIGS_TEXT = "No configs saved yet."
NO_RUNS_TEXT = "No saved runs yet."


def setup_page(
    data_manager: DataManager, optimiser_gateway: OptimiserGateway
) -> QWidget:
    """Build the Settings page."""
    simulation = PanelController(
        id="simulation",
        view=WidgetView(
            SimulationForm(data_manager, optimiser_gateway),
            title="Run simulation",
            help_text="Cost minimises import cost minus export revenue plus modelled "
            "battery wear. Emissions minimises grid-import emissions. "
            "Balanced combines financial and carbon-priced emissions costs; "
            "it is not a fixed 50/50 split. The start date is local to the "
            "location. House quantities multiply each configuration's demand "
            "and solar output. Baseline tariffs apply only to no-battery "
            "comparisons. Run Simulation creates a run; Previous runs opens "
            "saved results.",
        ),
    )
    locations = PanelController(
        id="locations",
        view=WidgetView(
            LocationEditor(data_manager),
            title="Locations",
            help_text="A location sets weather, local time and electricity region. "
            "Choose Create new, enter a postcode and choose its suburb "
            "to fill approximate coordinates, or enter the location manually. "
            "Southern latitudes are negative. State supplies the timezone and "
            "electricity region; saved locations are read-only.",
        ),
    )
    battery_view = BatteryFormView()
    battery_view.values_changed.connect(
        lambda values: battery_view.show_wear_rate(battery_wear_rate(values))
    )
    batteries = PanelController(
        id="battery_configs",
        view=battery_view,
        read=ReadBinding(select_battery_config_records),
        write=WriteBinding(data_manager, "set_battery_config"),
    )
    houses = PanelController(
        id="house_configs",
        view=FormView(
            title="House configs",
            help_text="Values describe one house; set the number of houses in Run "
            "simulation. Solar panel capacity can exceed inverter size, which "
            "limits AC output. Equipment choices affect the demand profile. "
            "Saved configs are read-only; choose Create new, enter a unique "
            "name, adjust the copied values and save.",
            empty_text=NO_CONFIGS_TEXT,
            id_key="house_config_id",
            id_label="House config",
            fields=HOUSE_CONFIG_FIELDS,
            existing_read_only=True,
            scrollable=False,
        ),
        read=ReadBinding(select_house_config_records),
        write=WriteBinding(data_manager, "set_house_config"),
    )
    previous_runs = PanelController(
        id="previous_runs",
        view=ChoiceView(
            title="Previous runs",
            help_text="Select a row and choose Use selected, or double-click it, to "
            "load that run on Operation, Community and Debrief. This opens saved "
            "results without rerunning the simulation. Dates use the location’s "
            "local time; the short run reference distinguishes similar entries.",
            empty_text=NO_RUNS_TEXT,
            value_key="request_id",
        ),
        read=ReadBinding(
            select_request_choices, optional=frozenset({"get_current_request"})
        ),
        write=WriteBinding(data_manager, "set_current_request"),
    )
    previous_runs.view.setDisabled(optimiser_gateway.is_running)
    optimiser_gateway.simulation_state_changed.connect(previous_runs.view.setDisabled)

    return SettingsPage(
        data_manager,
        [
            ("Run simulation", simulation),
            ("Previous runs", previous_runs),
            ("Locations", locations),
            ("Battery configs", batteries),
            ("House configs", houses),
            (
                "Storage",
                PanelController(
                    id="storage",
                    view=WidgetView(
                        CacheControls(optimiser_gateway),
                        title="Storage",
                        help_text="Refresh size shows the space used by the database "
                        "and downloaded AEMO files. Clear AEMO cache removes "
                        "downloaded archives and extracted files while "
                        "preserving saved runs, configurations and database "
                        "data. Files may be downloaded again when needed. "
                        "Reset database deletes saved runs and custom data "
                        "and restores defaults on the next launch; it closes "
                        "the app and keeps AEMO cache files.",
                    ),
                ),
            ),
        ],
    )


class SettingsPage(QWidget):
    """Keep one editor visible without discarding drafts on navigation."""

    def __init__(
        self, data_manager: DataManager, sections: list[tuple[str, PanelController]]
    ):
        super().__init__()
        self.navigation = QListWidget()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setAccessibleName("Settings sections")
        self.navigation.setFixedWidth(200)
        self.sections = QStackedWidget()
        self.sections.setObjectName("settingsSections")
        for title, controller in sections:
            grid = Grid(
                data_manager,
                f"setup-{controller.id}",
                [GridItem(0, 0, 1, 1, controller)],
                n_rows=1,
                n_cols=1,
            )
            self.navigation.addItem(title)
            self.sections.addWidget(scrollable_page(grid, horizontal=False))
        self.navigation.currentRowChanged.connect(self.sections.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        layout = QHBoxLayout(self)
        layout.addWidget(self.navigation)
        layout.addWidget(self.sections, stretch=1)
