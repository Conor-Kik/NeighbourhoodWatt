from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from backend_v2.load_profiles.load_profile_dataclasses import (
    ClusterKernels,
    LoadProfile,
    LoadProfileGenerator,
    TimeKernel,
)

START_TIME = datetime(2025, 1, 1, 0, 0, 0)
VARIABLES_SPLIT_ON_MAP = {
    "has_aircon": True,
    "has_poolpump": False,
}


#### FIXTURES ##########################################################################
@pytest.fixture
def time_series():
    times = pd.date_range("2025-01-01", periods=48, freq="30min")
    return np.array([t.strftime("%Y-%m-%d %H:%M:%S") for t in times])


@pytest.fixture
def loads():
    rng = np.random.default_rng(0)
    return rng.uniform(0.1, 2.0, size=50)


@pytest.fixture
def cluster_kernels(loads):
    kernels = [TimeKernel(loads) for _ in range(48)]
    return ClusterKernels(
        id=1,
        season=0,
        time_kernels=kernels,
        variables_map={
            "has_aircon": True,
            "has_poolpump": False,
        },
    )


@pytest.fixture
def generator():
    return LoadProfileGenerator()


#### LoadProfile #######################################################################
def test_load_profile_valid(time_series):
    profile = LoadProfile(id=1, time=time_series, load=np.ones(48))
    assert profile.id == 1
    assert profile.date == "2025-01-01"
    assert profile.season == 0


def test_load_profile_hours(time_series):
    profile = LoadProfile(id=1, time=time_series, load=np.ones(48))
    assert profile.hours[0] == 0.0
    assert profile.hours[1] == 0.5
    assert profile.hours[-1] == 23.5


def test_load_profile_length_mismatch_fails(time_series):
    with pytest.raises(AssertionError):
        LoadProfile(id=1, time=time_series, load=np.ones(10))


def test_load_profile_bad_time_format_fails():
    bad_time = np.array(["2025/01/01 00:00", "2025/01/01 00:30"])
    with pytest.raises(AssertionError):
        LoadProfile(id=1, time=bad_time, load=np.ones(2))


def test_load_profile_multiple_days_fails():
    two_days = np.array(["2025-01-01 23:30:00", "2025-01-02 00:00:00"])
    with pytest.raises(AssertionError):
        LoadProfile(id=1, time=two_days, load=np.ones(2))


#### TimeKernel ########################################################################
def test_time_kernel_percentile_is_non_negative_and_monotonic(loads):
    kernel = TimeKernel(loads)
    p10 = kernel.percentile(0.1)
    p50 = kernel.percentile(0.5)
    p90 = kernel.percentile(0.9)
    assert p10 >= 0
    assert p10 <= p50 <= p90


def test_time_kernel_sample_is_non_negative(loads):
    kernel = TimeKernel(loads)
    samples = [kernel.sample() for _ in range(100)]
    assert all(s >= 0 for s in samples)


def test_time_kernel_mean_matches_clipped_sample_distribution():
    kernel = TimeKernel.load_from_data(np.array([[-1.0, 1.0], [0.0, 1.0]]))
    assert kernel.mean == pytest.approx(0.25)


def test_time_kernel_data_round_trip(loads):
    kernel = TimeKernel(loads)
    new_kernel = TimeKernel.load_from_data(kernel.get_data())
    assert new_kernel.percentile(0.5) == pytest.approx(kernel.percentile(0.5))


#### ClusterKernels ####################################################################
def test_cluster_kernels_len(cluster_kernels):
    assert len(cluster_kernels) == 48


def test_cluster_kernels_sample_load_shape(cluster_kernels):
    load = cluster_kernels.sample_load()
    assert load.shape == (48,)
    assert np.all(load >= 0)


def test_cluster_kernels_quartile_ordering(cluster_kernels):
    low = cluster_kernels.sample_load_quartile(0.1)
    high = cluster_kernels.sample_load_quartile(0.9)
    assert low.shape == (48,)
    assert np.all(low <= high)


def test_cluster_kernels_data_round_trip(cluster_kernels):
    data = cluster_kernels.get_data()
    new = ClusterKernels.from_data(data)
    assert new.id == cluster_kernels.id
    assert new.season == cluster_kernels.season
    assert new.variables_map == cluster_kernels.variables_map
    assert len(new) == len(cluster_kernels)


def test_cluster_kernels_from_data_missing_key_fails(cluster_kernels):
    data = cluster_kernels.get_data()
    del data["season"]
    with pytest.raises(AssertionError):
        ClusterKernels.from_data(data)


#### LoadProfileGenerator ##############################################################
def test_sample_load_profile(generator):
    profile = generator.get_sample_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP)
    assert isinstance(profile, LoadProfile)
    assert profile.date == "2025-01-01"
    assert len(profile.time) == len(profile.load)
    assert np.all(profile.load >= 0)


def test_sample_load_quartile_ordering(generator):
    low = generator.get_sample_load_quartile(0.1, START_TIME, **VARIABLES_SPLIT_ON_MAP)
    high = generator.get_sample_load_quartile(0.9, START_TIME, **VARIABLES_SPLIT_ON_MAP)
    assert len(low.load) == len(high.load)
    assert np.all(low.load <= high.load)


def test_sample_load_quartile_is_deterministic(generator):
    first = generator.get_sample_load_quartile(
        0.5, START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    second = generator.get_sample_load_quartile(
        0.5, START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    assert np.array_equal(first.load, second.load)


def test_cluster_kernels_are_reused_for_same_season_and_house_attributes(generator):
    generator.get_sample_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP)
    first = next(iter(generator._cluster_kernels_cache.values()))
    generator.get_sample_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP)
    generator.get_sample_load_quartile(0.5, START_TIME, **VARIABLES_SPLIT_ON_MAP)

    assert len(generator._cluster_kernels_cache) == 1
    assert next(iter(generator._cluster_kernels_cache.values())) is first


def test_realised_load_profile_rows(generator):
    rows = generator.get_realised_load_profile(
        "config_0", START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    n_30min = len(
        generator.get_sample_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP).time
    )

    # 30 min -> 5 min gives 6x as many rows.
    assert len(rows) == n_30min * 6
    assert all(r.house_config_id == "config_0" for r in rows)
    assert all(r.demand_kw >= 0 for r in rows)


def test_realised_load_profile_five_minute_spacing(generator):
    rows = generator.get_realised_load_profile(
        "config_0", START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    gaps = {
        (b.timestamp_utc - a.timestamp_utc).total_seconds()
        for a, b in zip(rows[:-1], rows[1:], strict=False)
    }
    assert gaps == {300.0}


def test_realised_load_profile_timestamps_are_utc(generator):
    rows = generator.get_realised_load_profile(
        "config_3", START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    assert all(r.timestamp_utc.tzinfo is not None for r in rows)


def test_forecasted_load_profile_rows(generator):
    rows = generator.get_forecasted_load_profile(
        "config_5", START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    n_30min = len(
        generator.get_sample_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP).time
    )

    assert len(rows) == n_30min
    assert all(r.house_config_id == "config_5" for r in rows)
    assert all(r.run_time_utc == START_TIME.replace(tzinfo=UTC) for r in rows)
    assert all(r.demand_kw >= 0 for r in rows)


def test_forecasted_load_profile_uses_distribution_mean(generator):
    profile = generator.get_mean_load_profile(START_TIME, **VARIABLES_SPLIT_ON_MAP)
    rows = generator.get_forecasted_load_profile(
        "config_5", START_TIME, **VARIABLES_SPLIT_ON_MAP
    )
    assert [row.demand_kw for row in rows] == pytest.approx(profile.load * 2)


def test_forecasted_load_profile_default_start_time(generator):
    # start_time=None should default to today at midnight without erroring.
    rows = generator.get_forecasted_load_profile("config_5", **VARIABLES_SPLIT_ON_MAP)
    assert len(rows) > 0
    assert rows[0].run_time_utc.hour == 0
    assert rows[0].run_time_utc.minute == 0
    assert rows[0].run_time_utc.tzinfo == UTC


def test_demand_rows_convert_half_hour_kwh_to_kw(generator, time_series, monkeypatch):
    profile = LoadProfile(id=1, time=time_series, load=np.full(48, 0.5))
    monkeypatch.setattr(generator, "get_sample_load_profile", lambda *a, **k: profile)
    monkeypatch.setattr(generator, "get_mean_load_profile", lambda *a, **k: profile)

    realised = generator.get_realised_load_profile("config_1", START_TIME)
    forecast = generator.get_forecasted_load_profile("config_1", START_TIME)

    assert all(row.demand_kw == pytest.approx(1.0) for row in realised)
    assert all(row.demand_kw == pytest.approx(1.0) for row in forecast)
    assert all(row.run_time_utc.tzinfo == UTC for row in forecast)
