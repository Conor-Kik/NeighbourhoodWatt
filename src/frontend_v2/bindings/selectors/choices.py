"""bindings/selectors/choices.py
Selectors that return a ChoicesPayload: records to pick from, and the pick."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from frontend_v2.bindings.needs_decorator import needs
from frontend_v2.bindings.selectors.common import (
    TIME_DURATION_SETTING,
    TIME_DURATIONS,
    chosen_duration,
    current_request_id,
    duration_entry,
)
from frontend_v2.bindings.selectors.records import select_battery_config_records
from frontend_v2.utils.payload import ChoicesPayload


@needs(
    {
        "get_all_simulation_requests",
        "get_current_request",
        "get_all_battery_configs",
        "get_all_locations",
    }
)
def select_request_choices(data: dict) -> ChoicesPayload:
    """Every saved run, labelled for picking, and the run currently picked."""
    requests = data["get_all_simulation_requests"]
    batteries = select_battery_config_records(
        {"get_all_battery_configs": data["get_all_battery_configs"]}
    ).labels
    timezones = {
        str(row["location_name"]): str(row["timezone"])
        for row in data["get_all_locations"]
    }
    # Buffers order by request_id; newest start first reads better in a list
    ordered = sorted(requests, key=lambda row: str(row["start_time_utc"]), reverse=True)
    return ChoicesPayload(
        choices={
            str(row["request_id"]): _request_label(
                row,
                batteries.get(
                    str(row["battery_config_id"]), str(row["battery_config_id"])
                ),
                timezones.get(str(row["location_name"]), "UTC"),
            )
            for row in ordered
        },
        current=current_request_id(data),
    )


@needs({TIME_DURATION_SETTING})
def select_time_duration_choices(data: dict) -> ChoicesPayload:
    """The durations the charts can show, and the one currently picked."""
    current = chosen_duration(data)
    choices = {key: entry.label for key, entry in TIME_DURATIONS.items()}
    entry = duration_entry(current)
    if entry is not None and current not in choices:
        choices[current] = entry.label
    return ChoicesPayload(choices=choices, current=current)


def _request_label(row, battery: str, timezone: str) -> str:
    """Show scannable run facts with dates in the run's location timezone."""
    zone = ZoneInfo(timezone)
    dates = []
    for field in ("start_time_utc", "end_time_utc"):
        stamp = datetime.fromisoformat(str(row[field]))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        dates.append(stamp.astimezone(zone).strftime("%d %b %Y"))
    count = int(row["house_count"])
    houses = "house" if count == 1 else "houses"
    return (
        f"{row['location_name']} · {dates[0]} → {dates[1]}\n"
        f"{count} {houses} · {row['objective_mode']} · {battery} · "
        f"Run {str(row['request_id'])[:8]}"
    )
