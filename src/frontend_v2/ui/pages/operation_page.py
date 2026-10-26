"""Operation page."""

from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from frontend_v2.bindings.bindings import ReadBinding, UiWriteBinding
from frontend_v2.bindings.selectors import (
    select_community_battery,
    select_community_battery_soc_history,
    select_community_energy_balance,
    select_current_replay_time,
    select_live_prices,
    select_operating_cost,
    select_plan_vs_live_battery_action,
    select_predicted_buy_and_sell_prices,
    select_predicted_community_pv,
    select_replay_state,
    select_storage_and_curtailment,
    select_time_duration_choices,
)
from frontend_v2.bindings.selectors.common import TIME_DURATION_SETTING
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.scroll_area import scrollable_page
from frontend_v2.ui.views.accent_view import AccentView
from frontend_v2.ui.views.date_selector_view import DateSelectorView
from frontend_v2.ui.views.line_graph_view import LineGraphView
from frontend_v2.ui.views.replay_bar import ReplayBarView


def operation_page(data_manager: DataManager) -> QWidget:
    """Build the operation page."""
    # Accentuated / KPI Views
    current_replay_time = PanelController(
        id="current_replay_time",
        view=AccentView(
            title="Current replay time",
            help_text="Time in the selected run’s local timezone. Use the replay "
            "timeline to inspect another interval; this does not rerun the simulation.",
            empty_text="No replay data yet.",
            description="Displayed replay interval",
        ),
        read=ReadBinding(select_current_replay_time),
    )
    community_battery = PanelController(
        id="community_battery",
        view=AccentView(
            title="Community battery",
            help_text="State of charge at the replayed interval, as a percentage "
            "of the selected run's total battery capacity. Stored energy in kWh "
            "is shown beneath it. Minimum and maximum SOC limit usable storage; "
            "the percentage is not rescaled to that usable range.",
            empty_text="No battery data yet.",
            description="State of charge",
        ),
        read=ReadBinding(
            select_community_battery,
            optional=frozenset({"get_all_battery_configs"}),
        ),
    )
    live_prices = PanelController(
        id="live_prices",
        view=AccentView(
            title="Live prices",
            help_text="Import and export tariffs used by the smart battery at the "
            "replayed interval, in Australian cents per kWh. They use saved "
            "AEMO market prices with modelled network tariffs. Fixed tariffs "
            "selected in Settings apply only to no-battery baseline "
            "comparisons.",
            empty_text="No pricing data yet.",
            description="Current tariff",
        ),
        read=ReadBinding(select_live_prices),
    )
    operating_cost = PanelController(
        id="operating_cost",
        view=AccentView(
            title="Net cost so far",
            help_text="Recorded import cost minus export revenue plus modelled "
            "battery wear, from run start to the replay cursor. The smaller "
            "figure covers the preceding 30 minutes, or elapsed time if shorter. "
            "Net earnings mean revenue exceeded costs. Chart duration does not "
            "change these figures. Carbon weighting and estimated credits for "
            "energy left at the end are excluded.",
            empty_text="No operating cost data yet.",
            description="Since run start · includes battery wear",
        ),
        read=ReadBinding(select_operating_cost),
    )

    # Diagnostics
    community_energy_balance = PanelController(
        id="community_energy_balance",
        view=LineGraphView(
            title="Community energy balance",
            help_text="Recorded power for all houses combined, in kW, for the "
            "smart-battery run. Demand is consumption, PV is available "
            "solar, import is bought from the grid and export is sold. A "
            "flat PV peak can reflect inverter limits. Chart duration "
            "selects the history ending at the replay cursor.",
            empty_text="No community energy data yet.",
        ),
        read=ReadBinding(select_community_energy_balance),
    )
    storage_and_curtailment = PanelController(
        id="storage_and_curtailment",
        view=LineGraphView(
            title="Storage and curtailment",
            help_text="Recorded smart-battery charging, discharging and curtailed "
            "solar power in kW, shown as positive values. Charging stores "
            "energy; discharging supplies it. Curtailed PV is available "
            "solar that was not used or exported. Compare with state of "
            "charge to understand storage limits. Chart duration selects the "
            "history ending at the replay cursor.",
            empty_text="No storage data yet.",
        ),
        read=ReadBinding(select_storage_and_curtailment),
    )
    plan_vs_live_battery_action = PanelController(
        id="plan_vs_live_battery_action",
        view=LineGraphView(
            title="Plan versus live battery action",
            help_text="Plan is the optimiser’s 30-minute schedule; live is the "
            "controller’s recorded action. Differences can occur as actual demand, "
            "solar output or battery limits differ from the plan’s assumptions. "
            "Each live point uses its 30-minute scheduled interval. Long runs "
            "are sampled; unavailable schedule intervals leave gaps while live "
            "data remains visible.",
            empty_text="No battery action data yet.",
        ),
        read=ReadBinding(
            select_plan_vs_live_battery_action,
            optional=frozenset({"get_current_optimiser_outputs"}),
        ),
    )
    community_battery_soc_history = PanelController(
        id="community_battery_soc_history",
        view=LineGraphView(
            title="Community battery SOC history",
            help_text="Recorded state of charge as a percentage of the selected "
            "run's total battery capacity. Flat sections mean stored energy is "
            "unchanged. The Community battery card also shows kWh at the replay "
            "cursor. Minimum and maximum SOC settings limit usable storage.",
            empty_text="No battery SOC data yet.",
            y_label="SOC (%)",
        ),
        read=ReadBinding(select_community_battery_soc_history),
    )
    predicted_community_pv = PanelController(
        id="predicted_community_pv",
        view=LineGraphView(
            title="Predicted community PV",
            help_text="Solar-output forecast available for the replayed optimiser "
            "interval. Chart duration selects the period ahead of the replay "
            "cursor, limited to the available forecast horizon. The issue "
            "time identifies the displayed forecast. Updating forecast means "
            "the previous issue remains visible while its replacement loads. "
            "Forecasts can differ from recorded solar output.",
            empty_text="PV forecast unavailable for this replay interval.",
        ),
        read=ReadBinding(select_predicted_community_pv),
    )
    predicted_buy_and_sell_prices = PanelController(
        id="predicted_buy_and_sell_prices",
        view=LineGraphView(
            title="Predicted buy and sell prices",
            help_text="Forecast import and export tariffs for the replayed optimiser "
            "interval, in c/kWh. Chart duration selects the period ahead of "
            "the replay cursor, limited to the available forecast horizon. "
            "These can differ from realised tariffs. The issue time "
            "identifies the displayed forecast; Updating forecast means its "
            "replacement is still loading.",
            empty_text="Price forecast unavailable for this replay interval.",
            y_label="c/kWh",
        ),
        read=ReadBinding(select_predicted_buy_and_sell_prices),
    )

    # Replay bar
    replay_bar = PanelController(
        id="replay_bar",
        view=ReplayBarView(data_manager.replay_clock),
        read=ReadBinding(select_replay_state),
    )

    time_duration = PanelController(
        id="time_duration",
        view=DateSelectorView(
            title="Chart duration",
            help_text="Choose how much history to show ending at the replay cursor. "
            "Forecast charts show the same duration ahead, limited to the "
            "available forecast horizon. All time shows the full history and "
            "the full available forecast. This changes Operation charts; the "
            "cost-so-far card and Community and Debrief whole-run results "
            "stay unchanged.",
            empty_text="No durations to show yet.",
            setting_key=TIME_DURATION_SETTING,
        ),
        read=ReadBinding(select_time_duration_choices),
        write=UiWriteBinding(data_manager),
    )

    kpis = [current_replay_time, community_battery, live_prices, operating_cost]
    for controller in kpis:
        controller.view.setMinimumHeight(110)
    summary = Grid(
        data_manager,
        "operation-summary",
        [GridItem(0, index, 1, 1, controller) for index, controller in enumerate(kpis)],
        n_rows=1,
        n_cols=4,
    )
    summary.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    charts = [
        community_energy_balance,
        storage_and_curtailment,
        plan_vs_live_battery_action,
        community_battery_soc_history,
        predicted_community_pv,
        predicted_buy_and_sell_prices,
    ]
    for controller in charts:
        controller.view.setMinimumHeight(320)
    chart_grid = Grid(
        data_manager,
        "operation-charts",
        [
            GridItem(index // 2, index % 2, 1, 1, controller)
            for index, controller in enumerate(charts)
        ],
        n_rows=3,
        n_cols=2,
        minimum_column_width=520,
    )
    controls = Grid(
        data_manager,
        "operation-controls",
        [
            GridItem(0, 0, 3, 1, replay_bar),
            GridItem(0, 3, 1, 1, time_duration),
        ],
        n_rows=1,
        n_cols=4,
    )
    controls.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(summary)
    layout.addWidget(scrollable_page(chart_grid, horizontal=False), stretch=1)
    layout.addWidget(controls)
    return page
