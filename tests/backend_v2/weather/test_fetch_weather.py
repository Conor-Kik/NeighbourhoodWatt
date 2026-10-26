from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from backend_v2.weather import fetch_weather as fw


#### FAKE API RESPONCES ################################################################
class _FakeOpenMeteoResponse:
    """One object plays the role of response / Hourly() / Variables(i)."""

    def __init__(self, interval: int, values: list[np.ndarray]):
        self._interval = interval
        self._values = values

    def Hourly(self):
        return self

    def Interval(self):
        return self._interval

    def Time(self):
        return int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())

    def Variables(self, i):
        return MagicMock(ValuesAsNumpy=lambda: self._values[i])


def fake_openmeteo_client():
    general = _FakeOpenMeteoResponse(
        3600,
        [
            np.array([20.0, 26.0, 32.0]),
            np.array([3.6, 7.2, 10.8]),
        ],
    )
    satellite = _FakeOpenMeteoResponse(
        600,
        [np.arange(13, dtype=float) * 100, np.zeros(13), np.zeros(13)],
    )
    client = MagicMock()
    client.weather_api.side_effect = [[general], [satellite]]
    return client


class _FakeEcmwfDataset:
    """Minimal stand-in for the dynamical_catalog xarray-like dataset."""

    def __getitem__(self, _features):
        return self

    def sel(self, init_time, lead_time, **_kwargs):
        self._init_time = pd.Timestamp(init_time, tz="UTC")
        self._leads = lead_time
        return self

    def load(self):
        return self

    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for member in range(51):
            for lead in self._leads:
                hours = lead.total_seconds() / 3600.0
                rows.append(
                    {
                        "ensemble_member": member,
                        "valid_time": self._init_time + pd.Timedelta(hours=hours),
                        "downward_short_wave_radiation_flux_surface": 400.0,
                        "temperature_2m": 290.0,
                        "wind_u_10m": 1.0,
                        "wind_v_10m": 1.0,
                    }
                )
        return pd.DataFrame(rows)


#### TESTS #############################################################################
def test_fetch_openmeteo_weather_no_network():
    fake_client = fake_openmeteo_client()

    retrieval = fw.WeatherDataRetrieval(openmeteo_client=fake_client)
    rows = retrieval.fetch_openmeteo_weather(
        start_time=pd.Timestamp("2025-01-01 00:00:00", tz="UTC"),
        end_time=pd.Timestamp("2025-01-01 01:00:00", tz="UTC"),
        location_name="Brisbane",
        latitude=-27.4698,
        longitude=153.0251,
    )

    assert len(rows) == 7  # Native 10-minute samples, including both endpoints.
    assert fake_client.weather_api.call_count == 2  # general + satellite endpoints
    assert rows[0].location_name == "Brisbane"
    assert rows[1].timestamp_utc == pd.Timestamp("2025-01-01 00:10", tz="UTC")
    assert rows[1].ghi == 100.0
    assert rows[1].air_temperature == 21.0
    assert rows[1].wind_speed == pytest.approx(1.1666676)


def test_fetch_openmeteo_weather_covers_five_minute_edges():
    retrieval = fw.WeatherDataRetrieval(openmeteo_client=fake_openmeteo_client())

    rows = retrieval.fetch_openmeteo_weather(
        start_time=pd.Timestamp("2025-01-01 00:05", tz="UTC"),
        end_time=pd.Timestamp("2025-01-01 00:15", tz="UTC"),
        location_name="Brisbane",
        latitude=-27.4698,
        longitude=153.0251,
    )

    assert [row.timestamp_utc for row in rows] == list(
        pd.date_range("2025-01-01 00:00", periods=3, freq="10min", tz="UTC")
    )


def test_fetch_openmeteo_weather_interpolates_short_satellite_gap():
    client = fake_openmeteo_client()
    responses = list(client.weather_api.side_effect)
    satellite = responses[1][0]
    client.weather_api.side_effect = responses
    for values in satellite._values:
        values[3] = np.nan

    rows = fw.WeatherDataRetrieval(openmeteo_client=client).fetch_openmeteo_weather(
        start_time=pd.Timestamp("2025-01-01 00:00", tz="UTC"),
        end_time=pd.Timestamp("2025-01-01 01:00", tz="UTC"),
        location_name="Brisbane",
        latitude=-27.4698,
        longitude=153.0251,
    )

    assert rows[3].ghi == pytest.approx(300.0)
    assert rows[3].dhi == 0.0
    assert rows[3].dni == 0.0


def test_fetch_openmeteo_weather_rejects_long_satellite_gap():
    client = fake_openmeteo_client()
    responses = list(client.weather_api.side_effect)
    satellite = responses[1][0]
    client.weather_api.side_effect = responses
    for values in satellite._values:
        values[2:10] = np.nan

    with pytest.raises(RuntimeError, match="did not cover"):
        fw.WeatherDataRetrieval(openmeteo_client=client).fetch_openmeteo_weather(
            start_time=pd.Timestamp("2025-01-01 00:00", tz="UTC"),
            end_time=pd.Timestamp("2025-01-01 02:00", tz="UTC"),
            location_name="Brisbane",
            latitude=-27.4698,
            longitude=153.0251,
        )


def test_fetch_ecmwf_weather_no_network(monkeypatch):
    monkeypatch.setattr(fw.dynamical_catalog, "open", lambda name: _FakeEcmwfDataset())

    retrieval = fw.WeatherDataRetrieval(openmeteo_client=MagicMock())
    rows = retrieval.fetch_ecmwf_weather(
        start_time=pd.Timestamp("2025-01-01 00:00:00", tz="UTC"),
        end_time=pd.Timestamp("2025-01-01 23:30:00", tz="UTC"),
        location_name="Brisbane",
        latitude=-27.4698,
        longitude=153.0251,
    )

    cycles = {row.available_at_utc for row in rows}
    assert len(rows) == 109 * len(cycles)
    assert all(
        sum(row.available_at_utc == cycle for row in rows) == 109 for cycle in cycles
    )
    assert all(r.location_name == "Brisbane" for r in rows)
    assert all(r.ghi >= 0 for r in rows)


def test_fetch_ecmwf_cycles_loads_only_requested_cycle(monkeypatch):
    opened = []

    def fake_open(name):
        opened.append(name)
        return _FakeEcmwfDataset()

    monkeypatch.setattr(fw.dynamical_catalog, "open", fake_open)
    cycle = pd.Timestamp("2025-01-02", tz="UTC")

    rows = fw.WeatherDataRetrieval(openmeteo_client=MagicMock()).fetch_ecmwf_cycles(
        cycles=[cycle, cycle],
        location_name="Brisbane",
        latitude=-27.4698,
        longitude=153.0251,
    )

    assert len(opened) == 1
    assert len(rows) == 109
    assert {row.available_at_utc for row in rows} == {cycle}
