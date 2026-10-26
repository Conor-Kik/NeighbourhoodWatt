"""Debrief page."""

from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors import (
    select_battery_power,
    select_battery_soc,
    select_community_power,
    select_cost_breakdown,
    select_cumulative_cost,
    select_emissions,
    select_emissions_avoided,
    select_foresight_gap,
    select_grid_power,
    select_run_settings,
    select_savings,
    select_terminal_value,
    select_total_curtailed_pv,
    select_total_demand,
    select_total_grid_export,
    select_total_grid_import,
    select_total_pv,
)
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.views.accent_view import AccentView
from frontend_v2.ui.views.bar_graph_view import BarGraphView
from frontend_v2.ui.views.goal_summary_view import GoalSummaryView
from frontend_v2.ui.views.line_graph_view import LineGraphView

NO_RUN_TEXT = "No run picked yet. Pick one in Settings."
SUMMARY_NUMBER_POINT_SIZE = 28

# Perfect foresight sees the whole run, so it spends its battery down by the end
EMPTIES_BATTERY_NOTE = (
    "Perfect foresight can finish at minimum SOC because it knows when the run ends."
)


def debrief_page(data_manager: DataManager) -> QWidget:
    """Build the DeBrief page."""
    current_run = PanelController(
        id="current_run",
        view=GoalSummaryView(
            title="Run settings",
            help_text="Settings for the selected saved run: goal, location, dates, "
            "baseline tariffs, houses and battery. Results cover the whole "
            "community and the whole run. Community summarises outcomes; "
            "Debrief explains their components and compares methods with "
            "hindsight. Choose another run in Settings → Previous runs. The "
            "Operation replay cursor does not change these results.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(
            select_run_settings,
            optional=frozenset({"get_current_operating_costs"}),
        ),
    )
    savings = PanelController(
        id="savings",
        view=AccentView(
            title="Saved vs no battery",
            number_point_size=SUMMARY_NUMBER_POINT_SIZE,
            help_text="Financial cost without a battery minus the smart battery's "
            "financial cost, including modelled battery wear and excluding "
            "carbon weighting. Only the smart battery receives a financial "
            "remaining-energy estimate; Balanced runs use its Balanced "
            "financial estimate. Positive savings mean a lower estimated "
            "cost; a negative saving reads 'additional cost'. The detail "
            "shows adjusted costs or net earnings, not guaranteed cash "
            "savings.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_savings),
    )
    foresight_gap = PanelController(
        id="foresight_gap",
        view=AccentView(
            title="Smart battery vs perfect foresight",
            number_point_size=SUMMARY_NUMBER_POINT_SIZE,
            help_text="Perfect foresight plans with the actual future data under the "
            "saved goal: financial cost for Cost, grid emissions for "
            "Emissions, or financial plus modelled carbon cost for Balanced. "
            "Behind means the smart battery's adjusted goal value is higher; "
            "ahead means lower. Only the smart battery receives the matching "
            "estimated future value of energy left in the battery (SOC). This "
            "credit can make the rolling smart battery's adjusted result lower "
            "than perfect foresight's recorded result over the run. "
            "Missing matching estimates show "
            "unavailable.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_foresight_gap),
    )
    emissions_avoided = PanelController(
        id="emissions_avoided",
        view=AccentView(
            title="Emissions vs no battery",
            number_point_size=SUMMARY_NUMBER_POINT_SIZE,
            help_text="Grid-import emissions without a battery minus the smart "
            "battery's emissions, including its estimated remaining-energy "
            "emissions credit. Less means lower estimated emissions; more "
            "means higher. Balanced runs use the emissions estimate from the "
            "same Balanced future strategy. Values are whole-community kg "
            "CO₂e and exclude equipment manufacturing.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_emissions_avoided),
    )
    terminal_value = PanelController(
        id="terminal_value",
        view=AccentView(
            title="Energy left in battery",
            number_point_size=SUMMARY_NUMBER_POINT_SIZE,
            help_text="Estimated financial value of energy remaining above the "
            "battery's minimum state of charge at run end, in AUD. The "
            "detail shows total stored kWh. This estimate is included in "
            "financial comparisons and is not cash already earned. For "
            "Balanced runs this tile shows the financial component; the "
            "Balanced score also includes the carbon component of the "
            "remaining-energy estimate.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_terminal_value),
    )
    cost_breakdown = PanelController(
        id="cost_breakdown",
        view=BarGraphView(
            title="Operating cost breakdown",
            help_text="Bill cost plus modelled battery wear gives terminal-adjusted "
            "financial cost. The bill component includes the financial "
            "remaining-energy estimate; battery wear is shown separately. "
            "Balanced runs also include carbon cost after its matching "
            "remaining-energy estimate, giving the same Balanced total as "
            "Community. The diamond and Net label show the sum. Estimates "
            "can be negative, increasing the total. Only smart battery receives "
            "a remaining-energy credit. Reactive and Perfect foresight use "
            "recorded values. Perfect foresight is a hindsight benchmark planned "
            "with actual future data under the saved goal. The estimated future "
            "value of remaining battery energy (SOC) can make the rolling smart "
            "battery's adjusted result lower than perfect foresight's recorded "
            "result. Carbon weighting "
            "is not an extra bill. Upfront battery cost is excluded. Methods "
            "with missing estimates are omitted; small segments may be "
            "unlabelled.",
            empty_text="Operating cost breakdown unavailable for this run.",
            y_label="AUD",
            stacked=True,
            show_empty_when_no_categories=True,
        ),
        read=ReadBinding(select_cost_breakdown),
    )
    emissions = PanelController(
        id="emissions",
        view=BarGraphView(
            title="Emissions",
            help_text="Grid-import emissions over the whole run, in tonnes. "
            "Optimised's bar subtracts the estimated emissions value of energy left "
            "in the battery and matches the Emissions vs no battery tile and "
            "Community's adjusted grid emissions. Balanced runs use the emissions "
            "component of the Balanced estimate. Only Optimised receives this "
            "credit; Reactive and Perfect foresight show recorded emissions only. "
            "Missing estimates are omitted. Credits can be negative, increasing "
            "the adjusted total. Lower is better. Equipment manufacturing is "
            "excluded.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_emissions),
    )
    cumulative_cost = PanelController(
        id="cumulative_cost",
        view=LineGraphView(
            title="Cumulative cost",
            help_text="Recorded import cost minus export revenue plus modelled battery "
            "wear, accumulated from run start and sampled every 30 minutes. "
            "A falling line means revenue exceeded costs during that period. "
            "Carbon weighting and remaining-energy estimates are excluded, "
            "so the final value can differ from the adjusted operating-cost "
            "breakdown. Perfect foresight is a hindsight benchmark.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_cumulative_cost),
    )
    battery_soc = PanelController(
        id="battery_soc",
        view=LineGraphView(
            title="Battery state of charge",
            help_text="Stored energy as a percentage of nominal capacity, sampled "
            "every 30 minutes. Compare how the methods reserve or spend energy. "
            "Configured minimum and maximum SOC limit normal operation; "
            f"{EMPTIES_BATTERY_NOTE}",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(select_battery_soc),
    )

    community_power = PanelController(
        id="community_power",
        view=LineGraphView(
            title="Community demand and solar",
            help_text="Whole-community demand, available PV generation and curtailed "
            "PV in kW, averaged over 30-minute intervals for the "
            "smart-battery run. Curtailed PV is available solar that was not "
            "used or exported. Compare with Grid power and Battery power. "
            "The energy tiles above sum these flows over the whole run in "
            "kWh.",
            empty_text=NO_RUN_TEXT,
            y_label="kW",
        ),
        read=ReadBinding(select_community_power),
    )
    grid_power = PanelController(
        id="grid_power",
        view=LineGraphView(
            title="Grid power",
            help_text="Power imported from and exported to the grid in kW, averaged "
            "over 30-minute intervals, for the optimised run. Both are shown as "
            "positive values.",
            empty_text=NO_RUN_TEXT,
            y_label="kW",
        ),
        read=ReadBinding(select_grid_power),
    )
    battery_power = PanelController(
        id="battery_power",
        view=LineGraphView(
            title="Battery power",
            help_text="Power charging into and discharging out of the community "
            "battery in kW, averaged over 30-minute intervals, for the optimised "
            "run. Both are shown as positive values.",
            empty_text=NO_RUN_TEXT,
            y_label="kW",
        ),
        read=ReadBinding(select_battery_power),
    )
    totals = [
        PanelController(
            id=f"total_{key}",
            view=AccentView(
                title=title,
                help_text=f"{help_text} Energy in kWh summed over the whole run, "
                "rather than power in kW. Demand and available PV cover all "
                "houses. Grid import, export and curtailment describe the "
                "smart-battery run. The replay cursor does not change these totals.",
                empty_text=NO_RUN_TEXT,
                description="over the whole run",
            ),
            read=ReadBinding(selector),
        )
        for key, title, help_text, selector in (
            (
                "demand",
                "Demand",
                "Electricity used by all houses.",
                select_total_demand,
            ),
            (
                "pv",
                "PV generation",
                "Available solar from all houses, before curtailment.",
                select_total_pv,
            ),
            (
                "grid_import",
                "Grid import",
                "Electricity bought from the grid.",
                select_total_grid_import,
            ),
            (
                "grid_export",
                "Grid export",
                "Electricity sold to the grid.",
                select_total_grid_export,
            ),
            (
                "curtailed_pv",
                "Curtailed PV",
                "Solar that was available but neither used nor exported.",
                select_total_curtailed_pv,
            ),
        )
    ]

    banner = Grid(
        data_manager, "debrief-run", [GridItem(0, 0, 1, 1, current_run)], 1, 1
    )
    cards = [savings, foresight_gap, emissions_avoided, terminal_value]
    for controller in (*cards, *totals):
        controller.view.setMinimumHeight(150)
    summary = Grid(
        data_manager,
        "debrief-summary",
        [
            GridItem(0, index, 1, 1, controller)
            for index, controller in enumerate(cards)
        ],
        n_rows=1,
        n_cols=len(cards),
        minimum_column_width=220,
    )
    totals_grid = Grid(
        data_manager,
        "debrief-totals",
        [
            GridItem(0, index, 1, 1, controller)
            for index, controller in enumerate(totals)
        ],
        n_rows=1,
        n_cols=len(totals),
        minimum_column_width=180,
    )
    grid = Grid(
        data_manager,
        "debrief-grid",
        [
            GridItem(row=0, col=0, width=8, height=4, controller=cost_breakdown),
            GridItem(row=0, col=8, width=8, height=4, controller=emissions),
            GridItem(row=4, col=0, width=8, height=4, controller=cumulative_cost),
            GridItem(row=4, col=8, width=8, height=4, controller=battery_soc),
            GridItem(row=8, col=0, width=6, height=5, controller=community_power),
            GridItem(row=8, col=6, width=5, height=5, controller=grid_power),
            GridItem(row=8, col=11, width=5, height=5, controller=battery_power),
        ],
        n_cols=16,
        n_rows=13,
    )
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    for strip in (banner, summary, totals_grid):
        strip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    layout.addWidget(banner)
    layout.addWidget(summary)
    layout.addWidget(totals_grid)
    layout.addWidget(grid, stretch=1)
    return page
