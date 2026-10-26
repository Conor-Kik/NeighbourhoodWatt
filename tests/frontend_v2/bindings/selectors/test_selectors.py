"""tests/frontend_v2/bindings/selectors/test_selectors.py
Selectors read the replay head, not the end of the run, and honour the chart
window the user picked.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from frontend_v2.bindings.selectors.choices import select_time_duration_choices
from frontend_v2.bindings.selectors.common import (
    MAX_PLOT_POINTS,
    plot_slice,
    replay_window,
    time_duration,
    window_mask,
)
from frontend_v2.bindings.selectors.series import (
    select_predicted_buy_and_sell_prices,
    select_predicted_community_pv,
    select_storage_and_curtailment,
)
from frontend_v2.bindings.selectors.tabular import (
    NO_VALUE,
    select_community_battery,
    select_current_replay_time,
    select_live_prices,
    select_operating_cost,
    select_replay_state,
)
from frontend_v2.engine.data_manager import (
    UI_DURATION_KEY,
    UI_OPTIMISER_KEY,
    UI_TIME_KEY,
)
from frontend_v2.sources.replay_clock import UNSET_START_TIME

REQUEST_ID = "run-1"
LOCATION = "Brisbane"
BRISBANE_OFFSET = "+10:00"
REPLAY_MOMENT = "2026-10-11 09:00"
STAMP_JUST_BEFORE = "2026-10-11T08:55:00+00:00"
STAMP_AFTER = "2026-10-11T10:00:00+00:00"

RUN_DTYPE = [
    ("request_id", "U16"),
    ("location_name", "U32"),
    ("start_time_utc", "U32"),
    ("end_time_utc", "U32"),
    ("battery_config_id", "U64"),
]
LOCATION_DTYPE = [("location_name", "U32"), ("timezone", "U32")]
SETTING_DTYPE = [("row", "i8"), ("request_id", "U16")]
BATTERY_DTYPE = [
    ("timestamp_utc", "U32"),
    ("request_id", "U16"),
    ("scenario_name", "U32"),
    ("soc_kwh", "f8"),
]
PRICING_DTYPE = [
    ("timestamp_utc", "U32"),
    ("request_id", "U16"),
    ("buy_tariff_aud_per_kwh", "f8"),
    ("sell_tariff_aud_per_kwh", "f8"),
]
FORECAST_DTYPE = [
    ("run_time_utc", "U32"),
    ("target_time_utc", "U32"),
    ("request_id", "U16"),
    ("pv_kw", "f8"),
]


def make_data(replay_time, **buffers) -> dict:
    """The data dictionary a selector reads, with a run that replays in Brisbane."""
    data = {
        UI_TIME_KEY: replay_time,
        "get_all_simulation_requests": _records(
            RUN_DTYPE,
            [
                (
                    REQUEST_ID,
                    LOCATION,
                    "2026-10-11T08:00:00+00:00",
                    "2026-10-12T08:00:00+00:00",
                    "battery-1",
                )
            ],
        ),
        "get_all_locations": _records(
            LOCATION_DTYPE, [(LOCATION, "Australia/Brisbane")]
        ),
        "get_current_request": _settings(),
        "time_duration": None,
        "get_all_battery_configs": _records(
            [("battery_config_id", "U64"), ("capacity_kwh", "f8")],
            [("battery-1", 100.0)],
        ),
    }
    data.update(buffers)
    return data


def _settings() -> np.recarray:
    return _records(
        SETTING_DTYPE,
        [(0, REQUEST_ID)],
    )


def _records(dtype, rows) -> np.recarray:
    return np.rec.fromrecords(rows, dtype=dtype)


def test_replay_time_is_shown_in_the_timezone_of_the_run():
    data = make_data(datetime(2026, 10, 11, 9, 0, tzinfo=UTC))

    payload = select_current_replay_time(data)

    assert payload.rows == [("2026-10-11 19:00",)]


def test_replay_time_is_dashed_when_no_run_is_being_replayed():
    data = make_data(UNSET_START_TIME)

    payload = select_current_replay_time(data)

    assert payload.rows == [(NO_VALUE,)]


def test_replay_state_is_handed_over_as_the_clock_holds_it():
    moment = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
    data = {
        UI_TIME_KEY: moment,
        UI_DURATION_KEY: 1440,
        UI_OPTIMISER_KEY: 7,
    }

    payload = select_replay_state(data)

    assert payload.rows == [(moment, 1440, 7)]


def test_replay_state_has_no_time_until_the_clock_has_a_run():
    """The clock stores its unset day as UNSET_START_TIME; the bar reads None as
    "no replay running", so the selector translates the sentinel."""
    data = {
        UI_TIME_KEY: UNSET_START_TIME,
        UI_DURATION_KEY: 1440,
        UI_OPTIMISER_KEY: None,
    }

    payload = select_replay_state(data)

    assert payload.rows == [(None, 1440, None)]


def test_battery_state_is_read_at_the_replay_moment_not_the_end_of_the_run():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_optimised_battery_outputs=_records(
            BATTERY_DTYPE,
            [
                (STAMP_JUST_BEFORE, REQUEST_ID, "optimised_battery", 11.0),
                (STAMP_AFTER, REQUEST_ID, "optimised_battery", 99.0),
            ],
        ),
    )

    payload = select_community_battery(data)

    assert payload.rows == [("11.0%", "11.0 kWh stored")]


def test_battery_state_is_dashed_before_the_run_planned_anything():
    data = make_data(
        datetime(2026, 10, 11, 8, 0, tzinfo=UTC),
        get_current_optimised_battery_outputs=_records(
            BATTERY_DTYPE, [(STAMP_JUST_BEFORE, REQUEST_ID, "optimised_battery", 11.0)]
        ),
    )

    payload = select_community_battery(data)

    assert payload.rows == [(NO_VALUE, "")]


def test_battery_state_ignores_the_scenarios_that_are_not_the_optimisers():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_optimised_battery_outputs=_records(
            BATTERY_DTYPE,
            [
                (STAMP_JUST_BEFORE, REQUEST_ID, "reactive_battery", 77.0),
                (STAMP_JUST_BEFORE, REQUEST_ID, "optimised_battery", 11.0),
            ],
        ),
    )

    payload = select_community_battery(data)

    assert payload.rows == [("11.0%", "11.0 kWh stored")]


def test_prices_are_read_at_the_replay_moment():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_pricing_data=_records(
            PRICING_DTYPE,
            [
                (STAMP_JUST_BEFORE, REQUEST_ID, 0.25, 0.05),
                (STAMP_AFTER, REQUEST_ID, 0.99, 0.42),
            ],
        ),
    )

    payload = select_live_prices(data)

    assert payload.rows == [("Buy 25.00 · Sell 5.00 c/kWh",)]


def test_prices_are_dashed_before_the_first_price_of_the_run():
    data = make_data(
        datetime(2026, 10, 11, 8, 0, tzinfo=UTC),
        get_current_pricing_data=_records(
            PRICING_DTYPE, [(STAMP_JUST_BEFORE, REQUEST_ID, 0.25, 0.05)]
        ),
    )

    payload = select_live_prices(data)

    assert payload.rows == [(NO_VALUE,)]


@pytest.mark.parametrize("cost", [12.5, -12.5, 0.0])
def test_operating_cost_follows_replay_snapshot_and_ignores_chart_duration(cost):
    from database_v2.data_access_objects.frontend_dao import FrontendDao

    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_replay_cost=_records(
            FrontendDao.get_current_replay_cost.dtype,
            [(REQUEST_ID, "2026-10-11T09:00:00+00:00", cost, 2.5, 30)],
        ),
    )
    payload = select_operating_cost(data)
    expected = "-$12.50" if cost < 0 else f"${cost:.2f}"
    assert payload.rows[0][0] == expected
    assert "Last 30 min: $2.50" in payload.rows[0][1]
    assert "Recorded through 2026-10-11 19:00" in payload.rows[0][1]
    assert ("Net earnings so far" in payload.rows[0][1]) == (cost < 0)
    data["time_duration"] = "7d"
    assert select_operating_cost(data) == payload
    data[UI_TIME_KEY] -= timedelta(minutes=5)
    assert select_operating_cost(data).rows[0][0] == NO_VALUE
    data[UI_TIME_KEY] += timedelta(minutes=5)
    data["get_current_request"] = _records(SETTING_DTYPE, [(0, "other-run")])
    assert select_operating_cost(data).rows[0][0] == NO_VALUE


@pytest.mark.parametrize("cost,recent", [(float("nan"), 1), (1, float("inf"))])
def test_operating_cost_does_not_display_nonfinite_amounts(cost, recent):
    from database_v2.data_access_objects.frontend_dao import FrontendDao

    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_replay_cost=_records(
            FrontendDao.get_current_replay_cost.dtype,
            [(REQUEST_ID, "2026-10-11T09:00:00+00:00", cost, recent, 30)],
        ),
    )
    payload = select_operating_cost(data)
    assert "nan" not in str(payload).lower()
    assert "inf" not in str(payload).lower()


def _issue(run_at: str, target_at: str, pv_kw: float) -> tuple:
    return (run_at, target_at, REQUEST_ID, pv_kw)


def test_forecast_shows_the_issue_that_was_out_at_the_replay_moment():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0),
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T12:00:00+00:00", 4.0),
                _issue("2026-10-11T09:30:00+00:00", "2026-10-11T13:00:00+00:00", 8.0),
            ],
        ),
    )

    payload = select_predicted_community_pv(data)

    assert list(payload.y_series["Predicted PV (kW)"]) == [3.0, 4.0]
    assert payload.subtitle == "Forecast issued at 2026-10-11 19:00 AEST"


def test_forecast_is_empty_when_no_issue_was_out_yet():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [_issue("2026-10-11T12:00:00+00:00", "2026-10-11T13:00:00+00:00", 8.0)],
        ),
    )

    payload = select_predicted_community_pv(data)

    assert payload.y_series["Predicted PV (kW)"] == []


def test_forecast_is_empty_when_no_run_is_being_replayed():
    data = make_data(
        UNSET_START_TIME,
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [_issue("2026-10-11T06:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0)],
        ),
    )

    payload = select_predicted_community_pv(data)

    assert payload.y_series["Predicted PV (kW)"] == []


def test_selectors_refuse_a_key_they_did_not_declare():
    data = make_data(datetime(2026, 10, 11, 9, 0, tzinfo=UTC))

    with pytest.raises(KeyError):
        select_live_prices(data)


# ---------------------------------------------------------------- chart window

STEP_DTYPE = [
    ("timestamp_utc", "U32"),
    ("request_id", "U16"),
    ("scenario_name", "U32"),
    ("charge_kw", "f8"),
    ("discharge_kw", "f8"),
    ("curtailed_pv_kw", "f8"),
]


def _step(stamp: str, charge: float, discharge: float, curtailed: float) -> tuple:
    return (stamp, REQUEST_ID, "optimised_battery", charge, discharge, curtailed)


def _windowed_data(duration: str) -> dict:
    """Three battery steps: one a day before the window, two inside it."""
    return make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        time_duration=duration,
        get_current_optimised_battery_outputs=_records(
            STEP_DTYPE,
            [
                _step("2026-10-09T09:00:00+00:00", 1.0, 0.0, 0.0),
                _step("2026-10-10T12:00:00+00:00", 2.0, 0.0, 0.0),
                _step("2026-10-11T08:55:00+00:00", 3.0, 0.0, 0.0),
            ],
        ),
    )


def test_a_picked_duration_reads_back_as_its_span():
    data = _windowed_data("24h")

    assert time_duration(data) == timedelta(hours=24)


def test_without_a_picked_duration_the_charts_default_to_24_hours():
    assert time_duration(
        make_data(datetime(2026, 10, 11, 9, 0, tzinfo=UTC))
    ) == timedelta(hours=24)


@pytest.mark.parametrize("obsolete", ["30d", "1y", "a-year", "custom:nope", "custom:0"])
def test_obsolete_duration_defaults_to_24_hours(obsolete):
    assert time_duration(_windowed_data(obsolete)) == timedelta(hours=24)
    assert select_time_duration_choices(_windowed_data(obsolete)).current == "24h"


def test_custom_durations_are_stored_in_minutes():
    data = _windowed_data("custom:90")

    assert time_duration(data) == timedelta(minutes=90)
    assert replay_window(data) == (
        datetime(2026, 10, 11, 7, 30, tzinfo=UTC),
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
    )
    payload = select_time_duration_choices(data)
    assert payload.current == "custom:90"
    assert payload.choices["custom:90"] == "Custom (90 min)"


def test_the_whole_run_stops_at_the_moment_being_replayed():
    data = _windowed_data("full")
    stamps = [
        "2026-10-11T08:55:00+00:00",
        "2026-10-11T09:00:00+00:00",
        "2026-10-11T10:00:00+00:00",
    ]

    assert time_duration(data) is None
    assert replay_window(data) == (None, datetime(2026, 10, 11, 9, 0, tzinfo=UTC))
    assert window_mask(data, stamps) == [True, True, False]
    assert select_time_duration_choices(data).current == "full"


def test_a_duration_longer_than_the_calendar_clamps_to_the_whole_run():
    data = _windowed_data("custom:5256000000")

    assert replay_window(data) == (None, datetime(2026, 10, 11, 9, 0, tzinfo=UTC))
    assert window_mask(data, ["2026-10-11T10:00:00+00:00"]) == [False]


@pytest.mark.parametrize("duration,days", [("24h", 1), ("3d", 3), ("7d", 7)])
def test_duration_windows(duration, days):
    assert time_duration(_windowed_data(duration)) == timedelta(days=days)


def test_the_window_ends_at_the_moment_being_replayed():
    data = _windowed_data("24h")

    assert replay_window(data) == (
        datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
    )


def test_there_is_no_window_when_nothing_is_being_replayed():
    data = make_data(UNSET_START_TIME, time_duration="24h")

    assert replay_window(data) is None
    assert window_mask(data, ["2026-10-09T09:00:00+00:00"]) == [True]


def test_the_window_mask_keeps_only_the_stamps_inside_the_window():
    stamps = [
        "2026-10-09T09:00:00+00:00",
        "2026-10-10T12:00:00+00:00",
        "2026-10-11T08:55:00+00:00",
    ]

    assert window_mask(_windowed_data("24h"), stamps) == [False, True, True]


def test_a_series_keeps_only_the_steps_inside_the_picked_duration():
    payload = select_storage_and_curtailment(_windowed_data("24h"))

    # The kept steps are shown in the run's location time, Brisbane (UTC+10)
    assert payload.x_label == "Time (AEST)"
    assert [value.isoformat() for value in payload.x_values] == [
        "2026-10-10T22:00:00+10:00",
        "2026-10-11T18:55:00+10:00",
    ]
    assert list(payload.y_series["Battery charge (kW)"]) == [2.0, 3.0]


def test_three_days_keeps_every_step_in_this_short_run():
    payload = select_storage_and_curtailment(_windowed_data("3d"))

    assert len(payload.x_values) == 3


def test_short_series_are_drawn_whole_and_long_ones_are_thinned():
    assert plot_slice(100) == slice(None, None, 1)
    assert plot_slice(MAX_PLOT_POINTS * 3).step == 3


def test_a_thinned_series_keeps_its_x_values_and_every_series_aligned():
    start = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    steps = MAX_PLOT_POINTS + 500
    many_steps = _records(
        STEP_DTYPE,
        [
            _step((start + timedelta(minutes=step)).isoformat(), float(step), 0.0, 0.0)
            for step in range(steps)
        ],
    )
    data = make_data(
        start,
        time_duration="7d",
        get_current_optimised_battery_outputs=many_steps,
    )

    payload = select_storage_and_curtailment(data)

    assert len(payload.x_values) == len(payload.y_series["Battery charge (kW)"])
    assert len(payload.x_values) < steps


def test_the_duration_picker_offers_every_duration_and_the_picked_one():
    payload = select_time_duration_choices(_windowed_data("24h"))

    assert list(payload.choices) == [
        "1h",
        "2h",
        "6h",
        "12h",
        "24h",
        "3d",
        "7d",
        "full",
    ]
    assert payload.choices["full"] == "Whole run"
    assert payload.current == "24h"
    assert payload.choices["24h"] == "Last 24 hours"


@pytest.mark.parametrize("minute,expected", [(29, [3.0]), (30, [8.0]), (0, [3.0])])
def test_forecast_boundary_and_backward_seek(minute, expected):
    data = make_data(
        datetime(2026, 10, 11, 9, minute, tzinfo=UTC),
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0),
                _issue("2026-10-11T09:30:00+00:00", "2026-10-11T11:30:00+00:00", 8.0),
            ],
        ),
    )
    assert (
        list(select_predicted_community_pv(data).y_series["Predicted PV (kW)"])
        == expected
    )


def test_forecast_honours_a_custom_forward_window():
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        time_duration="custom:90",
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T08:59:00+00:00", 1.0),
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T09:15:00+00:00", 3.0),
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T10:30:00+00:00", 4.0),
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T10:31:00+00:00", 5.0),
            ],
        ),
    )

    payload = select_predicted_community_pv(data)

    assert list(payload.y_series["Predicted PV (kW)"]) == [3.0, 4.0]


def test_previous_issue_stays_visible_and_is_labelled_while_loading():
    data = make_data(
        datetime(2026, 10, 11, 9, 30, tzinfo=UTC),
        get_current_forecast_data=_records(
            FORECAST_DTYPE,
            [
                _issue("2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0),
            ],
        ),
    )
    result = select_predicted_community_pv(data)
    assert list(result.y_series["Predicted PV (kW)"]) == [3.0]
    assert "19:00 AEST" in result.subtitle
    assert "Updating forecast" in result.subtitle

    # Once the fetch completes without data, clear the previous curve.
    data["get_current_forecast_data"] = _records(FORECAST_DTYPE, [])
    result = select_predicted_community_pv(data)
    assert result.x_values == []
    assert "unavailable" in result.subtitle


@pytest.mark.parametrize("minute,prices", [(0, [0.3, 0.1]), (30, [0.7, 0.2])])
def test_price_forecast_follows_active_issue(minute, prices):
    dtype = [
        *FORECAST_DTYPE,
        ("buy_tariff_aud_per_kwh", "f8"),
        ("sell_tariff_aud_per_kwh", "f8"),
    ]
    data = make_data(
        datetime(2026, 10, 11, 9, minute, tzinfo=UTC),
        get_current_forecast_data=_records(
            dtype,
            [
                (
                    *_issue(
                        "2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0
                    ),
                    0.3,
                    0.1,
                ),
                (
                    *_issue(
                        "2026-10-11T09:30:00+00:00", "2026-10-11T11:30:00+00:00", 8.0
                    ),
                    0.7,
                    0.2,
                ),
            ],
        ),
    )
    result = select_predicted_buy_and_sell_prices(data)
    assert list(result.y_series["Buy price (c/kWh)"]) == [prices[0] * 100]
    assert list(result.y_series["Sell price (c/kWh)"]) == [prices[1] * 100]


def test_price_forecast_stays_visible_until_replacement_arrives():
    dtype = [
        *FORECAST_DTYPE,
        ("buy_tariff_aud_per_kwh", "f8"),
        ("sell_tariff_aud_per_kwh", "f8"),
    ]
    old = (
        *_issue("2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0),
        0.3,
        0.1,
    )
    new = (
        *_issue("2026-10-11T09:30:00+00:00", "2026-10-11T11:30:00+00:00", 8.0),
        0.7,
        0.2,
    )
    data = make_data(
        datetime(2026, 10, 11, 9, 30, tzinfo=UTC),
        get_current_forecast_data=_records(dtype, [old]),
    )
    pending = select_predicted_buy_and_sell_prices(data)
    assert list(pending.y_series["Buy price (c/kWh)"]) == [30.0]
    assert "Updating forecast" in pending.subtitle
    data["get_current_forecast_data"] = _records(dtype, [new])
    ready = select_predicted_buy_and_sell_prices(data)
    assert list(ready.y_series["Buy price (c/kWh)"]) == [70.0]
    assert "Updating forecast" not in ready.subtitle

    # Seeking backward must not show a forecast from the future.
    data[UI_TIME_KEY] = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    assert select_predicted_buy_and_sell_prices(data).x_values == []


@pytest.mark.parametrize("selected", [REQUEST_ID, None, "removed-run"])
def test_current_run_binding_receives_all_inputs_for_readable_run_labels(selected):
    from database_v2.data_access_objects.frontend_dao import FrontendDao
    from frontend_v2.bindings.bindings import ReadBinding
    from frontend_v2.bindings.selectors.tabular import select_current_run
    from tests.factories import BatteryConfigFactory

    battery = BatteryConfigFactory.build(
        battery_config_id="regional-5mw", capacity_kwh=10000, max_power_kw=5000
    ).model_dump()
    dtype = FrontendDao.get_all_battery_configs.dtype
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_all_simulation_requests=_records(
            [
                *RUN_DTYPE,
                ("house_count", "i4"),
                ("objective_mode", "U20"),
            ],
            [
                (
                    REQUEST_ID,
                    LOCATION,
                    "2026-10-11T08:00:00+00:00",
                    "2026-10-12T08:00:00+00:00",
                    "regional-5mw",
                    1,
                    "Cost",
                )
            ],
        ),
        get_all_battery_configs=np.array(
            [tuple(battery[name] for name in dtype.names)], dtype=dtype
        ),
        get_current_request=_records(
            SETTING_DTYPE, [(0, selected)] if selected else []
        ),
    )
    binding = ReadBinding(select_current_run)
    # Match Grid: supply only keys declared by the outer selector, not all data.
    payload = binding.translate({key: data[key] for key in binding.needs})
    text = payload.rows[0][0]
    if selected == REQUEST_ID:
        assert "Brisbane" in text
        assert "Regional battery · 10 MWh · 5 MW" in text
        assert "1 house · Cost" in text
    else:
        assert text == "No run picked"


@pytest.mark.parametrize(
    "buy,sell,shown",
    [
        (0.2575, 0.055, "Buy 25.75 · Sell 5.50 c/kWh"),
        (-0.0123, -0.005, "Buy -1.23 · Sell -0.50 c/kWh"),
        (0.0, 0.0, "Buy 0.00 · Sell 0.00 c/kWh"),
    ],
)
def test_operation_tariffs_display_cents_without_changing_stored_dollars(
    buy, sell, shown
):
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_pricing_data=_records(
            PRICING_DTYPE, [(STAMP_JUST_BEFORE, REQUEST_ID, buy, sell)]
        ),
    )
    assert select_live_prices(data).rows == [(shown,)]
    assert data["get_current_pricing_data"]["buy_tariff_aud_per_kwh"][0] == buy


def test_forecast_tariffs_convert_negative_and_fractional_cents_without_mutation():
    dtype = [
        *FORECAST_DTYPE,
        ("buy_tariff_aud_per_kwh", "f8"),
        ("sell_tariff_aud_per_kwh", "f8"),
    ]
    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_forecast_data=_records(
            dtype,
            [
                (
                    *_issue(
                        "2026-10-11T09:00:00+00:00", "2026-10-11T11:00:00+00:00", 3.0
                    ),
                    -0.0123,
                    0.055,
                ),
            ],
        ),
    )
    payload = select_predicted_buy_and_sell_prices(data)
    assert list(payload.y_series["Buy price (c/kWh)"]) == pytest.approx([-1.23])
    assert list(payload.y_series["Sell price (c/kWh)"]) == pytest.approx([5.5])
    assert data["get_current_forecast_data"]["buy_tariff_aud_per_kwh"][0] == -0.0123


@pytest.mark.parametrize("capacity", [0, -1, float("nan"), float("inf"), None])
def test_soc_unavailable_capacity_preserves_kwh_and_empties_percentage_history(
    capacity,
):
    from frontend_v2.bindings.bindings import ReadBinding
    from frontend_v2.bindings.selectors.series import (
        select_community_battery_soc_history,
    )

    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_optimised_battery_outputs=_records(
            BATTERY_DTYPE, [(STAMP_JUST_BEFORE, REQUEST_ID, "optimised_battery", 25)]
        ),
    )
    configs = data["get_all_battery_configs"]
    if capacity is None:
        data["get_all_battery_configs"] = configs[:0]
    else:
        configs["capacity_kwh"] = capacity
    binding = ReadBinding(select_community_battery)
    assert binding.translate({key: data[key] for key in binding.needs}).rows == [
        (NO_VALUE, "25.0 kWh stored")
    ]
    binding = ReadBinding(select_community_battery_soc_history)
    payload = binding.translate({key: data[key] for key in binding.needs})
    assert len(payload.x_values) == 0
    assert payload.y_series == {"SOC (%)": []}


def test_soc_percentage_uses_selected_run_capacity_and_keeps_replay_window():
    from frontend_v2.bindings.bindings import ReadBinding
    from frontend_v2.bindings.selectors.series import (
        select_community_battery_soc_history,
    )

    data = make_data(
        datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        get_current_optimised_battery_outputs=_records(
            BATTERY_DTYPE,
            [
                (STAMP_JUST_BEFORE, "old-run", "optimised_battery", 1000),
                (STAMP_JUST_BEFORE, REQUEST_ID, "optimised_battery", 25),
                (STAMP_AFTER, REQUEST_ID, "optimised_battery", 99),
            ],
        ),
    )
    data["get_all_battery_configs"]["capacity_kwh"] = 50
    assert select_community_battery(data).rows == [("50.0%", "25.0 kWh stored")]
    binding = ReadBinding(select_community_battery_soc_history)
    payload = binding.translate({key: data[key] for key in binding.needs})
    assert list(payload.y_series["SOC (%)"]) == [50]
    assert len(payload.x_values) == 1
    assert data["get_current_optimised_battery_outputs"]["soc_kwh"][1] == 25
