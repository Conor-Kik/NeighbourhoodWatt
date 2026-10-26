from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.demand_dao import DemandDao
from tests.factories import ForecastDemandRowFactory, RealisedDemandRowFactory


@pytest.fixture
def dao(test_db):
    demand_dao = DemandDao(test_db)

    yield demand_dao

    demand_dao.close()


def test_save_and_check_realised_demand(dao: DemandDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    minutes_offset = [i * 5 for i in range(24 * 30)]
    records = [
        RealisedDemandRowFactory.build(timestamp_utc=start + timedelta(minutes=offset))
        for offset in minutes_offset
    ]

    assert not dao.has_complete_realised_demand(
        start,
        start + timedelta(hours=24),
        records[0].house_config_id,
    )

    dao.save_realised_demand(records)

    assert dao.has_complete_realised_demand(
        start,
        start + timedelta(hours=24),
        records[0].house_config_id,
    )


def test_realised_coverage_uses_house_config_first_index(dao: DemandDao) -> None:
    plan = dao.conn.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM realised_demand_5m "
        "WHERE house_config_id = ? AND timestamp_utc >= ? AND timestamp_utc < ?",
        ("some-config", "2025-01-01", "2025-01-02"),
    ).fetchall()
    assert any("idx_realised_demand" in row[3] for row in plan)


def test_save_and_check_forecasted_demand(dao: DemandDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    # A 24-hour simulation needs another 47 targets for its final rolling solve.
    minutes_offset = [i * 30 for i in range(95)]
    records = [
        ForecastDemandRowFactory.build(
            run_time_utc=(start + timedelta(minutes=offset)).replace(hour=0, minute=0),
            target_time_utc=start + timedelta(minutes=offset),
        )
        for offset in minutes_offset
    ]

    assert not dao.has_complete_forecast_demand(
        start,
        start + timedelta(hours=24),
        records[0].house_config_id,
    )

    dao.save_forecast_demand(records)

    assert dao.has_complete_forecast_demand(
        start,
        start + timedelta(hours=24),
        records[0].house_config_id,
    )


def test_forecast_coverage_ignores_other_run_times(dao: DemandDao) -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    records = [
        ForecastDemandRowFactory.build(
            run_time_utc=start + timedelta(minutes=30),
            target_time_utc=start + timedelta(minutes=30 * i),
        )
        for i in range(48)
    ]
    dao.save_forecast_demand(records)
    assert not dao.has_complete_forecast_demand(
        start, start + timedelta(minutes=30), records[0].house_config_id
    )
