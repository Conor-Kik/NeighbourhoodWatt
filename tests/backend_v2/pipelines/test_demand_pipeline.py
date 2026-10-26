"""Demand preparation must supply daily profiles for every rolling solve."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from backend_v2.pipelines.demand_pipeline import DemandPipeline
from tests.factories import HouseConfigFactory, SimulationRequestFactory


def test_demand_pipeline_stores_utc_daily_profiles_through_last_horizon(
    test_db, dummy_house_config, dummy_location
):
    start = datetime(2026, 7, 23, tzinfo=UTC)
    request = SimulationRequestFactory.build(
        start_time_utc=start,
        end_time_utc=start + timedelta(hours=1),
        location_name=dummy_location.location_name,
        houses={dummy_house_config.house_config_id: 1},
    )
    pipeline = DemandPipeline()
    pipeline.ensure_data(request, test_db)
    assert len(pipeline._realised_start_times) == 1
    assert len(pipeline._forecast_start_times) == 2
    cfg_id = dummy_house_config.house_config_id

    from database_v2.data_access_objects.demand_dao import DemandDao

    dao = DemandDao(test_db)
    assert dao.has_complete_forecast_demand(start, start + timedelta(hours=1), cfg_id)
    dao.close()

    with sqlite3.connect(test_db) as conn:
        realised_count = conn.execute(
            "SELECT COUNT(*) FROM realised_demand_5m WHERE house_config_id = ?",
            (cfg_id,),
        ).fetchone()[0]
        run_times = conn.execute(
            "SELECT DISTINCT run_time_utc FROM forecast_demand_30m "
            "WHERE house_config_id = ? ORDER BY run_time_utc",
            (cfg_id,),
        ).fetchall()
    assert realised_count == 288
    assert run_times == [
        (start.isoformat(),),
        ((start + timedelta(days=1)).isoformat(),),
    ]


def test_demand_pipeline_logs_progress_by_tenth(
    test_db, dummy_location, caplog, monkeypatch
):
    configs = [
        HouseConfigFactory.build(house_config_id=f"config_{i}") for i in range(1, 21)
    ]
    request = SimulationRequestFactory.build(
        houses={cfg.house_config_id: 1 for cfg in configs},
        location_name=dummy_location.location_name,
    )
    monkeypatch.setattr(
        DemandPipeline,
        "_ensure_house_realised_demand",
        lambda self, house, num_houses: None,
    )
    monkeypatch.setattr(
        DemandPipeline, "_ensure_house_forecast_demand", lambda self, house: None
    )

    pipeline = DemandPipeline()
    with caplog.at_level(logging.INFO, logger="backend_v2.pipelines.demand_pipeline"):
        pipeline.ensure_data(request, test_db)

    messages = [
        record.message
        for record in caplog.records
        if record.name == "backend_v2.pipelines.demand_pipeline"
    ]
    assert [message for message in messages if message.startswith("Demand ready")] == [
        f"Demand ready for {count}/20 house configs ({count * 5}%)"
        for count in range(1, 21)
    ]
