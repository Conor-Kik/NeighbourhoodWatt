"""Community outcomes, explained against the selected run's goal."""

from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors import (
    select_community_financial_cost,
    select_community_goal_benefit,
    select_community_goal_comparison,
    select_community_goal_context,
    select_community_grid_emissions,
    select_community_net_flows,
    select_household_day,
)
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.ui.grid import Grid, GridItem
from frontend_v2.ui.panel_controller import PanelController
from frontend_v2.ui.views.accent_view import AccentView
from frontend_v2.ui.views.bar_graph_view import BarGraphView
from frontend_v2.ui.views.goal_summary_view import GoalSummaryView
from frontend_v2.ui.views.line_graph_view import LineGraphView

NO_RUN_TEXT = "Pick a saved run in Settings → Previous runs."
AVERAGE_TITLE = "Average per house—not an individual bill"


def community_page(data_manager: DataManager) -> QWidget:
    """Reuse responsive Grids inside MainWindow's single page scroll wrapper."""
    context = PanelController(
        id="community_goal_context",
        view=GoalSummaryView(
            title="Selected goal",
            help_text="The selected saved run's goal and whole-community results. Cost "
            "compares financial cost, Emissions compares grid-import "
            "emissions, and Balanced compares financial plus modelled carbon "
            "cost. Results include the matching remaining-energy estimate. "
            "This page summarises outcomes; Debrief provides component "
            "breakdowns and hindsight comparisons. Viewing a goal does not "
            "rerun the simulation.",
            empty_text=NO_RUN_TEXT,
        ),
        read=ReadBinding(
            select_community_goal_context,
            optional=frozenset({"get_current_operating_costs"}),
        ),
    )
    benefit = PanelController(
        id="community_goal_benefit",
        view=AccentView(
            title="Goal improvement vs no battery",
            help_text="Shared solar without a battery minus the smart battery, "
            "measured against the selected goal: financial cost for Cost, "
            "grid emissions for Emissions, or financial plus modelled carbon "
            "cost for Balanced. Only the smart battery receives the matching "
            "remaining-energy estimate. Positive is improvement; negative is "
            "a worse estimated outcome. This goal comparison can differ from "
            "financial savings when the goal includes emissions.",
            empty_text="Goal comparison unavailable.",
            description="Terminal-adjusted · against the selected goal",
        ),
        read=ReadBinding(select_community_goal_benefit),
    )
    financial = PanelController(
        id="community_financial_cost",
        view=AccentView(
            title="Adjusted financial cost",
            help_text="Whole-community import cost minus export revenue plus modelled "
            "battery wear, less the estimated financial value of remaining "
            "energy. Excludes carbon weighting. Balanced runs use the "
            "financial component of their Balanced future strategy. This "
            "matches the financial components in Debrief's operating-cost "
            "breakdown. A negative total is an estimated net benefit. The "
            "per-house value is an equal average, not an individual bill.",
            empty_text="Adjusted financial estimate unavailable.",
            description="Terminal-adjusted · AUD",
            detail_title=AVERAGE_TITLE,
        ),
        read=ReadBinding(
            select_community_financial_cost,
            optional=frozenset({"get_current_run_houses"}),
        ),
    )
    emissions = PanelController(
        id="community_grid_emissions",
        view=AccentView(
            title="Adjusted grid emissions",
            help_text="Whole-community grid-import emissions minus estimated future "
            "emissions avoided by remaining battery energy, in kg CO₂e. "
            "Balanced runs use the emissions component of their Balanced "
            "future strategy. Excludes equipment manufacturing. The "
            "per-house value is an equal average. Debrief's Emissions chart "
            "instead shows recorded tonnes before this estimate.",
            empty_text="Adjusted emissions estimate unavailable.",
            description="Terminal-adjusted · kg CO₂e",
            detail_title=AVERAGE_TITLE,
        ),
        read=ReadBinding(
            select_community_grid_emissions,
            optional=frozenset({"get_current_run_houses"}),
        ),
    )
    comparison = PanelController(
        id="community_goal_comparison",
        view=BarGraphView(
            title="Performance against the selected goal",
            help_text="Compare the three methods under the saved goal: financial cost "
            "for Cost, grid emissions for Emissions, or financial plus "
            "modelled carbon cost for Balanced. Lower is better. Smart "
            "battery plans using forecasts. Reactive battery charges from "
            "surplus solar and discharges to meet demand within battery "
            "limits, without forecasts. Shared solar without a battery is "
            "the baseline. Only smart battery receives a matching "
            "remaining-energy estimate; reactive assumes minimum SOC. "
            "Methods with missing results or estimates are omitted. Debrief "
            "explains the financial and Balanced totals in its component "
            "breakdown.",
            empty_text="Goal comparison unavailable.",
            subtitle="Terminal-adjusted estimates · lower is better · same goal",
            show_empty_when_no_categories=True,
        ),
        read=ReadBinding(select_community_goal_comparison),
    )
    household = PanelController(
        id="household_day",
        view=LineGraphView(
            title="Average house: demand and solar",
            help_text="Whole-community demand and available PV generation divided "
            "equally by the number of houses, averaged over 30-minute "
            "intervals across the whole run. This is an average across the "
            "selected house mix. Inverter limits can flatten solar peaks; "
            "curtailed solar is shown separately on Debrief.",
            empty_text="Average-house power unavailable.",
            subtitle="Arithmetic average across the selected house mix",
            y_label="kW per house",
        ),
        read=ReadBinding(select_household_day),
    )
    flows = PanelController(
        id="community_net_flows",
        view=LineGraphView(
            title="Grid and battery flows",
            help_text="Whole-community power for the smart-battery run, averaged over "
            "30-minute intervals. Net grid is import minus export: positive "
            "means buying and negative means selling. Net battery is "
            "discharge minus charge: positive means supplying power and "
            "negative means charging. These charts cover the whole run and "
            "do not follow the Operation replay cursor.",
            empty_text="Community power flows unavailable.",
            subtitle="Whole-run context · above zero: buying / discharging · "
            "below zero: selling / charging",
            y_label="kW",
        ),
        read=ReadBinding(select_community_net_flows),
    )

    goal = Grid(data_manager, "community-goal", [GridItem(0, 0, 1, 1, context)], 1, 1)
    cards = [benefit, financial, emissions]
    for controller in cards:
        controller.view.setMinimumHeight(185)
    summary = Grid(
        data_manager,
        "community-summary",
        [
            GridItem(0, index, 1, 1, controller)
            for index, controller in enumerate(cards)
        ],
        n_rows=1,
        n_cols=3,
        minimum_column_width=260,
    )
    for controller in (comparison, household, flows):
        controller.view.setMinimumHeight(340)
    charts = Grid(
        data_manager,
        "community-charts",
        [GridItem(0, 0, 1, 1, comparison), GridItem(0, 1, 1, 1, household)],
        n_rows=1,
        n_cols=2,
        minimum_column_width=420,
    )
    flow_grid = Grid(
        data_manager, "community-flows", [GridItem(0, 0, 1, 1, flows)], 1, 1
    )
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    for grid in (goal, summary, charts, flow_grid):
        grid.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(grid)
    layout.addStretch()
    return page
