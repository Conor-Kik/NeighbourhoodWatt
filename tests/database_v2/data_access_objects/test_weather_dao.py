from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.weather_dao import WeatherDao
from database_v2.models.data_models import ECMWFWeatherRow
from tests.factories import ECMWFWeatherRowFactory, OpenMeteoWeatherRowFactory


@pytest.fixture
def dao(test_db):
    weather_dao = WeatherDao(test_db)

    yield weather_dao

    weather_dao.close()


def test_save_and_check_openmeteo_weather(dao: WeatherDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    minute_offsets = [i * 10 for i in range(145)]
    records = [
        OpenMeteoWeatherRowFactory.build(
            timestamp_utc=start + timedelta(minutes=offset)
        )
        for offset in minute_offsets
    ]

    assert not dao.has_complete_openmeto_weather(
        start,
        start + timedelta(hours=24),
        records[0].location_name,
    )

    dao.save_openmeteo_weather(records)

    assert dao.has_complete_openmeto_weather(
        start,
        start + timedelta(hours=24),
        records[0].location_name,
    )


def test_openmeteo_coverage_includes_rounded_window_edges(dao: WeatherDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    source_times = [start + timedelta(minutes=10 * i) for i in range(3)]
    dao.save_openmeteo_weather(
        [
            OpenMeteoWeatherRowFactory.build(
                timestamp_utc=timestamp,
            )
            for timestamp in source_times
        ]
    )
    requested_start = start + timedelta(minutes=5)
    requested_end = start + timedelta(minutes=15)

    assert dao.has_complete_openmeto_weather(requested_start, requested_end, "Brisbane")
    assert [
        row.timestamp_utc
        for row in dao.get_openmeteo_weather(requested_start, requested_end, "Brisbane")
    ] == source_times


def test_save_and_check_emcwf_weather(dao: WeatherDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    hour_offsets = [i * 0.5 for i in range(48)]
    records = [
        ECMWFWeatherRowFactory.build(
            timestamp_utc=start + timedelta(hours=offset),
            available_at_utc=start,
        )
        for offset in hour_offsets
    ]

    assert not dao.has_complete_ecmwf_weather(
        start,
        start + timedelta(hours=24),
        "Brisbane",
    )

    dao.save_emcwf_weather(records)

    assert dao.has_complete_ecmwf_weather(
        start,
        start + timedelta(hours=24),
        "Brisbane",
    )


def test_get_weather_round_trips_saved_rows(dao: WeatherDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    openmeteo = [
        OpenMeteoWeatherRowFactory.build(
            timestamp_utc=start + timedelta(minutes=30 * i),
        )
        for i in range(4)
    ]
    ecmwf = [
        ECMWFWeatherRowFactory.build(
            timestamp_utc=start + timedelta(minutes=30 * i),
            available_at_utc=start,
        )
        for i in range(4)
    ]
    dao.save_openmeteo_weather(openmeteo)
    dao.save_emcwf_weather(ecmwf)

    end = start + timedelta(hours=1)
    assert dao.get_openmeteo_weather(start, end, "Brisbane") == openmeteo[:3]
    assert dao.get_ecmwf_weather(start, end, "Brisbane") == ecmwf[:2]
    assert dao.get_openmeteo_weather(start, end, "Sydney") == []


def test_ecmwf_cycle_coverage_detects_and_fills_short_horizon(
    dao: WeatherDao,
) -> None:
    cycle = datetime(2025, 1, 1, tzinfo=UTC)

    def rows(count: int) -> list[ECMWFWeatherRow]:
        return [
            ECMWFWeatherRowFactory.build(
                timestamp_utc=cycle + timedelta(minutes=30 * i),
                available_at_utc=cycle,
            )
            for i in range(count)
        ]

    dao.save_emcwf_weather(rows(97))
    assert not dao.has_complete_ecmwf_cycles({cycle}, "Brisbane")
    assert dao.get_missing_ecmwf_cycles({cycle}, "Brisbane") == {cycle}

    dao.save_emcwf_weather(rows(109))
    assert dao.has_complete_ecmwf_cycles({cycle}, "Brisbane")
    assert dao.get_missing_ecmwf_cycles({cycle}, "Brisbane") == set()
    assert (
        len(dao.get_ecmwf_weather(cycle, cycle + timedelta(hours=55), "Brisbane"))
        == 109
    )
