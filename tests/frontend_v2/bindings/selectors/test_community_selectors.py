import numpy as np
import pytest

from frontend_v2.bindings.selectors.series import (
    select_community_net_flows,
    select_household_day,
)

FLOW_FIELDS = [
    ("timestamp_utc", "U32"),
    ("request_id", "U8"),
    ("demand_kw", "f8"),
    ("pv_kw", "f8"),
    ("grid_import_kw", "f8"),
    ("grid_export_kw", "f8"),
    ("charge_kw", "f8"),
    ("discharge_kw", "f8"),
]


def make_data(current: str) -> dict:
    """Two saved runs in Brisbane, with run current picked in Settings."""
    return {
        "get_current_request": np.array(
            [] if current is None else [(0, current)],
            dtype=[("row", "i8"), ("request_id", "U16")],
        ),
        "get_all_simulation_requests": np.array(
            [("run-a", "Brisbane"), ("run-b", "Brisbane")],
            dtype=[("request_id", "U8"), ("location_name", "U16")],
        ),
        "get_all_locations": np.array(
            [("Brisbane", "Australia/Brisbane")],
            dtype=[("location_name", "U16"), ("timezone", "U32")],
        ),
        "get_current_run_houses": np.array(
            [("run-a", 4), ("run-b", 2)],
            dtype=[("request_id", "U8"), ("quantity", "i4")],
        ),
        "get_current_energy_flows": np.array(
            [
                # Run a: buying from the grid while the battery charges
                ("2026-01-01T00:00:00+00:00", "run-a", 8, 4, 10, 0, 3, 0),
                # Run a: selling to the grid while the battery discharges
                ("2026-01-01T00:30:00+00:00", "run-a", 8, 4, 0, 6, 0, 5),
                ("2026-01-01T00:00:00+00:00", "run-b", 2, 2, 1, 0, 0, 0),
            ],
            dtype=FLOW_FIELDS,
        ),
        "get_current_operating_costs": np.array(
            [("run-a", "optimised_battery", 100.0)],
            dtype=[
                ("request_id", "U8"),
                ("scenario_name", "U32"),
                ("comparison_economic_cost_aud", "f8"),
            ],
        ),
    }


def test_net_flows_sign_convention():
    payload = select_community_net_flows(make_data("run-a"))
    # Positive is buying / battery supplying; negative is selling / charging
    assert list(payload.y_series["Net grid (kW)"]) == [10, -6]
    assert list(payload.y_series["Net battery (kW)"]) == [-3, 5]


def test_net_flows_in_location_time():
    payload = select_community_net_flows(make_data("run-a"))
    assert payload.x_label == "Time (AEST)"
    assert payload.x_values[0].hour == 10


def test_switching_run_shows_only_its_rows():
    payload = select_community_net_flows(make_data("run-b"))
    assert len(payload.x_values) == 1
    assert list(payload.y_series["Net grid (kW)"]) == [1]


def test_household_day_is_average_per_house():
    payload = select_household_day(make_data("run-a"))
    assert list(payload.y_series["Demand (kW)"]) == [2, 2]
    assert list(payload.y_series["PV generation (kW)"]) == [1, 1]


@pytest.mark.parametrize("run", ["run-a", "run-b"])
def test_net_flows_match_household_day_times(run):
    data = make_data(run)
    assert select_community_net_flows(data).x_values == (
        select_household_day(data).x_values
    )
