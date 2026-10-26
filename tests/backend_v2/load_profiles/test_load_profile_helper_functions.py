from datetime import datetime

import numpy as np
import pytest

import backend_v2.load_profiles.load_profile_helper_functions as hf


#### time2dt / date2dt #################################################################
def test_time2dt():
    assert hf.time2dt("2025-01-01 14:30:00") == datetime(2025, 1, 1, 14, 30, 0)


def test_date2dt():
    assert hf.date2dt("2024-03-05") == datetime(2024, 3, 5)


def test_time2dt_bad_format_raises():
    with pytest.raises(ValueError):
        hf.time2dt("2024-03-05")


def test_date2dt_bad_format_raises():
    with pytest.raises(ValueError):
        hf.date2dt("05/03/2024")


#### get_season ########################################################################
@pytest.mark.parametrize(
    "month, expected",
    [
        (12, 0),
        (1, 0),
        (2, 0),  # summer
        (3, 1),
        (4, 1),
        (5, 1),  # autumn
        (6, 2),
        (7, 2),
        (8, 2),  # winter
        (9, 3),
        (10, 3),
        (11, 3),  # spring
    ],
)
def test_get_season_from_date(month, expected):
    assert hf.get_season(f"2024-{month:02d}-15") == expected


@pytest.mark.parametrize("month, expected", [(1, 0), (4, 1), (7, 2), (10, 3)])
def test_get_season_from_time(month, expected):
    assert hf.get_season(f"2024-{month:02d}-15 12:00:00") == expected


#### get_day_time_series ###############################################################
def test_day_time_series_default_length_and_bounds():
    series = hf.get_day_time_series(datetime(2024, 1, 15))
    assert len(series) == 48
    assert series[0] == "2024-01-15 00:00:00"
    assert series[-1] == "2024-01-15 23:30:00"


def test_day_time_series_custom_step():
    assert len(hf.get_day_time_series(datetime(2024, 1, 15), delta_t_min=60)) == 24
    assert len(hf.get_day_time_series(datetime(2024, 1, 15), delta_t_min=15)) == 96


def test_day_time_series_snaps_to_start_of_day():
    series = hf.get_day_time_series(datetime(2024, 1, 15, 13, 45, 12))
    assert series[0] == "2024-01-15 00:00:00"
    assert len(series) == 48


def test_day_time_series_default_is_today():
    series = hf.get_day_time_series()
    today = datetime.now().strftime("%Y-%m-%d")
    assert series[0].startswith(today)


def test_day_time_series_all_same_date():
    series = hf.get_day_time_series(datetime(2024, 1, 15))
    assert all(t.startswith("2024-01-15") for t in series)


#### get_dummy_time_series #############################################################
@pytest.mark.parametrize("season", [0, 1, 2, 3])
def test_dummy_time_series_matches_season(season):
    series = hf.get_dummy_time_series(season)
    assert len(series) == 48
    assert hf.get_season(series[0]) == season


def test_dummy_time_series_default_is_today():
    series = hf.get_dummy_time_series()
    assert len(series) == 48
    assert series[0].startswith(datetime.now().strftime("%Y-%m-%d"))


@pytest.mark.parametrize("bad_season", [-1, 4, 10])
def test_dummy_time_series_invalid_season_raises(bad_season):
    with pytest.raises(AssertionError):
        hf.get_dummy_time_series(bad_season)


#### compute_epenchnikov_bandwith ######################################################
def test_bandwidth_too_few_points():
    assert hf.compute_epenchnikov_bandwith(np.array([1.0])) == 1e-3
    assert hf.compute_epenchnikov_bandwith(np.array([])) == 1e-3


def test_bandwidth_constant_data_uses_minimum():
    assert hf.compute_epenchnikov_bandwith(np.full(20, 2.5)) == 1e-3


def test_bandwidth_is_positive():
    x = np.random.default_rng(0).normal(5, 2, size=100)
    assert hf.compute_epenchnikov_bandwith(x) > 0


def test_bandwidth_scales_with_data():
    x = np.random.default_rng(0).normal(5, 2, size=100)
    h1 = hf.compute_epenchnikov_bandwith(x)
    h10 = hf.compute_epenchnikov_bandwith(x * 10)
    assert h10 == pytest.approx(h1 * 10)


def test_bandwidth_shrinks_with_more_data():
    rng = np.random.default_rng(0)
    small = hf.compute_epenchnikov_bandwith(rng.normal(0, 1, size=20))
    large = hf.compute_epenchnikov_bandwith(rng.normal(0, 1, size=2000))
    assert large < small


def test_forecast_ev_charging_supplies_required_energy():
    load = np.zeros(48)
    hf.add_forecasted_ev_charging_to_load(load)
    assert load.sum() == pytest.approx(18.75)
    assert load.max() == pytest.approx(3.7)
