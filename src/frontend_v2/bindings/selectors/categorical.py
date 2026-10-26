"""bindings/selectors/categorical.py
Selectors that return a CategoricalPayload: one value per named category."""

from math import isfinite

from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.bindings.selectors.common import (
    CURRENT_REQUESTS_AND_SETTINGS,
    NO_BATTERY,
    OPTIMISED,
    PERFECT_FORESIGHT,
    SCENARIO_LABELS,
    by_scenario,
    current_request_rows,
)
from frontend_v2.bindings.selectors.objectives import _mode, _number, _terminal_credit
from frontend_v2.ui.theme import SCENARIO_COLOURS
from frontend_v2.utils.payload import CategoricalPayload

# Debrief adds a hindsight benchmark to Community's three methods.
BREAKDOWN_SCENARIOS = (OPTIMISED, "reactive_battery", NO_BATTERY, PERFECT_FORESIGHT)


@needs(CURRENT_REQUESTS_AND_SETTINGS | {"get_current_operating_costs"})
def select_cost_breakdown(data: dict) -> CategoricalPayload:
    """Terminal-adjusted financial components, with carbon for Balanced."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    rows = [costs[name] for name in BREAKDOWN_SCENARIOS if name in costs]
    balanced = _mode(data) == "Balanced"
    financial_credits = []
    carbon_credits = []
    available = []
    for row in rows:
        credit = (
            _terminal_credit(row, "Balanced" if balanced else "Cost")
            if row["scenario_name"] == OPTIMISED
            else 0.0
        )
        financial = (
            _number(row, "estimated_terminal_balanced_financial_value_aud")
            if balanced and row["scenario_name"] == OPTIMISED
            else credit
        )
        if (
            credit is not None
            and financial is not None
            and isfinite(credit - financial)
            and all(
                _number(row, field) is not None
                for field in (
                    "grid_import_aud",
                    "grid_export_aud",
                    "battery_degradation_aud",
                )
            )
            and (not balanced or _number(row, "emissions_cost_aud") is not None)
        ):
            available.append(row)
            financial_credits.append(financial)
            carbon_credits.append(credit - financial)
    missing_results = len(available) < len(rows)
    rows = available
    values = {
        "Bill cost (AUD)": [
            row["grid_import_aud"] - row["grid_export_aud"] - credit
            for row, credit in zip(rows, financial_credits, strict=True)
        ],
        "Battery wear (AUD)": [row["battery_degradation_aud"] for row in rows],
    }
    if balanced:
        values["Modelled carbon cost (AUD equivalent)"] = [
            row["emissions_cost_aud"] - credit
            for row, credit in zip(rows, carbon_credits, strict=True)
        ]
    detail = (
        "Only the smart battery receives an estimated remaining-energy credit. "
        "This can put its adjusted result below perfect foresight."
    )
    if missing_results:
        detail += " Methods with unavailable values are omitted."
    return CategoricalPayload(
        x_labels=[
            "Perfect foresight (hindsight)"
            if row["scenario_name"] == PERFECT_FORESIGHT
            else SCENARIO_LABELS[row["scenario_name"]]
            for row in rows
        ],
        y_values=values,
        title="Balanced operating score" if balanced else "Economic operating cost",
        y_label="AUD equivalent" if balanced else "AUD",
        subtitle="Terminal-adjusted estimates · lower is better",
        detail=detail,
    )


@needs(CURRENT_REQUESTS_AND_SETTINGS | {"get_current_operating_costs"})
def select_emissions(data: dict) -> CategoricalPayload:
    """Subtract the smart battery's terminal credit from its emissions bar."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    field = (
        "estimated_terminal_balanced_emissions_value_tonnes"
        if _mode(data) == "Balanced"
        else "estimated_terminal_emissions_value_tonnes"
    )
    credit = _number(costs.get(OPTIMISED), field)
    recorded = _number(costs.get(OPTIMISED), "emissions_tonnes")
    adjusted = None
    if credit is not None and recorded is not None and isfinite(recorded - credit):
        adjusted = recorded - credit
    detail = (
        "Only Optimised has an estimated remaining-energy credit. "
        "Its bar subtracts that credit from recorded emissions. "
        "Reactive and Perfect foresight show recorded totals without adjustment."
    )
    if adjusted is None and OPTIMISED in costs:
        del costs[OPTIMISED]
        detail += " Optimised is omitted because its terminal estimate is unavailable."
    return CategoricalPayload(
        x_labels=[SCENARIO_LABELS[name] for name in costs],
        y_values={
            "Emissions (tonnes)": [
                adjusted if name == OPTIMISED else row["emissions_tonnes"]
                for name, row in costs.items()
            ]
        },
        x_colours=[SCENARIO_COLOURS[name] for name in costs],
        y_label="Emissions (tonnes)",
        subtitle="Optimised includes an estimated remaining-energy credit",
        detail=detail,
    )
