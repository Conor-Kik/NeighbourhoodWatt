import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend_v2.generation.pv_forecast_schedule import available_pv_cycle_at
from backend_v2.generation.pv_generation_service import PvGenerationService
from backend_v2.pipelines.pv_pipeline import PvPipeline
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.pv_dao import PvDao
from database_v2.data_access_objects.weather_dao import WeatherDao
from database_v2.db_initilisation import reset_db
from database_v2.models.data_models import (
    ECMWFWeatherRow,
    ForecastPvSnapshot,
    OpenMeteoWeatherRow,
    RealisedPvRow,
)
from database_v2.models.request_models import ObjectiveMode, SimulationRequest

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=2)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    reset_db(path)
    return path


@pytest.fixture
def request_input(db_path: Path) -> SimulationRequest:
    location = LocationDao(str(db_path)).get_location("Brisbane")
    house_configs = HouseConfigDao(str(db_path)).get_all_house_configs()
    assert location is not None
    assert house_configs
    # Two identical house configs with quantity 1 each → community of 2
    cfg_id = house_configs[0].house_config_id
    return SimulationRequest(
        objective_mode=ObjectiveMode.COST,
        start_time_utc=START,
        end_time_utc=END,
        location_name=location.location_name,
        battery_config_id=BatteryDao(str(db_path))
        .get_all_battery_configs()[0]
        .battery_config_id,
        houses={cfg_id: 2},
    )


def store_weather(db_path: Path) -> None:
    dao = WeatherDao(db_path)
    dao.save_openmeteo_weather(
        [
            OpenMeteoWeatherRow(
                timestamp_utc=t,
                location_name="Brisbane",
                ghi=800.0,
                dhi=160.0,
                dni=640.0,
                air_temperature=25.0,
                wind_speed=2.0,
            )
            for t in [START + timedelta(minutes=10 * i) for i in range(13)]
        ]
    )
    dao.save_emcwf_weather(
        [
            ECMWFWeatherRow(
                timestamp_utc=t,
                available_at_utc=START - timedelta(days=1),
                location_name="Brisbane",
                ghi=800.0,
                air_temperature=25.0,
                wind_speed=2.0,
            )
            for t in [START + timedelta(minutes=30 * i) for i in range(51)]
        ]
    )
    dao.close()


def count_rows(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_ensure_data_generates_and_stores_pv(
    db_path: Path, request_input: SimulationRequest
) -> None:
    store_weather(db_path)

    PvPipeline().ensure_data(request_input, db_path)

    # One config × 24 realised timesteps; one config × 51 forecast targets
    assert count_rows(db_path, "realised_pv_5m") == 1 * 24
    assert count_rows(db_path, "forecast_pv_cycle_30m") == 1 * 51


def test_ensure_data_skips_generation_when_pv_is_stored(
    db_path: Path,
    request_input: SimulationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_weather(db_path)
    PvPipeline().ensure_data(request_input, db_path)

    def fail(*_args, **_kwargs):
        raise AssertionError("PV should not be regenerated")

    monkeypatch.setattr(PvGenerationService, "calculate_realised_pv", fail)
    monkeypatch.setattr(PvGenerationService, "calculate_forecast_pv", fail)

    PvPipeline().ensure_data(request_input, db_path)

    assert count_rows(db_path, "realised_pv_5m") == 1 * 24


def test_ensure_data_repairs_partial_realised_pv(
    db_path: Path, request_input: SimulationRequest
) -> None:
    store_weather(db_path)
    cfg_id = next(iter(request_input.houses))
    location_name = request_input.location_name
    PvDao(db_path).save_realised_pv(
        [
            RealisedPvRow(
                timestamp_utc=START,
                house_config_id=cfg_id,
                location_name=location_name,
                pv_kw=99.0,
            )
        ]
    )

    PvPipeline().ensure_data(request_input, db_path)

    assert count_rows(db_path, "realised_pv_5m") == 1 * 24
    with sqlite3.connect(db_path) as conn:
        saved = conn.execute(
            "SELECT pv_kw FROM realised_pv_5m "
            "WHERE timestamp_utc = ? AND house_config_id = ? AND location_name = ?",
            (START.isoformat(), cfg_id, location_name),
        ).fetchone()[0]
    assert saved != 99.0


def test_ensure_data_replaces_old_shrinking_forecasts(
    db_path: Path, request_input: SimulationRequest
) -> None:
    store_weather(db_path)
    cfg_id = next(iter(request_input.houses))
    location_name = request_input.location_name
    old = [
        ForecastPvSnapshot(
            available_at_utc=available_pv_cycle_at(START),
            house_config_id=cfg_id,
            location_name=location_name,
            target_times_utc=[
                START + timedelta(minutes=30 * target) for target in range(4)
            ],
            pv_kw=[0.0] * 4,
        )
    ]
    PvDao(db_path).save_forecast_pv(old)

    PvPipeline().ensure_data(request_input, db_path)

    assert count_rows(db_path, "forecast_pv_cycle_30m") == 1 * 51
    conn = sqlite3.connect(db_path)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forecast_pv_cycle_30m WHERE pv_kw > 0"
            ).fetchone()[0]
            > 0
        )
    finally:
        conn.close()


def test_ensure_data_raises_when_weather_is_missing(
    db_path: Path, request_input: SimulationRequest
) -> None:
    with pytest.raises(RuntimeError, match="No OpenMeteo weather stored"):
        PvPipeline().ensure_data(request_input, db_path)
