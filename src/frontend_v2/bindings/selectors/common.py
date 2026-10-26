"""bindings/selectors/common.py
Shared home for the two questions every selector asks about a run — which run is
picked, and which moment of its replay the user is looking at — and for the
scenario vocabulary and chart-window helpers the payload selectors share."""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from math import ceil, isfinite
from zoneinfo import ZoneInfo

from frontend_v2.engine.data_manager import UI_TIME_KEY
from frontend_v2.sources.replay_clock import UNSET_START_TIME

CURRENT_REQUESTS_AND_SETTINGS = frozenset(
    {"get_all_simulation_requests", "get_current_request"}
)

REPLAY_NEEDS = frozenset(
    {
        UI_TIME_KEY,
        "get_all_simulation_requests",
        "get_all_locations",
        "get_current_request",
    }
)
"""What a selector needs to know which run is picked and which moment of it the
user is looking at."""

WINDOW_NEEDS = frozenset({UI_TIME_KEY, "get_current_request", "time_duration"})
"""What a selector needs to honour the chart duration the user picked: the moment
being replayed, and the duration itself, both off the app settings."""

SCENARIO_GETTERS = {
    "optimised_battery": "get_current_optimised_battery_outputs",
    "reactive_battery": "get_current_reactive_battery_outputs",
    "perfect_foresight": "get_current_perfect_foresight_outputs",
}
"""Scenario name to the getter holding that scenario's rows."""

OPTIMISED = "optimised_battery"
PERFECT_FORESIGHT = "perfect_foresight"
NO_BATTERY = "no_battery_shared"

# Every scenario, in the order the charts show them
SCENARIO_LABELS: dict[str, str] = {
    OPTIMISED: "Optimised",
    "reactive_battery": "Reactive",
    PERFECT_FORESIGHT: "Perfect foresight",
    NO_BATTERY: "No battery (shared)",
    "no_battery_individual": "No battery (individual)",
}

# Scenarios with a battery, so with a state of charge
BATTERY_SCENARIOS = (OPTIMISED, "reactive_battery", PERFECT_FORESIGHT)

KG_PER_TONNE = 1000

# What _timeline reads, for the series selectors that use it
TIMELINE_NEEDS = {
    "get_current_scenario_timeline",
    "get_all_simulation_requests",
    "get_all_locations",
    "get_current_request",
}

# Power flows: the label shown, and the get_current_energy_flows field
FLOW_FIELDS: dict[str, str] = {
    "Demand": "demand_kw",
    "PV generation": "pv_kw",
    "Grid import": "grid_import_kw",
    "Grid export": "grid_export_kw",
    "Curtailed PV": "curtailed_pv_kw",
    "Battery charging": "charge_kw",
    "Battery discharging": "discharge_kw",
}

# What house_count reads, for every selector that divides by it
HOUSEHOLD_NEEDS = {"get_current_run_houses", "get_current_request"}

TIME_DURATION_SETTING = "time_duration"
"""App-setting key holding how far back the charts reach."""

MAX_PLOT_POINTS = 2000
"""Most points a time series hands to a chart. A year of five-minute steps is
over a hundred thousand points, which no chart can draw smoothly."""


@dataclass(frozen=True)
class Duration:
    """One entry of the duration picker: what it is called, and how far back it
    reaches. A span of None means the whole run."""

    label: str
    span: timedelta | None


WHOLE_RUN_DURATION = "full"
"""Duration key for showing the whole run rather than a trailing window."""

CUSTOM_DURATION_PREFIX = "custom:"
"""Prefix for a custom duration key; the remainder is its length in minutes."""

TIME_DURATIONS: dict[str, Duration] = {
    "1h": Duration("Last 1 hour", timedelta(hours=1)),
    "2h": Duration("Last 2 hours", timedelta(hours=2)),
    "6h": Duration("Last 6 hours", timedelta(hours=6)),
    "12h": Duration("Last 12 hours", timedelta(hours=12)),
    "24h": Duration("Last 24 hours", timedelta(hours=24)),
    "3d": Duration("Last 3 days", timedelta(days=3)),
    "7d": Duration("Last 7 days", timedelta(days=7)),
    WHOLE_RUN_DURATION: Duration("Whole run", None),
}
"""The duration picker's entries, in the order they are offered."""


def _format_minutes(minutes: float) -> str:
    """Minutes without a needless decimal part or exponent."""
    return str(int(minutes)) if minutes.is_integer() else f"{minutes:.12g}"


def custom_duration_key(minutes: float) -> str:
    """Encode a custom duration, stored in minutes, as a setting value."""
    return f"{CUSTOM_DURATION_PREFIX}{_format_minutes(minutes)}"


def duration_entry(picked: str) -> Duration | None:
    """Resolve a fixed, whole-run or custom duration key, or None if invalid."""
    fixed = TIME_DURATIONS.get(picked)
    if fixed is not None:
        return fixed
    if not isinstance(picked, str) or not picked.startswith(CUSTOM_DURATION_PREFIX):
        return None
    try:
        minutes = float(picked[len(CUSTOM_DURATION_PREFIX) :])
    except ValueError:
        return None
    if not isfinite(minutes) or minutes <= 0:
        return None
    try:
        span = timedelta(minutes=minutes)
    except OverflowError:
        return None
    return Duration(f"Custom ({_format_minutes(minutes)} min)", span)


def replay_time(data: dict) -> datetime | None:
    """The moment the replay head is sitting on, or None when no run is being
    replayed. Selectors that use it must list UI_TIME_KEY in their @needs."""
    moment = data[UI_TIME_KEY]
    if moment is None or moment == UNSET_START_TIME:
        return None
    return moment


def chosen_duration(data: dict) -> str:
    """The chosen duration, defaulting obsolete or missing choices to 24 hours.
    Custom durations are kept as their encoded minutes.
    Selectors must list TIME_DURATION_SETTING in their @needs."""
    picked = data[TIME_DURATION_SETTING]
    if isinstance(picked, str) and duration_entry(picked) is not None:
        return picked
    return "24h"


def time_duration(data: dict) -> timedelta | None:
    """How far back from the replay head the charts reach, or None for the
    whole run.

    Selectors declare TIME_DURATION_SETTING in their @needs.
    """
    entry = duration_entry(chosen_duration(data))
    return None if entry is None else entry.span


def replay_window(data: dict) -> tuple[datetime | None, datetime] | None:
    """The stretch of the run the user asked to see: the picked duration ending
    at the moment being replayed. None when nothing is being replayed. The whole
    run (or a duration too long for the calendar) starts at the beginning of the
    run, so the start is None. Selectors that use it must list WINDOW_NEEDS in
    their @needs."""
    moment = replay_time(data)
    if moment is None:
        return None
    span = time_duration(data)
    if span is None:
        return None, moment
    try:
        return moment - span, moment
    except OverflowError:
        return None, moment


def window_mask(data: dict, stamps) -> list[bool]:
    """Which of `stamps` fall inside the window the user asked to see, one flag
    per stamp and in the same order. Every stamp when there is no window, so
    callers apply the result without checking first. Stored stamps are ISO 8601
    UTC text, so their text order is chronological order.

    Selectors that use it must list WINDOW_NEEDS in their @needs."""
    window = replay_window(data)
    if window is None:
        return [True] * len(stamps)
    earliest, latest = window
    latest_text = latest.strftime("%Y-%m-%dT%H:%M:%S")
    if earliest is None:
        return [str(stamp)[:19] <= latest_text for stamp in stamps]
    earliest_text = earliest.strftime("%Y-%m-%dT%H:%M:%S")
    return [earliest_text <= str(stamp)[:19] <= latest_text for stamp in stamps]


def forward_window_mask(data: dict, stamps) -> list[bool]:
    """Which of `stamps` fall in the chosen duration starting at the replay
    moment, one flag per stamp and in the same order. Forecast selectors use
    this so a custom duration trims the future instead of the past. Every stamp
    when no window is active.

    Selectors that use it must list the replay moment and time_duration in
    their @needs."""
    moment = replay_time(data)
    span = time_duration(data)
    if moment is None or span is None:
        return [True] * len(stamps)
    try:
        latest = moment + span
    except OverflowError:
        return [True] * len(stamps)
    earliest_text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    latest_text = latest.strftime("%Y-%m-%dT%H:%M:%S")
    return [earliest_text <= str(stamp)[:19] <= latest_text for stamp in stamps]


def plot_slice(count: int) -> slice:
    """A slice keeping at most MAX_PLOT_POINTS of `count` values, evenly spaced.
    Applying the one slice to the x values and to every y series keeps them
    aligned."""
    return slice(None, None, max(1, ceil(count / MAX_PLOT_POINTS)))


def current_request_id(data: dict) -> str | None:
    """The request_id picked in Settings, or None if none is picked. Selectors
    that use it must list "get_current_request" in their @needs."""
    current = data["get_current_request"]
    return str(current[0]["request_id"]) if len(current) else None


def current_request_rows(data: dict, key: str):
    """Rows of a get_current_* getter that belong to the run picked in Settings.
    Buffers keep rows from previously picked runs, so filter with this. Selectors
    that use it must list key and "get_current_request" in their @needs."""
    rows = data[key]
    return rows[rows["request_id"] == current_request_id(data)]


def current_request_scenario_rows(data: dict, scenario_name: str):
    """Rows of one scenario of the run picked in Settings, in replay order.

    Selectors that use it must list "get_current_request" and the getter named
    by SCENARIO_GETTERS in their @needs.

    Raises:
        ValueError: for a scenario name no getter holds.
    """
    return current_request_rows(data, scenario_getter(scenario_name))


def scenario_getter(scenario_name: str) -> str:
    """The getter that holds a scenario's rows.

    Raises:
        ValueError: for a scenario name no getter holds.
    """
    if scenario_name not in SCENARIO_GETTERS:
        raise ValueError(f'Unknown scenario name: "{scenario_name}"')
    return SCENARIO_GETTERS[scenario_name]


def current_request_timezone(data: dict) -> tzinfo:
    """Timezone of the location of the run picked in Settings; UTC if no run is
    picked or its location is unknown. Selectors that use it must list
    CURRENT_REQUESTS_AND_SETTINGS and "get_all_locations" in their @needs."""
    requests = data["get_all_simulation_requests"]
    request = requests[requests["request_id"] == current_request_id(data)]
    if not len(request):
        return UTC

    locations = data["get_all_locations"]
    location = locations[locations["location_name"] == request[0]["location_name"]]
    return ZoneInfo(str(location[0]["timezone"])) if len(location) else UTC


def current_request_times(data: dict, stamps) -> tuple[list[datetime], str]:
    """ISO 8601 UTC stamps as datetimes in the timezone of the run picked in
    Settings, and an x axis label naming that timezone. Selectors that use it
    must list what current_request_timezone needs."""
    zone = current_request_timezone(data)
    times = [datetime.fromisoformat(stamp).astimezone(zone) for stamp in stamps]
    return times, f"Time ({times[0].tzname() if times else 'UTC'})"


def last_row_at_or_before(rows, replay_moment: datetime, column: str):
    """The row whose `column` is the latest one at or before `replay_moment`, or
    None when the rows hold nothing that early.

    Stored stamps are ISO 8601 UTC text ("2026-10-11T08:55:00+00:00"), so their
    text order is chronological order and the search is a plain binary search
    over that text."""
    moment = replay_moment.strftime("%Y-%m-%dT%H:%M:%S")
    stamps = [str(stamp)[:19] for stamp in rows[column]]
    position = bisect_right(stamps, moment) - 1
    return None if position < 0 else rows[position]


def row_at_replay(data: dict, key: str, column: str = "timestamp_utc"):
    """The row of a time series in force at the moment being replayed, or None
    when the app is not replaying or the series has not reached that moment yet.

    A series runs at a coarser step than the replay head, so this is the row in
    force then, not a row exactly on the replayed moment."""
    moment = replay_time(data)
    if moment is None:
        return None
    return last_row_at_or_before(current_request_rows(data, key), moment, column)


def by_scenario(rows) -> dict:
    """Rows keyed by scenario name, in the order the charts show them."""
    return in_scenario_order({row["scenario_name"]: row for row in rows})


def in_scenario_order(by_name: dict) -> dict:
    """by_name's scenarios, in the order the charts show them."""
    return {name: by_name[name] for name in SCENARIO_LABELS if name in by_name}


def house_count(data: dict) -> int:
    """How many houses the run picked in Settings had, of every config. Selectors
    that use it must list HOUSEHOLD_NEEDS in their @needs."""
    houses = current_request_rows(data, "get_current_run_houses")
    return int(houses["quantity"].sum())


def battery_capacity(data: dict) -> float | None:
    """Positive finite capacity of the selected run's battery, or unavailable.

    Callers declare CURRENT_REQUESTS_AND_SETTINGS and get_all_battery_configs.
    """
    requests = current_request_rows(data, "get_all_simulation_requests")
    if not len(requests):
        return None
    configs = data["get_all_battery_configs"]
    battery = configs[configs["battery_config_id"] == requests[0]["battery_config_id"]]
    if not len(battery):
        return None
    capacity = float(battery[0]["capacity_kwh"])
    return capacity if isfinite(capacity) and capacity > 0 else None
