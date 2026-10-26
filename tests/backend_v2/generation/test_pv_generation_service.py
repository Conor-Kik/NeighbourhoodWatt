from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from backend_v2.generation.pv_forecast_schedule import pv_cycle_windows
from backend_v2.generation.pv_generation_service import PvGenerationService
from database_v2.models.data_models import ECMWFWeatherRow, OpenMeteoWeatherRow
from database_v2.models.request_models import (
    HouseConfig,
    Location,
    NemRegion,
)

BRISBANE = Location(
    location_name="Brisbane",
    latitude=-27.4678,
    longitude=153.0278,
    timezone="Australia/Brisbane",
    nem_region=NemRegion.QLD,
)


def make_house_config(capacity_kw: float = 6.6) -> HouseConfig:
    return HouseConfig(
        house_config_id="config-1",
        pv_peak_capacity=capacity_kw,
        panel_tilt=20,
        panel_azimuth=0,
        pv_inverter_ac_kw=10.0,
        pv_inverter_efficiency=0.96,
        has_aircon=True,
        has_poolpump=True,
        has_ev=True,
    )


# 2026-01-01 00:00 UTC is 10:00 in Brisbane, 2026-01-01 14:00 UTC is midnight.
DAYTIME = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
NIGHT = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


def openmeteo_weather(start: datetime, ghi: float) -> list[OpenMeteoWeatherRow]:
    return [
        OpenMeteoWeatherRow(
            timestamp_utc=start + timedelta(minutes=10 * i),
            location_name="Brisbane",
            ghi=ghi,
            dhi=ghi * 0.2,
            dni=ghi * 0.8,
            air_temperature=25.0,
            wind_speed=2.0,
        )
        for i in range(7)
    ]


def ecmwf_weather(
    start: datetime, ghi: float, count: int, cycle: datetime
) -> list[ECMWFWeatherRow]:
    return [
        ECMWFWeatherRow(
            timestamp_utc=start + timedelta(minutes=30 * i),
            available_at_utc=cycle,
            location_name="Brisbane",
            ghi=ghi,
            air_temperature=25.0,
            wind_speed=2.0,
        )
        for i in range(count)
    ]


def test_realised_pv_is_five_minute_and_positive_in_daytime() -> None:
    rows = PvGenerationService().calculate_realised_pv(
        BRISBANE,
        make_house_config(),
        openmeteo_weather(DAYTIME, 800.0),
        DAYTIME,
        DAYTIME + timedelta(hours=1),
    )

    assert len(rows) == 12
    assert rows[1].timestamp_utc - rows[0].timestamp_utc == timedelta(minutes=5)
    assert all(row.house_config_id == "config-1" for row in rows)
    assert all(row.pv_kw > 0 for row in rows)


def test_realised_pv_is_zero_at_night() -> None:
    rows = PvGenerationService().calculate_realised_pv(
        BRISBANE,
        make_house_config(),
        openmeteo_weather(NIGHT, 0.0),
        NIGHT,
        NIGHT + timedelta(hours=1),
    )

    assert all(row.pv_kw == 0.0 for row in rows)


def test_realised_pv_scales_with_capacity() -> None:
    weather = openmeteo_weather(DAYTIME, 800.0)
    end = DAYTIME + timedelta(hours=1)
    service = PvGenerationService()

    small = service.calculate_realised_pv(
        BRISBANE, make_house_config(3.0), weather, DAYTIME, end
    )
    large = service.calculate_realised_pv(
        BRISBANE, make_house_config(6.0), weather, DAYTIME, end
    )

    assert large[0].pv_kw == pytest.approx(2 * small[0].pv_kw, rel=0.05)


def test_realised_pv_aligns_backward_radiation_and_interpolates_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = [
        OpenMeteoWeatherRow(
            timestamp_utc=DAYTIME + timedelta(minutes=10 * i),
            location_name="Brisbane",
            ghi=ghi,
            dhi=ghi / 5,
            dni=ghi * 4 / 5,
            air_temperature=20.0 + 2 * i,
            wind_speed=2.0 + 2 * i,
        )
        for i, ghi in enumerate([10.0, 200.0, 600.0, 900.0])
    ]
    frames: list[pd.DataFrame] = []

    def capture(
        location: Location, house_config: HouseConfig, frame: pd.DataFrame
    ) -> pd.Series:
        frames.append(frame)
        return pd.Series(0.0, index=frame.index)

    service = PvGenerationService()
    monkeypatch.setattr(service, "_simulate", capture)
    service.calculate_realised_pv(
        BRISBANE, make_house_config(), weather, DAYTIME, DAYTIME + timedelta(minutes=30)
    )

    assert frames[0]["ghi"].tolist() == [200, 200, 600, 600, 900, 900]
    assert frames[0]["air_temperature"].tolist() == [20, 21, 22, 23, 24, 25]
    assert frames[0]["wind_speed"].tolist() == [2, 3, 4, 5, 6, 7]


def test_realised_pv_rejects_missing_native_radiation() -> None:
    weather = openmeteo_weather(DAYTIME, 800.0)
    del weather[2]

    with pytest.raises(ValueError, match="Missing 10-minute"):
        PvGenerationService().calculate_realised_pv(
            BRISBANE,
            make_house_config(),
            weather,
            DAYTIME,
            DAYTIME + timedelta(hours=1),
        )


def test_forecast_pv_stores_overlapping_runs_once_per_cycle() -> None:
    start = DAYTIME + timedelta(days=1)
    end = start + timedelta(hours=1.5)

    snapshots = PvGenerationService().calculate_forecast_pv(
        BRISBANE,
        make_house_config(),
        ecmwf_weather(start, 800.0, 50, DAYTIME),
        start,
        end,
    )

    assert len(snapshots) == 1
    assert snapshots[0].available_at_utc == DAYTIME + timedelta(hours=6, minutes=44)
    assert len(snapshots[0].target_times_utc) == 50
    assert snapshots[0].target_times_utc[0] == start
    assert snapshots[0].pv_kw[0] > 0


def test_seven_days_need_one_target_series_per_daily_cycle() -> None:
    windows = pv_cycle_windows(DAYTIME, DAYTIME + timedelta(days=7))

    assert len(windows) == 8
    assert (
        sum(
            int((end - start) / timedelta(minutes=30))
            for start, end in windows.values()
        )
        == 712
    )


def test_forecast_pv_switches_cycles_only_after_dissemination() -> None:
    old_cycle = datetime(2026, 1, 1, tzinfo=UTC)
    new_cycle = old_cycle + timedelta(days=1)
    start = new_cycle + timedelta(hours=6, minutes=30)
    weather = ecmwf_weather(old_cycle, 400.0, 109, old_cycle)
    weather += ecmwf_weather(new_cycle, 800.0, 109, new_cycle)

    snapshots = PvGenerationService().calculate_forecast_pv(
        BRISBANE, make_house_config(), weather, start, start + timedelta(hours=1)
    )

    assert len(snapshots) == 2
    assert all(len(snapshot.pv_kw) == 48 for snapshot in snapshots)
    assert [snapshot.available_at_utc for snapshot in snapshots] == [
        old_cycle + timedelta(hours=6, minutes=44),
        new_cycle + timedelta(hours=6, minutes=44),
    ]
    assert snapshots[0].target_times_utc[1] == snapshots[1].target_times_utc[0]
    assert snapshots[0].pv_kw[1] > 0
    assert snapshots[1].pv_kw[0] > snapshots[0].pv_kw[1]


def test_forecast_pv_rejects_incomplete_cycle() -> None:
    start = DAYTIME + timedelta(days=1)
    with pytest.raises(ValueError, match="missing targets"):
        PvGenerationService().calculate_forecast_pv(
            BRISBANE,
            make_house_config(),
            ecmwf_weather(start, 800.0, 47, DAYTIME),
            start,
            start + timedelta(minutes=30),
        )
