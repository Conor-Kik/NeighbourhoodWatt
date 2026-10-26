"""Community outcomes evaluated against the selected run's objective."""

from math import isfinite

from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.bindings.selectors.common import (
    CURRENT_REQUESTS_AND_SETTINGS,
    HOUSEHOLD_NEEDS,
    KG_PER_TONNE,
    NO_BATTERY,
    OPTIMISED,
    by_scenario,
    current_request_rows,
    current_request_times,
    house_count,
)
from frontend_v2.bindings.selectors.objectives import (
    GOALS,
    _adjusted_value,
    _goal_value,
    _mode,
    _number,
    _terminal_credit,
)
from frontend_v2.bindings.selectors.tabular import (
    NOT_AVAILABLE,
    _aud,
    _kg,
    _tile,
    select_run_battery,
    select_run_houses,
)
from frontend_v2.ui.theme import SCENARIO_COLOURS
from frontend_v2.utils.payload import CategoricalPayload, TabularPayload

OUTCOME_NEEDS = CURRENT_REQUESTS_AND_SETTINGS | {"get_current_operating_costs"}
METHODS = {
    OPTIMISED: "Smart\nbattery",
    "reactive_battery": "Reactive\nbattery",
    NO_BATTERY: "Shared solar\nwithout battery",
}


def _tariff(price) -> str:
    """A saved fixed tariff in c/kWh, or the market price when none was fixed."""
    if price is None or not isfinite(price):
        return "market price"
    return f"{price * 100:.2f} c/kWh (fixed)"


@needs(OUTCOME_NEEDS | {"get_all_locations"})
def select_community_goal_context(data: dict) -> TabularPayload:
    requests = current_request_rows(data, "get_all_simulation_requests")
    if not len(requests):
        return TabularPayload(headers=[], rows=[])
    request = requests[0]
    mode = _mode(data)
    times, _ = current_request_times(
        data, [request["start_time_utc"], request["end_time_utc"]]
    )
    count = int(request["house_count"])
    houses = "house" if count == 1 else "houses"
    tariffs = " · ".join(
        f"{side} {_tariff(request[field])}"
        for side, field in (
            ("buy", "fixed_buy_aud_per_kwh"),
            ("sell", "fixed_sell_aud_per_kwh"),
        )
    )
    context = (
        f"{request['location_name']} · {times[0]:%d %b %Y} → {times[1]:%d %b %Y} "
        f"· {count} {houses} · {tariffs}"
    )
    goal = GOALS.get(mode)
    explanation = (
        goal[1]
        if goal
        else "Objective unavailable; goal comparisons cannot be evaluated."
    )
    amounts = ["", "", "", ""]
    carbon_rate = ""
    rows = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    smart = rows.get(OPTIMISED)
    if mode in GOALS:
        emissions = mode == "Emissions"
        recorded = _number(
            smart, "emissions_tonnes" if emissions else "realised_economic_cost_aud"
        )
        carbon = _number(smart, "emissions_cost_aud")
        credit = _terminal_credit(smart, mode)
        result = _goal_value(smart, mode)
        format_value = _kg if emissions else _aud
        amounts = [
            format_value(recorded) if recorded is not None else NOT_AVAILABLE,
            (_aud(carbon) if carbon is not None else NOT_AVAILABLE)
            if mode == "Balanced"
            else "",
            format_value(credit) if credit is not None else NOT_AVAILABLE,
            format_value(result / KG_PER_TONNE if emissions else result)
            if result is not None
            else NOT_AVAILABLE,
        ]
    if mode == "Balanced":
        carbon = _number(smart, "emissions_cost_aud")
        tonnes = _number(smart, "emissions_tonnes")
        rate = carbon / tonnes if carbon is not None and tonnes and tonnes > 0 else None
        carbon_rate = (
            f"Effective carbon price: {_aud(rate)}/t CO₂e · emissions-weighted"
            if rate is not None and isfinite(rate) and rate >= 0
            else "Effective carbon price unavailable"
            + (" · no grid emissions" if tonnes == 0 else "")
        )
    return TabularPayload(
        headers=[
            "goal",
            "explanation",
            "context",
            "run_id",
            "financial",
            "carbon",
            "terminal",
            "result",
            "carbon_rate",
            "note",
            "details",
        ],
        rows=[
            (
                mode or "Unavailable",
                explanation,
                context,
                str(request["request_id"]),
                *amounts,
                carbon_rate,
                "Subtracts the estimated value of energy remaining at run end. "
                "Reactive assumes minimum SOC. "
                + (
                    "Some estimates are unavailable; rerun to calculate them. "
                    if any(
                        _terminal_credit(rows.get(name), mode) is None
                        for name in METHODS
                        if name in rows
                    )
                    else ""
                )
                + (
                    "Carbon weighting is not an additional bill."
                    if mode == "Balanced"
                    else ""
                ),
                "Remaining energy\n"
                "Values energy above minimum SOC using post-run forecasts and "
                "remaining cycle allowance. Estimates can be negative.\n\n"
                "Comparison basis\nReactive "
                "control is assumed to finish at minimum SOC and receives no credit. "
                "Balanced results and their financial and emissions cards use "
                "the same future strategy. Other goals retain their separate "
                "financial and emissions estimates.\n\n"
                "What this means\n"
                "These are estimates, not bill savings or guaranteed outcomes. "
                "See Debrief for detailed totals and hindsight analysis."
                + (
                    "\n\nCarbon weighting\nBalanced is not a fixed 50/50 split. "
                    "The effective price is stored carbon cost divided by grid "
                    "emissions, weighted by emissions rather than time, "
                    "including year changes."
                    if mode == "Balanced"
                    else ""
                ),
            )
        ],
    )


@needs(
    OUTCOME_NEEDS | HOUSEHOLD_NEEDS | {"get_all_locations", "get_all_battery_configs"}
)
def select_run_settings(data: dict) -> TabularPayload:
    """The selected run's goal and settings for Debrief's top card: the goal, place,
    dates and tariffs, with the houses and battery listed under the toggle."""
    goal = select_community_goal_context(data)
    if not goal.rows:
        return TabularPayload(headers=[], rows=[])
    facts = dict(zip(goal.headers, goal.rows[0], strict=True))
    house_lines = [
        f"{count} × {config} ({pv:g} kW solar, {inverter:g} kW inverter, {extras})"
        for config, count, pv, inverter, extras in select_run_houses(data).rows
    ]
    battery = dict(select_run_battery(data).rows)
    battery_lines = [
        f"{name}: {value:g}" if isinstance(value, float) else f"{name}: {value}"
        for name, value in battery.items()
    ]
    note = ""
    if battery:
        note = (
            f"Battery: {battery['Capacity (kWh)']:g} kWh · "
            f"{battery['Max power (kW)']:g} kW max power"
        )
    return TabularPayload(
        headers=[
            "goal",
            "explanation",
            "context",
            "run_id",
            "financial",
            "carbon",
            "terminal",
            "result",
            "carbon_rate",
            "note",
            "details",
        ],
        rows=[
            (
                "Run settings",
                f"{facts['goal']} goal. {facts['explanation']}",
                facts["context"],
                facts["run_id"],
                "",
                "",
                "",
                "",
                facts["carbon_rate"],
                note,
                "Houses\n"
                + " · ".join(house_lines or ["Unavailable"])
                + "\n\nBattery\n"
                + " · ".join(battery_lines or ["Unavailable"]),
            )
        ],
    )


@needs(OUTCOME_NEEDS)
def select_community_goal_benefit(data: dict) -> TabularPayload:
    mode = _mode(data)
    rows = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    smart = _goal_value(rows.get(OPTIMISED), mode)
    baseline = _goal_value(rows.get(NO_BATTERY), mode)
    if smart is None or baseline is None:
        return _tile(NOT_AVAILABLE, "Goal comparison unavailable")
    # Match the displayed precision so tiny differences do not claim a benefit.
    difference = baseline - smart
    if not isfinite(difference):
        return _tile(NOT_AVAILABLE, "Goal comparison unavailable")
    benefit = round(difference, 1 if mode == "Emissions" else 2)
    shown = _kg(benefit / KG_PER_TONNE) if mode == "Emissions" else _aud(benefit)
    if benefit > 0:
        shown = "+" + shown
    state = (
        "Improvement"
        if benefit > 0
        else "Worse outcome"
        if benefit < 0
        else "No change"
    )
    metric = GOALS[mode][0]
    return _tile(
        shown, f"{state} · {metric}\nShared solar without battery minus smart battery"
    )


def _community_total(data: dict, field: str, emissions: bool = False) -> TabularPayload:
    rows = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    smart = rows.get(OPTIMISED)
    if _mode(data) == "Balanced":
        credit_field = (
            "estimated_terminal_balanced_emissions_value_tonnes"
            if emissions
            else "estimated_terminal_balanced_financial_value_aud"
        )
        recorded, credit = _number(smart, field), _number(smart, credit_field)
        value = (
            recorded - credit if recorded is not None and credit is not None else None
        )
        if value is not None and not isfinite(value):
            value = None
    else:
        value = _adjusted_value(smart, field, "Emissions" if emissions else "Cost")
    if value is None or (emissions and not isfinite(value * KG_PER_TONNE)):
        return _tile(NOT_AVAILABLE, "Terminal-adjusted estimate unavailable")
    count = house_count(data)
    format_value = _kg if emissions else _aud
    average = format_value(value / count) if count > 0 else "Average unavailable"
    return _tile(format_value(value), average)


@needs(OUTCOME_NEEDS | HOUSEHOLD_NEEDS)
def select_community_financial_cost(data: dict) -> TabularPayload:
    return _community_total(data, "realised_economic_cost_aud")


@needs(OUTCOME_NEEDS | HOUSEHOLD_NEEDS)
def select_community_grid_emissions(data: dict) -> TabularPayload:
    return _community_total(data, "emissions_tonnes", emissions=True)


@needs(OUTCOME_NEEDS)
def select_community_goal_comparison(data: dict) -> CategoricalPayload:
    mode = _mode(data)
    rows = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    values = {name: _goal_value(rows.get(name), mode) for name in METHODS}
    available = {name: value for name, value in values.items() if value is not None}
    smart, reactive = values[OPTIMISED], values["reactive_battery"]
    detail = "Smart-versus-reactive comparison unavailable."
    if smart is not None and reactive is not None and isfinite(reactive - smart):
        difference = round(reactive - smart, 1 if mode == "Emissions" else 2)
        metric = GOALS[mode][0].split(" (")[0].lower()
        if difference == 0:
            detail = (
                f"Smart battery and reactive control have the same adjusted {metric}."
            )
        else:
            amount = (
                _kg(abs(difference) / KG_PER_TONNE) + " CO₂e"
                if mode == "Emissions"
                else _aud(abs(difference))
                + (" AUD equivalent" if mode == "Balanced" else "")
            )
            change = "lower" if difference > 0 else "higher"
            detail = (
                f"Smart battery: {amount} {change} {metric} than reactive control "
                "(terminal-adjusted)."
            )
    return CategoricalPayload(
        x_labels=[METHODS[name] for name in available],
        y_values={GOALS[mode][0]: list(available.values())} if mode in GOALS else {},
        x_colours=[SCENARIO_COLOURS[name] for name in available],
        detail=detail,
    )
