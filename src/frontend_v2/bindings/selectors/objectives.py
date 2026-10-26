"""Shared objective metrics for Community and Debrief comparisons."""

from math import isfinite

from frontend_v2.bindings.selectors.common import (
    KG_PER_TONNE,
    NO_BATTERY,
    current_request_rows,
)

GOALS = {
    "Cost": (
        "Financial cost (AUD)",
        "Minimise import cost minus export revenue, including modelled battery wear.",
    ),
    "Emissions": (
        "Grid emissions (kg CO₂e)",
        "Minimise emissions from imported grid electricity.",
    ),
    "Balanced": (
        "Balanced score (AUD equivalent)",
        "Minimise financial cost plus carbon-priced grid emissions. "
        "The carbon price determines the trade-off.",
    ),
}


def _mode(data: dict) -> str | None:
    requests = current_request_rows(data, "get_all_simulation_requests")
    return str(requests[0]["objective_mode"]) if len(requests) else None


def _number(row, field: str) -> float | None:
    """Absent or nonfinite results are unavailable, never an implicit zero."""
    if row is None:
        return None
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _terminal_credit(row, mode: str | None) -> float | None:
    if row is None:
        return None
    if row["scenario_name"] in (NO_BATTERY, "reactive_battery"):
        return 0.0
    field = {
        "Cost": "estimated_terminal_cost_value_aud",
        "Emissions": "estimated_terminal_emissions_value_tonnes",
        "Balanced": "estimated_terminal_balanced_value_aud",
    }.get(mode)
    return _number(row, field) if field else None


def _adjusted_value(
    row, field: str, mode: str, *, terminal_adjusted: bool = True
) -> float | None:
    value = _number(row, field)
    credit = _terminal_credit(row, mode) if terminal_adjusted else 0.0
    if value is None or credit is None or not isfinite(value - credit):
        return None
    return value - credit


def _goal_value(
    row, mode: str | None, *, terminal_adjusted: bool = True
) -> float | None:
    """Evaluate the saved objective; hindsight can use recorded totals only."""
    if mode == "Emissions":
        tonnes = _adjusted_value(
            row, "emissions_tonnes", "Emissions", terminal_adjusted=terminal_adjusted
        )
        if tonnes is None:
            return None
        kilograms = tonnes * KG_PER_TONNE
        return kilograms if isfinite(kilograms) else None
    if mode == "Cost":
        return _adjusted_value(
            row,
            "realised_economic_cost_aud",
            "Cost",
            terminal_adjusted=terminal_adjusted,
        )
    if mode == "Balanced":
        cost = _number(row, "realised_economic_cost_aud")
        carbon = _number(row, "emissions_cost_aud")
        credit = _terminal_credit(row, mode) if terminal_adjusted else 0.0
        if cost is not None and carbon is not None and credit is not None:
            total = cost + carbon - credit
            return total if isfinite(total) else None
    return None
