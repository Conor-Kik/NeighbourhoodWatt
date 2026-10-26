from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.price_dao import PriceDao
from tests.factories import ForecastPriceSnapshotFactory, RealisedPriceRowFactory


@pytest.fixture
def dao(test_db):
    price_dao = PriceDao(test_db)

    yield price_dao

    price_dao.close()


def test_save_and_check_realised_prices(dao: PriceDao) -> None:
    start = datetime(2026, 9, 25, tzinfo=UTC)
    records = [
        RealisedPriceRowFactory.build(
            timestamp_utc=start + timedelta(minutes=offset),
        )
        for offset in range(0, 30, 5)
    ]

    assert not dao.has_complete_realised_prices(
        start, start + timedelta(minutes=30), "Brisbane"
    )

    dao.save_realised_prices(records)

    assert dao.has_complete_realised_prices(
        start, start + timedelta(minutes=30), "Brisbane"
    )

    row = dao.conn.execute(
        """
        SELECT buy_tariff_aud, sell_tariff_aud
        FROM realised_pricing_5m
        WHERE timestamp_utc = ? AND location_name = ?
        """,
        (start.isoformat(), "Brisbane"),
    ).fetchone()

    assert tuple(row) == (records[0].buy_tariff_aud, records[0].sell_tariff_aud)


def test_save_forecast_prices_flattens_snapshot(dao: PriceDao) -> None:
    run_time = datetime(2026, 9, 25, tzinfo=UTC)
    target_times = [run_time + timedelta(minutes=30 * index) for index in range(3)]
    snapshot = ForecastPriceSnapshotFactory.build(
        run_time_utc=run_time,
        target_times_utc=target_times,
        buy_tariffs_aud=[0.25, 0.26, 0.27],
        sell_tariffs_aud=[0.08, 0.09, 0.10],
        forecast_sources=["aemo", "aemo", "historical"],
    )

    dao.save_forecast_prices([snapshot])
    assert [
        row[0]
        for row in dao.conn.execute(
            "SELECT forecast_source FROM forecast_pricing_30m ORDER BY target_time_utc"
        )
    ] == ["aemo", "aemo", "historical"]

    rows = dao.conn.execute(
        """
        SELECT target_time_utc, buy_tariff_aud, sell_tariff_aud
        FROM forecast_pricing_30m
        WHERE run_time_utc = ? AND location_name = ?
        ORDER BY target_time_utc
        """,
        (run_time.isoformat(), "Brisbane"),
    ).fetchall()

    assert [tuple(row) for row in rows] == [
        (target.isoformat(), buy_tariff, sell_tariff)
        for target, buy_tariff, sell_tariff in zip(
            target_times,
            [0.25, 0.26, 0.27],
            [0.08, 0.09, 0.10],
            strict=True,
        )
    ]
    assert not dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), "Brisbane"
    )

    full_snapshot = ForecastPriceSnapshotFactory.build(
        run_time_utc=run_time,
        target_times_utc=[
            run_time + timedelta(minutes=30 * index) for index in range(48)
        ],
    )
    dao.save_forecast_prices([full_snapshot])
    assert dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), full_snapshot.location_name
    )

    dao.conn.execute("UPDATE forecast_pricing_30m SET forecast_source = 'unknown'")
    assert not dao.has_complete_forecast_window(
        run_time, run_time + timedelta(minutes=30), full_snapshot.location_name
    )
