"""bindings/selectors/series.py
Selectors that return a SeriesPayload: one value per point on an axis."""

from datetime import datetime

import numpy as np

from database_v2.utils.forecast_issue import forecast_issue_at
from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.bindings.selectors.common import (
    BATTERY_SCENARIOS,
    CURRENT_REQUESTS_AND_SETTINGS,
    FLOW_FIELDS,
    HOUSEHOLD_NEEDS,
    NO_BATTERY,
    REPLAY_NEEDS,
    SCENARIO_LABELS,
    TIME_DURATION_SETTING,
    TIMELINE_NEEDS,
    WINDOW_NEEDS,
    battery_capacity,
    current_request_rows,
    current_request_scenario_rows,
    current_request_times,
    forward_window_mask,
    house_count,
    in_scenario_order,
    plot_slice,
    replay_time,
    window_mask,
)
from frontend_v2.ui.theme import SCENARIO_COLOURS
from frontend_v2.utils.payload import SeriesPayload

# What select_household_day reads: the flows, and the location's timezone
DAY_NEEDS = {
    "get_current_energy_flows",
    "get_all_simulation_requests",
    "get_all_locations",
    *HOUSEHOLD_NEEDS,
}


def _shown(values, mask: list[bool]) -> list:
    """The values a window mask keeps, in the order they were given. The mask has
    one flag per value, so the two lengths only differ if a caller has a bug."""
    return [value for value, keep in zip(values, mask, strict=True) if keep]


@needs(
    WINDOW_NEEDS
    | CURRENT_REQUESTS_AND_SETTINGS
    | {
        "get_all_locations",
        "get_current_realised_data",
        "get_current_optimised_battery_outputs",
    }
)
def select_community_energy_balance(data: dict) -> SeriesPayload:
    realised = current_request_rows(data, "get_current_realised_data")
    live = current_request_scenario_rows(data, "optimised_battery")
    live_by_timestamp = {str(row["timestamp_utc"]): row for row in live}
    common_timestamps = [
        str(timestamp)
        for timestamp in realised["timestamp_utc"]
        if str(timestamp) in live_by_timestamp
    ]
    live_rows = [live_by_timestamp[timestamp] for timestamp in common_timestamps]
    realised_by_timestamp = {str(row["timestamp_utc"]): row for row in realised}
    realised_rows = [
        realised_by_timestamp[timestamp] for timestamp in common_timestamps
    ]

    # The window the user picked, then thin, so a year of five-minute steps is
    # not handed to the chart whole. All three lists keep the same positions.
    inside = window_mask(data, common_timestamps)
    common_timestamps = _shown(common_timestamps, inside)
    realised_rows = _shown(realised_rows, inside)
    live_rows = _shown(live_rows, inside)
    thin = plot_slice(len(common_timestamps))
    times, x_label = current_request_times(data, common_timestamps[thin])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Demand (kW)": [row["demand_kw"] for row in realised_rows][thin],
            "PV (kW)": [row["pv_kw"] for row in realised_rows][thin],
            "Grid import (kW)": [row["grid_import_kw"] for row in live_rows][thin],
            "Grid export (kW)": [row["grid_export_kw"] for row in live_rows][thin],
        },
    )


@needs(
    WINDOW_NEEDS
    | CURRENT_REQUESTS_AND_SETTINGS
    | {"get_all_locations", "get_current_optimised_battery_outputs"}
)
def select_storage_and_curtailment(data: dict) -> SeriesPayload:
    rows = current_request_scenario_rows(data, "optimised_battery")
    rows = rows[window_mask(data, rows["timestamp_utc"])]
    thin = plot_slice(len(rows))
    times, x_label = current_request_times(data, rows["timestamp_utc"][thin])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Battery charge (kW)": rows["charge_kw"][thin],
            "Battery discharge (kW)": rows["discharge_kw"][thin],
            "Curtailment (kW)": rows["curtailed_pv_kw"][thin],
        },
    )


@needs(
    WINDOW_NEEDS
    | CURRENT_REQUESTS_AND_SETTINGS
    | {
        "get_all_locations",
        "get_current_optimiser_outputs",
        "get_current_optimised_battery_outputs",
    }
)
def select_plan_vs_live_battery_action(data: dict) -> SeriesPayload:
    plan = current_request_rows(data, "get_current_optimiser_outputs")
    live = current_request_scenario_rows(data, "optimised_battery")
    requests = current_request_rows(data, "get_all_simulation_requests")
    if not len(requests):
        return SeriesPayload(x_label="Time (UTC)", x_values=[], y_series={})

    # Buffers thin the two streams independently. Exact timestamp intersections
    # can disappear; each live sample belongs to a request-anchored plan interval.
    plan_by_issue = {
        datetime.fromisoformat(str(row["timestamp_utc"])): row for row in plan
    }
    start = datetime.fromisoformat(str(requests[0]["start_time_utc"]))
    end = datetime.fromisoformat(str(requests[0]["end_time_utc"]))
    live = live[window_mask(data, live["timestamp_utc"])]
    live = live[plot_slice(len(live))]
    plan_rows = [
        plan_by_issue.get(
            forecast_issue_at(start, end, datetime.fromisoformat(str(stamp)))
        )
        for stamp in live["timestamp_utc"]
    ]
    times, x_label = current_request_times(data, live["timestamp_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Plan charge (kW)": [
                row["charge_kw"] if row is not None else np.nan for row in plan_rows
            ],
            "Plan discharge (kW)": [
                row["discharge_kw"] if row is not None else np.nan for row in plan_rows
            ],
            "Live charge (kW)": live["charge_kw"],
            "Live discharge (kW)": live["discharge_kw"],
        },
    )


@needs(
    WINDOW_NEEDS
    | CURRENT_REQUESTS_AND_SETTINGS
    | {
        "get_all_locations",
        "get_current_optimised_battery_outputs",
        "get_all_battery_configs",
    }
)
def select_community_battery_soc_history(data: dict) -> SeriesPayload:
    rows = current_request_scenario_rows(data, "optimised_battery")
    rows = rows[window_mask(data, rows["timestamp_utc"])]
    thin = plot_slice(len(rows))
    times, x_label = current_request_times(data, rows["timestamp_utc"][thin])
    capacity = battery_capacity(data)
    percentages = rows["soc_kwh"][thin] / capacity * 100 if capacity else []
    return SeriesPayload(
        x_label=x_label,
        x_values=times if capacity else [],
        y_series={"SOC (%)": percentages},
    )


def _forecast_at_replay(data: dict):
    """Keep the previous issue visible while its replacement is loading.

    Only use issues from this run at or before the replay interval. An empty
    fetched snapshot still shows unavailable; future issues never leak backward.
    """
    moment = replay_time(data)
    requests = current_request_rows(data, "get_all_simulation_requests")
    if moment is None or not len(requests):
        return None, False
    issue = forecast_issue_at(
        datetime.fromisoformat(requests[0]["start_time_utc"]),
        datetime.fromisoformat(requests[0]["end_time_utc"]),
        moment,
    )
    if issue is None:
        return None, False
    rows = current_request_rows(data, "get_current_forecast_data")
    available = rows[rows["run_time_utc"] <= issue.isoformat()]
    if not len(available):
        return None, False
    shown_issue = max(available["run_time_utc"])
    horizon = available[available["run_time_utc"] == shown_issue]
    return (
        horizon[np.argsort(horizon["target_time_utc"].astype(str))],
        shown_issue != issue.isoformat(),
    )


def _forecast_label(data: dict, rows, updating: bool) -> str:
    """Identify the displayed issue and any pending replacement."""
    if rows is None:
        return "Forecast unavailable for this replay interval."
    times, _ = current_request_times(data, [str(rows[0]["run_time_utc"])])
    label = f"Forecast issued at {times[0]:%Y-%m-%d %H:%M %Z}"
    return f"{label} · Updating forecast…" if updating else label


@needs(REPLAY_NEEDS | {"get_current_forecast_data", TIME_DURATION_SETTING})
def select_predicted_community_pv(data: dict) -> SeriesPayload:
    rows, updating = _forecast_at_replay(data)
    if rows is None:
        return SeriesPayload(
            x_label="Time (UTC)",
            x_values=[],
            y_series={"Predicted PV (kW)": []},
            subtitle=_forecast_label(data, rows, updating),
        )
    subtitle = _forecast_label(data, rows, updating)
    rows = rows[forward_window_mask(data, rows["target_time_utc"])]
    times, x_label = current_request_times(data, rows["target_time_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={"Predicted PV (kW)": rows["pv_kw"]},
        subtitle=subtitle,
    )


@needs(REPLAY_NEEDS | {"get_current_forecast_data", TIME_DURATION_SETTING})
def select_predicted_buy_and_sell_prices(data: dict) -> SeriesPayload:
    rows, updating = _forecast_at_replay(data)
    if rows is None:
        return SeriesPayload(
            x_label="Time (UTC)",
            x_values=[],
            y_series={"Buy price (c/kWh)": [], "Sell price (c/kWh)": []},
            subtitle=_forecast_label(data, rows, updating),
        )
    subtitle = _forecast_label(data, rows, updating)
    rows = rows[forward_window_mask(data, rows["target_time_utc"])]
    times, x_label = current_request_times(data, rows["target_time_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Buy price (c/kWh)": rows["buy_tariff_aud_per_kwh"] * 100,
            "Sell price (c/kWh)": rows["sell_tariff_aud_per_kwh"] * 100,
        },
        subtitle=subtitle,
    )


@needs(TIMELINE_NEEDS)
def select_cumulative_cost(data: dict) -> SeriesPayload:
    """Running net cost of each battery scenario, and of no battery."""
    names = (*BATTERY_SCENARIOS, NO_BATTERY)
    return _timeline("cumulative_cost_aud", "AUD", data, names)


@needs(TIMELINE_NEEDS)
def select_battery_soc(data: dict) -> SeriesPayload:
    """Battery state of charge of each battery scenario, as a percentage of its
    nominal capacity."""
    return _timeline("soc_percent", "%", data, BATTERY_SCENARIOS)


@needs(DAY_NEEDS)
def select_household_day(data: dict) -> SeriesPayload:
    """The average household's demand and solar generation over the run, in the
    location's time. Grid flows are left out: they are the shared battery's, which
    dwarf one house's own use."""
    rows = current_request_rows(data, "get_current_energy_flows")
    count = house_count(data)
    if not count:
        rows = rows[:0]
    times, x_label = current_request_times(data, rows["timestamp_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Demand (kW)": rows["demand_kw"] / max(count, 1),
            "PV generation (kW)": rows["pv_kw"] / max(count, 1),
        },
    )


@needs(DAY_NEEDS)
def select_community_net_flows(data: dict) -> SeriesPayload:
    """The community's net grid and net battery power over the run, in the
    location's time. Positive is buying from the grid, or the battery supplying
    power; negative is selling to the grid, or the battery charging."""
    rows = current_request_rows(data, "get_current_energy_flows")
    times, x_label = current_request_times(data, rows["timestamp_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            "Net grid (kW)": rows["grid_import_kw"] - rows["grid_export_kw"],
            "Net battery (kW)": rows["discharge_kw"] - rows["charge_kw"],
        },
    )


POWER_NEEDS = {
    "get_current_energy_flows",
    "get_all_simulation_requests",
    "get_all_locations",
    "get_current_request",
}


def _power(data: dict, labels: tuple[str, ...]) -> SeriesPayload:
    """The named FLOW_FIELDS power flows over the run, in the location's time."""
    rows = current_request_rows(data, "get_current_energy_flows")
    times, x_label = current_request_times(data, rows["timestamp_utc"])
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={f"{label} (kW)": rows[FLOW_FIELDS[label]] for label in labels},
    )


@needs(POWER_NEEDS)
def select_community_power(data: dict) -> SeriesPayload:
    """Community demand, PV generation and curtailed PV over the run."""
    return _power(data, ("Demand", "PV generation", "Curtailed PV"))


@needs(POWER_NEEDS)
def select_grid_power(data: dict) -> SeriesPayload:
    """Community grid import and export over the run."""
    return _power(data, ("Grid import", "Grid export"))


@needs(POWER_NEEDS)
def select_battery_power(data: dict) -> SeriesPayload:
    """Community battery charging and discharging over the run."""
    return _power(data, ("Battery charging", "Battery discharging"))


def _timeline(field: str, unit: str, data: dict, names: tuple) -> SeriesPayload:
    """One series per scenario in names of a timeline field, over the timestamps
    every one of them has, in the location's time."""
    rows = current_request_rows(data, "get_current_scenario_timeline")
    by_scenario: dict[str, dict[str, float]] = {}
    for row in rows:
        if row["scenario_name"] in names:
            by_scenario.setdefault(row["scenario_name"], {})[row["timestamp_utc"]] = (
                row[field]
            )
    scenarios = in_scenario_order(by_scenario)

    # Rows of a previously picked run can leave no rows for the current one
    timestamps = sorted(
        set.intersection(*(set(values) for values in scenarios.values()))
        if scenarios
        else set()
    )
    keys = {name: f"{SCENARIO_LABELS[name]} ({unit})" for name in scenarios}
    times, x_label = current_request_times(data, timestamps)
    return SeriesPayload(
        x_label=x_label,
        x_values=times,
        y_series={
            keys[name]: [values[t] for t in timestamps]
            for name, values in scenarios.items()
        },
        colours={keys[name]: SCENARIO_COLOURS[name] for name in scenarios},
    )
