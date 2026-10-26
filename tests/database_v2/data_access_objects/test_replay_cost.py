"""Replay spending uses all recorded intervals, not sampled chart history."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from database_v2.data_access_objects.frontend_dao import FrontendDao
from database_v2.data_access_objects.simulation_dao import SimulationDao

START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def cost_dao(test_db, dummy_location, dummy_battery_config):
    with sqlite3.connect(test_db) as conn:
        conn.execute(
            "INSERT INTO simulation_request (request_id, objective_mode, "
            "start_time_utc, end_time_utc, location_name, battery_config_id, "
            "fixed_buy_aud_per_kwh, fixed_sell_aud_per_kwh) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cost-run",
                "Balanced",
                START.isoformat(),
                (START + timedelta(days=28)).isoformat(),
                dummy_location.location_name,
                dummy_battery_config.battery_config_id,
                None,
                None,
            ),
        )
        for index in range(28 * 24 * 12):
            stamp = (START + timedelta(minutes=5 * index)).isoformat()
            conn.execute(
                "INSERT INTO realised_pricing_5m VALUES (?, ?, ?, ?)",
                (stamp, dummy_location.location_name, 0.3, 0.1),
            )
            conn.execute(
                "INSERT INTO live_controller_output_5m VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("cost-run", "optimised_battery", stamp, 5, 0, 0, 12, 6, 0, 0.25, ""),
            )
    dao = FrontendDao(test_db)
    dao.current_request_id = "cost-run"
    yield dao, dummy_battery_config
    for value in vars(dao).values():
        if hasattr(value, "conn"):
            value.conn.close()


@pytest.mark.parametrize("minutes", [0, 5, 30, 65, 28 * 24 * 60])
def test_exact_cost_at_start_short_window_and_long_run(cost_dao, minutes):
    dao, battery = cost_dao
    rows = dao.get_current_replay_cost(START + timedelta(minutes=minutes))
    assert len(rows) == 1
    _, stamp, total, recent, recent_minutes = rows[0]
    step_cost = 0.25 + 0.25 * battery.degradation_aud_per_kwh
    assert stamp == (START + timedelta(minutes=minutes)).isoformat()
    assert total == pytest.approx(minutes / 5 * step_cost)
    assert recent == pytest.approx(min(minutes, 30) / 5 * step_cost)
    assert recent_minutes == min(minutes, 30)


@pytest.mark.parametrize(
    "missing", ["live_controller_output_5m", "realised_pricing_5m"]
)
def test_missing_interval_or_price_does_not_understate_cost(cost_dao, missing):
    dao, _ = cost_dao
    with sqlite3.connect(dao.db_path) as conn:
        conn.execute(
            f"DELETE FROM {missing} WHERE timestamp_utc = ?", (START.isoformat(),)
        )
    assert dao.get_current_replay_cost(START + timedelta(minutes=30)) == []


def test_fixed_tariff_and_export_earnings(cost_dao):
    dao, battery = cost_dao
    with sqlite3.connect(dao.db_path) as conn:
        conn.execute(
            "UPDATE simulation_request SET fixed_buy_aud_per_kwh = 0.05, "
            "fixed_sell_aud_per_kwh = 0.05"
        )
        conn.execute("UPDATE live_controller_output_5m SET grid_import_kw = 0")
    row = dao.get_current_replay_cost(START + timedelta(minutes=30))[0]
    assert row[2] == pytest.approx(6 * (-0.05 + 0.25 * battery.degradation_aud_per_kwh))
    dao.current_request_id = None
    assert dao.get_current_replay_cost(START) == []


@pytest.mark.parametrize("hours", [2, 28 * 24])
def test_saved_terminal_history_matches_engine_half_hour_accounting(cost_dao, hours):
    frontend, _ = cost_dao
    end = START + timedelta(hours=hours)
    with sqlite3.connect(frontend.db_path) as conn:
        conn.execute(
            "UPDATE simulation_request SET end_time_utc = ?", (end.isoformat(),)
        )
        conn.execute(
            "UPDATE live_controller_output_5m SET cycle_throughput_kwh = 2 "
            "WHERE timestamp_utc = ?",
            ((end - timedelta(minutes=5)).isoformat(),),
        )
        schema_before = conn.execute(
            "SELECT sql FROM sqlite_master ORDER BY name"
        ).fetchall()
    request = frontend.run_dao.get_all_simulation_requests()[0]
    dao = SimulationDao(frontend.db_path, request)
    try:
        history = dao.get_terminal_throughput_history()
    finally:
        dao.close()
    periods = min(hours * 2, 48)
    assert history == [0.0] * (48 - periods) + [1.5] * (periods - 1) + [3.25]
    with sqlite3.connect(frontend.db_path) as conn:
        assert (
            conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            == schema_before
        )
