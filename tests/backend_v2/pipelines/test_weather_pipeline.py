"""Weather pipeline fetches only forecast cycles missing from the database."""

from datetime import UTC, datetime, timedelta

from backend_v2.pipelines.weather_pipeline import WeatherPipeline
from backend_v2.weather.fetch_weather import WeatherDataRetrieval
from database_v2.data_access_objects.weather_dao import WeatherDao
from database_v2.db_initilisation import reset_db
from database_v2.models.data_models import ECMWFWeatherRow
from tests.factories import SimulationRequestFactory


def test_ecmwf_fetches_only_missing_or_incomplete_cycles(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "weather.db"
    reset_db(db_path)
    first = datetime(2025, 12, 31, tzinfo=UTC)
    partial = first + timedelta(days=1)
    absent = partial + timedelta(days=1)
    request = SimulationRequestFactory.build(
        start_time_utc=partial,
        end_time_utc=absent + timedelta(hours=12),
    )

    def cycle_rows(cycle: datetime, count: int) -> list[ECMWFWeatherRow]:
        return [
            ECMWFWeatherRow(
                timestamp_utc=cycle + timedelta(minutes=30 * offset),
                available_at_utc=cycle,
                location_name="Brisbane",
                ghi=0.0,
                air_temperature=25.0,
                wind_speed=2.0,
            )
            for offset in range(count)
        ]

    dao = WeatherDao(db_path)
    dao.save_emcwf_weather(cycle_rows(first, 109))
    dao.save_emcwf_weather(cycle_rows(partial, 97))
    dao.close()

    monkeypatch.setattr(WeatherPipeline, "_ensure_openmeteo_weather", lambda self: None)
    fetched = []

    def fake_fetch(self, cycles, location_name, latitude, longitude):
        fetched.append(set(cycles))
        return [row for cycle in cycles for row in cycle_rows(cycle, 109)]

    monkeypatch.setattr(WeatherDataRetrieval, "fetch_ecmwf_cycles", fake_fetch)

    WeatherPipeline().ensure_data(request, db_path)
    assert fetched == [{partial, absent}]

    dao = WeatherDao(db_path)
    assert dao.has_complete_ecmwf_cycles({first, partial, absent}, "Brisbane")
    dao.close()

    WeatherPipeline().ensure_data(request, db_path)
    assert fetched == [{partial, absent}]
