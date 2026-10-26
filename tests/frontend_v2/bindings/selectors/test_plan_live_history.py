"""Plan/live comparisons survive independent history sampling."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors.series import select_plan_vs_live_battery_action
from frontend_v2.engine.data_manager import UI_TIME_KEY
from frontend_v2.utils.buffer import DEFAULT_MAXLEN, Buffer
from tests.frontend_v2.bindings.selectors.test_selectors import make_data

DTYPE = [
    ("timestamp_utc", "U32"),
    ("request_id", "U16"),
    ("scenario_name", "U32"),
    ("charge_kw", "f8"),
    ("discharge_kw", "f8"),
]


def history(days=14, start=datetime(2026, 1, 1, tzinfo=UTC)):
    end = start + timedelta(days=days)
    data = make_data(end - timedelta(minutes=5))
    requests = data["get_all_simulation_requests"]
    requests["start_time_utc"] = start.isoformat()
    requests["end_time_utc"] = end.isoformat()
    for key, minutes, power in (
        ("get_current_optimiser_outputs", 30, 10),
        ("get_current_optimised_battery_outputs", 5, 8),
    ):
        buffer = Buffer(DTYPE, "timestamp_utc")
        buffer.replace(
            [
                (
                    (start + timedelta(minutes=index * minutes)).isoformat(),
                    "run-1",
                    "optimised_battery",
                    power,
                    power / 2,
                )
                for index in range(days * 24 * 60 // minutes)
            ]
        )
        data[key] = buffer.get()
    data["time_duration"] = "7d"
    return data


def translate(data):
    binding = ReadBinding(select_plan_vs_live_battery_action)
    return binding.translate({key: data[key] for key in binding.needs})


@pytest.mark.parametrize("days", [14, 60, 365])
def test_actual_buffer_sampling_does_not_blank_long_run_chart(days):
    data = history(days)
    plan = data["get_current_optimiser_outputs"]
    plan_rows = days * 24 * 60 // 30
    live_rows = days * 24 * 60 // 5
    live = data["get_current_optimised_battery_outputs"]
    if live_rows >= DEFAULT_MAXLEN:
        assert not set(plan["timestamp_utc"]) & set(live["timestamp_utc"])
    before = {
        key: rows.copy() for key, rows in data.items() if isinstance(rows, np.ndarray)
    }
    payload = translate(data)
    assert len(payload.x_values) > 0
    np.testing.assert_array_equal(payload.y_series["Live charge (kW)"], 8)
    scheduled = np.asarray(payload.y_series["Plan charge (kW)"])
    assert np.isfinite(scheduled).any()
    assert (scheduled[np.isfinite(scheduled)] == 10).all()
    if plan_rows >= DEFAULT_MAXLEN:
        assert np.isnan(scheduled).any()  # Sampled-out plans are not carried forward.
    for key, rows in before.items():
        np.testing.assert_array_equal(data[key], rows)


def test_interval_alignment_uses_request_start_and_exact_boundaries():
    start = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    data = history(1, start)
    data["get_current_optimiser_outputs"]["charge_kw"][1] = 20
    data[UI_TIME_KEY] = start + timedelta(minutes=30)
    payload = translate(data)
    assert payload.y_series["Plan charge (kW)"] == [10] * 6 + [20]


def test_first_visible_live_sample_uses_plan_just_before_window():
    data = history()
    data["time_duration"] = "24h"
    payload = translate(data)
    first = payload.x_values[0].astimezone(UTC)
    assert first.minute % 30 != 0
    assert payload.y_series["Plan charge (kW)"][0] == 10


@pytest.mark.parametrize("missing", ["all", "interval", "foreign_run"])
def test_missing_schedule_preserves_live_without_using_an_old_plan(missing):
    data = history(1)
    plan = data["get_current_optimiser_outputs"]
    if missing == "all":
        data["get_current_optimiser_outputs"] = plan[:0]
    elif missing == "foreign_run":
        plan["request_id"] = "old-run"
    else:
        data["get_current_optimiser_outputs"] = plan[:1]
    payload = translate(data)
    assert len(payload.x_values) == 288
    scheduled = payload.y_series["Plan charge (kW)"]
    assert np.isnan(scheduled[6:]).all()
    np.testing.assert_array_equal(payload.y_series["Live charge (kW)"], 8)


def test_empty_live_history_stays_empty():
    data = history(1)
    data["get_current_optimised_battery_outputs"] = data[
        "get_current_optimised_battery_outputs"
    ][:0]
    assert translate(data).x_values == []
