from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.pv_dao import PvDao
from tests.factories import ForecastPvSnapshotFactory, RealisedPvRowFactory


@pytest.fixture
def dao(test_db):
    pv_dao = PvDao(test_db)

    yield pv_dao

    pv_dao.close()


def test_save_and_check_realised_pv(dao: PvDao) -> None:
    start = datetime(2026, 9, 25, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    records = [
        RealisedPvRowFactory.build(
            timestamp_utc=start + timedelta(minutes=offset),
            house_config_id=cfg_id,
            location_name="Brisbane",
        )
        for cfg_id in ("config_a", "config_b")
        for offset in range(0, 30, 5)
    ]

    assert not dao.has_complete_realised_pv(
        start, end, "Brisbane", ["config_a", "config_b"]
    )

    dao.save_realised_pv(records)

    assert dao.has_complete_realised_pv(
        start, end, "Brisbane", ["config_a", "config_b"]
    )
    assert not dao.has_complete_realised_pv(
        start, end, "Brisbane", ["config_a", "config_b", "config_c"]
    )


def test_save_realised_pv_repairs_partial_existing_rows(dao: PvDao) -> None:
    start = datetime(2026, 9, 25, tzinfo=UTC)
    old = RealisedPvRowFactory.build(
        timestamp_utc=start,
        house_config_id="config_a",
        location_name="Brisbane",
        pv_kw=99.0,
    )
    dao.save_realised_pv([old])

    records = [
        RealisedPvRowFactory.build(
            timestamp_utc=start + timedelta(minutes=offset),
            house_config_id="config_a",
            location_name="Brisbane",
            pv_kw=1.0,
        )
        for offset in range(0, 30, 5)
    ]
    dao.save_realised_pv(records)

    assert dao.has_complete_realised_pv(
        start, start + timedelta(minutes=30), "Brisbane", ["config_a"]
    )
    rows = dao.conn.execute(
        "SELECT pv_kw FROM realised_pv_5m "
        "WHERE house_config_id = ? AND location_name = ? ORDER BY timestamp_utc",
        ("config_a", "Brisbane"),
    ).fetchall()
    assert [row[0] for row in rows] == [1.0] * 6


def test_save_forecast_pv_flattens_snapshot(dao: PvDao) -> None:
    target_start = datetime(2026, 9, 25, tzinfo=UTC)
    available_at = target_start - timedelta(hours=17, minutes=16)
    target_times = [target_start, target_start + timedelta(minutes=30)]
    snapshot = ForecastPvSnapshotFactory.build(
        available_at_utc=available_at,
        house_config_id="config_a",
        location_name="Brisbane",
        target_times_utc=target_times,
        pv_kw=[0.0, 1.5],
    )

    assert not dao.has_forecast_pv_cycle(available_at, "Brisbane", ["config_a"])

    dao.save_forecast_pv([snapshot])

    rows = dao.conn.execute(
        """SELECT house_config_id, location_name, available_at_utc,
        target_time_utc, pv_kw
        FROM forecast_pv_cycle_30m ORDER BY target_time_utc"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (
            "config_a",
            "Brisbane",
            available_at.isoformat(),
            target_times[0].isoformat(),
            0.0,
        ),
        (
            "config_a",
            "Brisbane",
            available_at.isoformat(),
            target_times[1].isoformat(),
            1.5,
        ),
    ]
    assert dao.has_forecast_pv_cycle(available_at, "Brisbane", ["config_a"])
    assert not dao.has_forecast_pv_cycle(
        available_at, "Brisbane", ["config_a", "config_b"]
    )
