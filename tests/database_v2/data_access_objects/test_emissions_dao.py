from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.emissions_dao import EmissionsDao
from tests.factories import (
    ForecastEmissionsSnapshotFactory,
    RealisedEmissionsRowFactory,
)


@pytest.fixture
def dao(test_db):
    emissions_dao = EmissionsDao(test_db)

    yield emissions_dao

    emissions_dao.close()


def test_save_and_check_realised_emissions(dao: EmissionsDao) -> None:
    start = datetime(2026, 9, 25, tzinfo=UTC)
    records = [
        RealisedEmissionsRowFactory.build(
            timestamp_utc=start + timedelta(minutes=offset),
        )
        for offset in range(0, 30, 5)
    ]

    assert not dao.has_complete_realised_emissions(
        start, start + timedelta(minutes=30), records[0].location_name
    )

    dao.save_realised_emissions(records)

    assert dao.has_complete_realised_emissions(
        start, start + timedelta(minutes=30), records[0].location_name
    )


def test_save_forecast_emissions_flattens_snapshot(dao: EmissionsDao) -> None:
    run_time = datetime(2026, 9, 25, tzinfo=UTC)
    target_times = [run_time + timedelta(minutes=30 * index) for index in range(3)]
    snapshot = ForecastEmissionsSnapshotFactory.build(
        run_time_utc=run_time,
        target_times_utc=target_times,
        carbon_intensities_t_per_mwh=[0.70, 0.65, 0.60],
    )

    assert not dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), snapshot.location_name
    )

    dao.save_forecast_emissions([snapshot])

    assert not dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), snapshot.location_name
    )

    rows = dao.conn.execute(
        """
        SELECT target_time_utc, carbon_intensity_t_per_mwh
        FROM forecast_emissions_30m
        WHERE run_time_utc = ? AND location_name = ?
        ORDER BY target_time_utc
        """,
        (run_time.isoformat(), snapshot.location_name),
    ).fetchall()

    assert [tuple(row) for row in rows] == [
        (target.isoformat(), intensity)
        for target, intensity in zip(target_times, [0.70, 0.65, 0.60], strict=True)
    ]

    full_snapshot = ForecastEmissionsSnapshotFactory.build(
        run_time_utc=run_time,
        target_times_utc=[
            run_time + timedelta(minutes=30 * index) for index in range(48)
        ],
        carbon_intensities_t_per_mwh=[0.7] * 48,
    )
    dao.save_forecast_emissions([full_snapshot])
    assert dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), full_snapshot.location_name
    )
