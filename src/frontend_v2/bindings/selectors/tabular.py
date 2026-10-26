"""bindings/selectors/tabular.py
Selectors that return a TabularPayload: a header row and one row per entry."""

import math
from datetime import datetime

from database_v2.data_access_objects.frontend_dao import TIMELINE_STEP_MINUTES
from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.bindings.selectors.choices import select_request_choices
from frontend_v2.bindings.selectors.common import (
    FLOW_FIELDS,
    KG_PER_TONNE,
    NO_BATTERY,
    OPTIMISED,
    PERFECT_FORESIGHT,
    REPLAY_NEEDS,
    battery_capacity,
    by_scenario,
    current_request_id,
    current_request_rows,
    current_request_timezone,
    replay_time,
    row_at_replay,
)
from frontend_v2.bindings.selectors.objectives import GOALS, _goal_value, _mode
from frontend_v2.engine.data_manager import (
    UI_DURATION_KEY,
    UI_OPTIMISER_KEY,
    UI_TIME_KEY,
)
from frontend_v2.ui.views.replay_bar import PAYLOAD_HEADERS
from frontend_v2.utils.payload import TabularPayload

NO_VALUE = "-"
"""Shown when the run has nothing to say about the moment being replayed."""

NOT_AVAILABLE = "—"
"""Shown in a number tile when the scenarios it compares are missing."""

COST_NEEDS = {
    "get_current_operating_costs",
    "get_all_simulation_requests",
    "get_current_request",
}

MINUTES_PER_HOUR = 60


def _local_time(data: dict, moment: datetime) -> str:
    """A moment written out in the timezone of the run being replayed."""
    return moment.astimezone(current_request_timezone(data)).strftime("%Y-%m-%d %H:%M")


@needs({UI_TIME_KEY, UI_DURATION_KEY, UI_OPTIMISER_KEY})
def select_replay_state(data: dict) -> TabularPayload:
    """Hand the replay clock's own state to the replay bar, unformatted.

    The time is None until the clock has been given a run, which is the bar's
    empty state; the clock stores it as its unset sentinel."""
    return TabularPayload(
        headers=list(PAYLOAD_HEADERS),
        rows=[(replay_time(data), data[UI_DURATION_KEY], data[UI_OPTIMISER_KEY])],
    )


@needs(REPLAY_NEEDS)
def select_current_replay_time(data: dict) -> TabularPayload:
    """The moment the replay head is sitting on, in the run's own time."""
    moment = replay_time(data)
    value = NO_VALUE if moment is None else _local_time(data, moment)
    return TabularPayload(headers=["Current replay time"], rows=[(value,)])


@needs(
    REPLAY_NEEDS | {"get_current_optimised_battery_outputs", "get_all_battery_configs"}
)
def select_community_battery(data: dict) -> TabularPayload:
    """Recorded SOC percentage, with stored energy alongside, at replay time."""
    row = row_at_replay(data, "get_current_optimised_battery_outputs")
    if row is None or not math.isfinite(row["soc_kwh"]):
        return _tile(NO_VALUE)
    capacity = battery_capacity(data)
    percent = row["soc_kwh"] / capacity * 100 if capacity else math.nan
    value = f"{percent:.1f}%" if math.isfinite(percent) else NO_VALUE
    return _tile(value, f"{row['soc_kwh']:,.1f} kWh stored")


@needs(REPLAY_NEEDS | {"get_current_pricing_data"})
def select_live_prices(data: dict) -> TabularPayload:
    """Import and export tariffs in force at the moment being replayed."""
    row = row_at_replay(data, "get_current_pricing_data")
    if row is None:
        value = NO_VALUE
    else:
        value = (
            f"Buy {row['buy_tariff_aud_per_kwh'] * 100:.2f} · "
            f"Sell {row['sell_tariff_aud_per_kwh'] * 100:.2f} c/kWh"
        )
    return TabularPayload(headers=["Live prices"], rows=[(value,)])


@needs(REPLAY_NEEDS | {"get_current_replay_cost"})
def select_operating_cost(data: dict) -> TabularPayload:
    """Recorded financial cost before replay time, independent of chart duration."""
    row = row_at_replay(data, "get_current_replay_cost")
    if row is None or not math.isfinite(row["cumulative_cost_aud"]):
        return _tile(NO_VALUE, "No recorded cost at this replay time")
    cost = row["cumulative_cost_aud"]
    value = _aud(cost)
    minutes = row["recent_minutes"]
    recent = row["recent_cost_aud"]
    if minutes == 0:
        detail = "No elapsed intervals"
    elif not math.isfinite(recent):
        detail = f"Last {minutes} min: unavailable"
    else:
        amount = f"{_aud(abs(recent))} net earnings" if recent < 0 else _aud(recent)
        detail = f"Last {minutes} min: {amount}"
    recorded_at = datetime.fromisoformat(str(row["timestamp_utc"]))
    if cost < 0:
        detail = f"Net earnings so far\n{detail}"
    detail += f"\nRecorded through {_local_time(data, recorded_at)}"
    return _tile(value, detail)


@needs(select_request_choices.needs)
def select_current_run(data: dict) -> TabularPayload:
    """The run the debrief shows, described in one line."""
    choices = select_request_choices(data)
    if choices.current not in choices.choices:
        return _tile("No run picked")
    return _tile(choices.choices[choices.current])


@needs(COST_NEEDS)
def select_savings(data: dict) -> TabularPayload:
    """How much cheaper the optimised battery ran than no battery. Both costs
    are net of the optimised battery's terminal-value estimate; a negative saving
    is shown as an additional cost."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    if OPTIMISED not in costs or NO_BATTERY not in costs:
        return _tile(NOT_AVAILABLE)
    optimised = _comparison_cost(data, costs[OPTIMISED])
    baseline = costs[NO_BATTERY]["comparison_economic_cost_aud"]
    if optimised is None:
        return _tile(NOT_AVAILABLE, "Terminal-adjusted estimate unavailable")
    saved = baseline - optimised
    detail = (
        f"Without a battery: {_pay_or_earn(baseline)}\n"
        f"With the smart battery: {_pay_or_earn(optimised)}"
    )
    if saved < 0:
        return _tile(f"{_aud(-saved)} additional cost", detail)
    return _tile(f"{_aud(saved)} saved", detail)


@needs(COST_NEEDS)
def select_foresight_gap(data: dict) -> TabularPayload:
    """Compare the saved objective with hindsight, crediting smart remaining energy."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    if OPTIMISED not in costs or PERFECT_FORESIGHT not in costs:
        return _tile(NOT_AVAILABLE)
    mode = _mode(data)
    optimised = _goal_value(costs[OPTIMISED], mode)
    foresight = _goal_value(costs[PERFECT_FORESIGHT], mode, terminal_adjusted=False)
    if optimised is None or foresight is None:
        return _tile(
            NOT_AVAILABLE, "Matching objective or terminal estimate unavailable"
        )
    gap = optimised - foresight
    if not math.isfinite(gap):
        return _tile(NOT_AVAILABLE, "Objective comparison unavailable")
    gap = round(gap, 1 if mode == "Emissions" else 2)

    def amount(value: float) -> str:
        if mode == "Emissions":
            return _kg(value / KG_PER_TONNE)
        return _aud(value)

    # Keep the headline compact; the smaller metric line supplies the full unit.
    detail = (
        f"{GOALS[mode][0]} · terminal-adjusted\n"
        f"Smart battery: {amount(optimised)}\n"
        f"Perfect foresight: {amount(foresight)}"
    )
    if gap > 0:
        return _tile(f"{amount(gap)} behind", detail)
    if gap < 0:
        return _tile(f"{amount(-gap)} ahead", detail)
    return _tile("Level", detail)


@needs(COST_NEEDS)
def select_emissions_avoided(data: dict) -> TabularPayload:
    """Emissions the optimised battery saved against no battery, net of the
    optimised battery's terminal-value estimate for the run's objective."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    if OPTIMISED not in costs or NO_BATTERY not in costs:
        return _tile(NOT_AVAILABLE)
    field = (
        "estimated_terminal_balanced_emissions_value_tonnes"
        if _mode(data) == "Balanced"
        else "estimated_terminal_emissions_value_tonnes"
    )
    if field.startswith("estimated_terminal_balanced") and math.isnan(
        costs[OPTIMISED][field]
    ):
        return _tile(NOT_AVAILABLE, "Terminal-adjusted estimate unavailable")
    avoided = _emissions_avoided_tonnes(costs, field)
    baseline = costs[NO_BATTERY]["emissions_tonnes"]
    detail = (
        f"Without a battery: {_kg(baseline)}\n"
        f"With the smart battery: {_kg(baseline - avoided)}"
    )
    amount = f"{abs(avoided) * KG_PER_TONNE:,.0f} kg"
    return _tile(f"{amount} {'less' if avoided >= 0 else 'more'}", detail)


@needs(COST_NEEDS)
def select_terminal_value(data: dict) -> TabularPayload:
    """Estimated financial value of the energy left in the optimised battery at
    the end of the run. It is an estimate, not cash, and is kept separate from
    the emissions estimate."""
    costs = by_scenario(current_request_rows(data, "get_current_operating_costs"))
    if OPTIMISED not in costs:
        return _tile(NOT_AVAILABLE)
    optimised = costs[OPTIMISED]
    value = optimised[_credit_field(data)]
    if math.isnan(value):
        return _tile(NOT_AVAILABLE)
    return _tile(
        _aud(value),
        f"Estimate for the {optimised['terminal_soc_kwh']:,.1f} kWh left in the "
        "battery at the end. Not cash.",
    )


RUN_TOTAL_NEEDS = {"get_current_energy_flows", "get_current_request"}


def _run_total(data: dict, label: str) -> TabularPayload:
    """Energy of one flow over the whole run, as a number tile."""
    rows = current_request_rows(data, "get_current_energy_flows")
    hours = TIMELINE_STEP_MINUTES / MINUTES_PER_HOUR
    return _tile(f"{float(rows[FLOW_FIELDS[label]].sum()) * hours:,.0f} kWh")


@needs(RUN_TOTAL_NEEDS)
def select_total_demand(data: dict) -> TabularPayload:
    """Community demand over the whole run."""
    return _run_total(data, "Demand")


@needs(RUN_TOTAL_NEEDS)
def select_total_pv(data: dict) -> TabularPayload:
    """Community PV generation over the whole run."""
    return _run_total(data, "PV generation")


@needs(RUN_TOTAL_NEEDS)
def select_total_grid_import(data: dict) -> TabularPayload:
    """Energy imported from the grid over the whole run."""
    return _run_total(data, "Grid import")


@needs(RUN_TOTAL_NEEDS)
def select_total_grid_export(data: dict) -> TabularPayload:
    """Energy exported to the grid over the whole run."""
    return _run_total(data, "Grid export")


@needs(RUN_TOTAL_NEEDS)
def select_total_curtailed_pv(data: dict) -> TabularPayload:
    """PV energy curtailed over the whole run."""
    return _run_total(data, "Curtailed PV")


@needs({"get_current_run_houses", "get_current_request"})
def select_run_houses(data: dict) -> TabularPayload:
    """The house configs of the run, and how many houses use each."""
    houses = current_request_rows(data, "get_current_run_houses")
    return TabularPayload(
        headers=[
            "House config",
            "Houses",
            "PV peak (kW)",
            "Inverter AC (kW)",
            "Extras",
        ],
        rows=[
            (
                str(row["house_config_id"]),
                int(row["quantity"]),
                float(row["pv_peak_capacity"]),
                float(row["pv_inverter_ac_kw"]),
                _extras(row),
            )
            for row in houses
        ],
    )


@needs(
    {"get_all_simulation_requests", "get_all_battery_configs", "get_current_request"}
)
def select_run_battery(data: dict) -> TabularPayload:
    """The battery config the run used, one property per row."""
    headers = ["Property", "Value"]
    requests = data["get_all_simulation_requests"]
    request = requests[requests["request_id"] == current_request_id(data)]
    if not len(request):
        return TabularPayload(headers=headers, rows=[])

    configs = data["get_all_battery_configs"]
    config = configs[configs["battery_config_id"] == request[0]["battery_config_id"]]
    if not len(config):
        return TabularPayload(headers=headers, rows=[])

    battery = config[0]
    return TabularPayload(
        headers=headers,
        rows=[
            ("Config", str(battery["battery_config_id"])),
            ("Capacity (kWh)", float(battery["capacity_kwh"])),
            ("Max power (kW)", float(battery["max_power_kw"])),
            ("Efficiency", _percent(battery["efficiency"])),
            ("Min charge", _percent(battery["min_soc_frac"])),
            ("Max charge", _percent(battery["max_soc_frac"])),
            ("Replacement cost (AUD)", float(battery["replacement_cost_aud"])),
            ("Lifetime (cycles)", int(battery["lifetime_cycles"])),
            ("Max cycles per 24h", float(battery["max_cycles_24h"])),
        ],
    )


def _pay_or_earn(cost: float) -> str:
    """A cost as what the community pays, or earns when the cost is negative."""
    return f"pay {_aud(cost)}" if cost >= 0 else f"earn {_aud(-cost)}"


def _credit_field(data: dict) -> str:
    """The financial terminal estimate matching the run's objective. A Balanced
    run is valued under its own strategy, never from the separate Cost and
    Emissions estimates."""
    if _mode(data) == "Balanced":
        return "estimated_terminal_balanced_financial_value_aud"
    return "estimated_terminal_cost_value_aud"


def _comparison_cost(data: dict, row) -> float | None:
    """A scenario's cost net of its terminal estimate, or None when a Balanced
    run's estimate is missing."""
    if row["scenario_name"] != OPTIMISED:
        return row["comparison_economic_cost_aud"]
    credit = row[_credit_field(data)]
    if math.isnan(credit):
        if _mode(data) == "Balanced":
            return None
        return row["realised_economic_cost_aud"]
    return row["realised_economic_cost_aud"] - credit


def _emissions_avoided_tonnes(
    summaries: dict, field: str = "estimated_terminal_emissions_value_tonnes"
) -> float:
    """Emissions the optimised battery avoided against no battery, in tonnes, net
    of the optimised battery's terminal value. A missing terminal value is NaN,
    which reads as no credit. Callers must check OPTIMISED and NO_BATTERY are
    both in summaries first."""
    optimised = summaries[OPTIMISED]
    terminal = optimised[field]
    credit = 0.0 if math.isnan(terminal) else terminal
    baseline = summaries[NO_BATTERY]["emissions_tonnes"]
    return baseline - optimised["emissions_tonnes"] + credit


def _tile(text: str, detail: str = "") -> TabularPayload:
    """Payload for a number tile showing text, with a line of detail under it."""
    return TabularPayload(headers=["Value", "Detail"], rows=[(text, detail)])


def _aud(value: float) -> str:
    """'$12.34', or '-$1.20' when negative."""
    return _signed(value, ",.2f", "$")


def _kg(tonnes: float) -> str:
    """Tonnes as '139.0 kg', or '-1.5 kg' when negative."""
    return f"{_signed(tonnes * KG_PER_TONNE, ',.1f')} kg"


def _signed(value: float, spec: str, prefix: str = "") -> str:
    """value formatted by spec, with the minus sign before prefix."""
    sign = "-" if value < 0 else ""
    return f"{sign}{prefix}{abs(value):{spec}}"


def _extras(house) -> str:
    """The loads a house config has besides its base demand, such as
    'Aircon, EV'."""
    extras = [
        label
        for label, field in (
            ("Aircon", "has_aircon"),
            ("Pool pump", "has_poolpump"),
            ("EV", "has_ev"),
        )
        if house[field]
    ]
    return ", ".join(extras) if extras else "None"


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"
