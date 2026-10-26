import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend_v2.generation.pv_forecast_schedule import available_pv_cycle_at
from database_v2.data_access_objects.demand_dao import demand_clock_time
from database_v2.data_access_objects.results_dao import ResultsDao
from database_v2.data_access_objects.run_dao import RunDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.models.request_models import SimulationRequest
from tests.factories import SimulationRequestFactory


def profile_time(stamp: str) -> datetime:
    return demand_clock_time(
        datetime.fromisoformat(stamp), ZoneInfo("Australia/Brisbane")
    )


@pytest.fixture
def dummy_request():
    return SimulationRequestFactory.build(
        location_name="Brisbane",
        houses={"config_a": 1, "config_b": 1},
    )


@pytest.fixture
def test_db_path(test_db, dummy_request):
    conn = sqlite3.connect(test_db)

    bat = dummy_request.battery_config_id
    run_time = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    target_time = run_time
    available_at = available_pv_cycle_at(datetime.fromisoformat(run_time)).isoformat()

    conn.execute(
        """INSERT OR IGNORE INTO location
        (location_name, latitude, longitude, timezone, nem_region)
        VALUES (?, ?, ?, ?, ?)""",
        ("Brisbane", -27.47, 153.02, "Australia/Brisbane", "QLD1"),
    )
    conn.execute(
        """INSERT OR IGNORE INTO battery_config
        (battery_config_id, capacity_kwh, max_power_kw, efficiency,
         min_soc_frac, max_soc_frac, replacement_cost_aud, lifetime_cycles,
         max_cycles_24h) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bat, 13.5, 5.0, 0.9, 0.05, 1.0, 12000.0, 3650, 1.0),
    )

    for cfg_id in ("config_a", "config_b"):
        conn.execute(
            """INSERT OR IGNORE INTO house_config
            (house_config_id, pv_peak_capacity, panel_tilt, panel_azimuth,
             pv_inverter_ac_kw, pv_inverter_efficiency, has_aircon, has_poolpump,
             has_ev)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cfg_id, 6.0, 20.0, 0.0, 5.0, 0.96, 1, 0, 1),
        )

    conn.execute(
        """INSERT INTO simulation_request
        (request_id, objective_mode, location_name, start_time_utc, end_time_utc,
         battery_config_id) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            dummy_request.request_id,
            dummy_request.objective_mode,
            "Brisbane",
            dummy_request.start_time_utc.isoformat(),
            dummy_request.end_time_utc.isoformat(),
            bat,
        ),
    )
    for cfg_id, qty in dummy_request.houses.items():
        conn.execute(
            """INSERT OR IGNORE INTO
            simulation_house (request_id, house_config_id, quantity)
            VALUES (?, ?, ?)""",
            (dummy_request.request_id, cfg_id, qty),
        )

    conn.execute("INSERT INTO carbon_price VALUES (2026, 50.0)")

    conn.execute(
        "INSERT INTO forecast_pricing_30m VALUES (?, ?, ?, 0.30, 0.05, 'aemo')",
        (run_time, target_time, "Brisbane"),
    )
    conn.execute(
        "INSERT INTO forecast_emissions_30m VALUES (?, ?, ?, 0.8)",
        (run_time, target_time, "Brisbane"),
    )
    conn.execute(
        "INSERT INTO forecast_demand_30m VALUES (?, ?, ?, ?)",
        (run_time, profile_time(target_time).isoformat(), "config_a", 2.0),
    )
    conn.execute(
        "INSERT INTO forecast_demand_30m VALUES (?, ?, ?, ?)",
        (run_time, profile_time(target_time).isoformat(), "config_b", 3.0),
    )
    conn.execute(
        "INSERT INTO forecast_pv_cycle_30m VALUES (?, ?, ?, ?, ?)",
        (available_at, target_time, "config_a", "Brisbane", 1.0),
    )
    conn.execute(
        "INSERT INTO forecast_pv_cycle_30m VALUES (?, ?, ?, ?, ?)",
        (available_at, target_time, "config_b", "Brisbane", 0.0),
    )

    conn.execute(
        "INSERT INTO realised_pricing_5m VALUES (?, ?, 0.30, 0.05)",
        (target_time, "Brisbane"),
    )
    conn.execute(
        "INSERT INTO realised_emissions_5m VALUES (?, ?, 0.8)",
        (target_time, "Brisbane"),
    )
    conn.execute(
        "INSERT INTO realised_demand_5m VALUES (?, ?, ?)",
        (profile_time(target_time).isoformat(), "config_a", 1.5),
    )
    conn.execute(
        "INSERT INTO realised_pv_5m VALUES (?, ?, ?, ?)",
        (target_time, "config_a", "Brisbane", 0.5),
    )
    conn.execute(
        "INSERT INTO realised_demand_5m VALUES (?, ?, ?)",
        (profile_time(target_time).isoformat(), "config_b", 2.5),
    )
    conn.execute(
        "INSERT INTO realised_pv_5m VALUES (?, ?, ?, ?)",
        (target_time, "config_b", "Brisbane", 1.5),
    )

    conn.commit()
    conn.close()

    return test_db


def test_simulation_request_round_trip_without_forecast_mode(
    test_db_path, dummy_request
):
    dao = SimulationDao(test_db_path, dummy_request)
    try:
        dao.save_simulation_request()
        columns = {
            row[1] for row in dao.conn.execute("PRAGMA table_info(simulation_request)")
        }
        assert "use_lightgbm_forecasts" not in columns
        assert (
            dao.conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'aemo_forecast_mode'"
            ).fetchone()
            is None
        )
    finally:
        dao.close()
    runs = RunDao(test_db_path)
    try:
        assert runs.get_all_simulation_requests() == [dummy_request]
    finally:
        runs.close()


def test_run_exists(test_db_path, dummy_request):
    """Only a run with every optimiser period saved counts as existing."""
    dao = SimulationDao(test_db_path, dummy_request)

    # Request row only: an incomplete run.
    assert dao.run_exists() is False

    periods = int(
        (dummy_request.end_time_utc - dummy_request.start_time_utc)
        / timedelta(minutes=30)
    )
    rows = [
        (
            dummy_request.request_id,
            (dummy_request.start_time_utc + i * timedelta(minutes=30)).isoformat(),
        )
        for i in range(periods)
    ]
    query = """INSERT INTO optimiser_output_30m (
        request_id, timestamp_utc, soc_kwh, charge_kw, discharge_kw,
        grid_import_kw, grid_export_kw, curtailed_pv_kw
    ) VALUES (?, ?, 0, 0, 0, 0, 0, 0)"""

    # All but one period saved: still incomplete.
    with sqlite3.connect(test_db_path) as conn:
        conn.executemany(query, rows[:-1])
    assert dao.run_exists() is False

    with sqlite3.connect(test_db_path) as conn:
        conn.executemany(query, rows[-1:])
    assert dao.run_exists() is False  # Comparison and final summaries not saved yet.
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            "INSERT INTO simulation_summary (request_id, scenario_name, "
            "battery_degradation_aud, grid_import_aud, grid_export_aud, "
            "emissions_tonnes) "
            "VALUES (?, 'perfect_foresight', 0, 0, 0, 0)",
            (dummy_request.request_id,),
        )
    assert dao.run_exists() is False  # Reactive comparison is still missing.
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            "INSERT INTO simulation_summary (request_id, scenario_name, "
            "battery_degradation_aud, grid_import_aud, grid_export_aud, "
            "emissions_tonnes) VALUES (?, 'reactive_battery', 0, 0, 0, 0)",
            (dummy_request.request_id,),
        )
    assert dao.run_exists() is False  # Terminal values have not been estimated yet.
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            "INSERT INTO simulation_summary (request_id, scenario_name, "
            "battery_degradation_aud, grid_import_aud, grid_export_aud, "
            "emissions_tonnes) VALUES (?, 'optimised_battery', 0, 0, 0, 0)",
            (dummy_request.request_id,),
        )
        conn.execute(
            "UPDATE simulation_summary SET estimated_terminal_cost_value_aud = 0, "
            "estimated_terminal_emissions_value_tonnes = 0 WHERE request_id = ? "
            "AND scenario_name = 'optimised_battery'",
            (dummy_request.request_id,),
        )
    assert dao.run_exists() is True

    dummy_request.request_id = "fake_456"
    assert dao.run_exists() is False


def test_get_forecast_window(test_db_path, dummy_request):
    """Tests that forecast SQL joins and groups correctly with quantity scaling."""
    dao = SimulationDao(test_db_path, dummy_request)
    run_time = datetime(2026, 1, 1, tzinfo=UTC)

    window = dao.get_forecast_window(run_time)

    assert window.carbon_price_aud_per_t == 50.0
    assert window.demand_kw[0] == 5.0
    assert window.pv_kw[0] == 1.0
    assert window.buy_tariff_aud_per_kwh[0] == 0.30
    assert window.carbon_intensity_t_per_mwh[0] == 0.8


@pytest.mark.parametrize("zone", ["Australia/Brisbane", "Australia/Adelaide"])
@pytest.mark.parametrize(
    "local_time,expected_rate",
    [("2025-12-31T23:55", 75), ("2026-01-01T00:00", 80), ("2026-01-01T10:00", 80)],
)
def test_carbon_price_changes_at_local_new_year(
    test_db_path, dummy_request, zone, local_time, expected_rate
):
    moment = (
        datetime.fromisoformat(local_time)
        .replace(tzinfo=ZoneInfo(zone))
        .astimezone(UTC)
    )
    stamp = moment.isoformat()
    profile = demand_clock_time(moment, ZoneInfo(zone)).isoformat()
    request = dummy_request.model_copy(
        update={"start_time_utc": moment, "end_time_utc": moment + timedelta(minutes=5)}
    )
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            "UPDATE location SET timezone = ? WHERE location_name = 'Brisbane'", (zone,)
        )
        conn.executemany(
            "INSERT OR REPLACE INTO carbon_price VALUES (?, ?)",
            [(2025, 75), (2026, 80)],
        )
        for table in ("realised_pricing_5m", "realised_emissions_5m", "realised_pv_5m"):
            conn.execute(f"UPDATE {table} SET timestamp_utc = ?", (stamp,))
        conn.execute("UPDATE realised_demand_5m SET timestamp_utc = ?", (profile,))
        for scenario in ("optimised_battery", "reactive_battery", "perfect_foresight"):
            conn.execute(
                """INSERT INTO live_controller_output_5m
                (request_id, scenario_name, timestamp_utc, soc_kwh, charge_kw,
                 discharge_kw, grid_import_kw, grid_export_kw, curtailed_pv_kw,
                 cycle_throughput_kwh) VALUES (?, ?, ?, 1, 0, 0, 2, 0, 0, 0)""",
                (request.request_id, scenario, stamp),
            )
    simulation = SimulationDao(test_db_path, request)
    results = ResultsDao(test_db_path, request)
    try:
        assert (
            simulation.get_forecast_window(moment).carbon_price_aud_per_t
            == expected_rate
        )
        summaries = [
            results._calc_battery_summary(scenario)
            for scenario in (
                "optimised_battery",
                "reactive_battery",
                "perfect_foresight",
            )
        ] + results.get_no_battery_summaries()
        for summary in summaries:
            assert summary["emissions_cost_aud"] / summary[
                "emissions_tonnes"
            ] == pytest.approx(expected_rate)
    finally:
        simulation.close()
        results.close()


def test_get_forecast_window_quantity_scaling(test_db_path):
    """Community total scales correctly when a house config has quantity > 1."""
    request = SimulationRequestFactory.build(
        location_name="Brisbane",
        battery_config_id="tesla_powerwall_3",
        houses={"config_a": 3},
    )
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO simulation_request
            (request_id, objective_mode, location_name, start_time_utc,
             end_time_utc, battery_config_id) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request.request_id,
                request.objective_mode,
                "Brisbane",
                request.start_time_utc.isoformat(),
                request.end_time_utc.isoformat(),
                "tesla_powerwall_3",
            ),
        )

    dao = SimulationDao(test_db_path, request)
    run_time = datetime(2026, 1, 1, tzinfo=UTC)
    window = dao.get_forecast_window(run_time)

    assert window.demand_kw[0] == 6.0
    assert window.pv_kw[0] == 3.0


def test_forecast_window_uses_only_the_cycle_available_at_run_time(
    test_db_path, dummy_request
):
    before = datetime(2026, 1, 2, 6, 30, tzinfo=UTC)
    after = before + timedelta(minutes=30)
    assert available_pv_cycle_at(before) < available_pv_cycle_at(after)

    with sqlite3.connect(test_db_path) as conn:
        for run_time in (before, after):
            run_text = run_time.isoformat()
            conn.execute(
                "INSERT INTO forecast_pricing_30m VALUES (?, ?, ?, 0.30, 0.05, 'aemo')",
                (run_text, run_text, "Brisbane"),
            )
            conn.execute(
                "INSERT INTO forecast_emissions_30m VALUES (?, ?, ?, 0.8)",
                (run_text, run_text, "Brisbane"),
            )
            for cfg_id in ("config_a", "config_b"):
                conn.execute(
                    "INSERT INTO forecast_demand_30m VALUES (?, ?, ?, 2.0)",
                    (
                        profile_time(run_text).replace(hour=0, minute=0).isoformat(),
                        profile_time(run_text).isoformat(),
                        cfg_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO forecast_pv_cycle_30m VALUES (?, ?, ?, ?, ?)",
                    (
                        available_pv_cycle_at(run_time).isoformat(),
                        run_text,
                        cfg_id,
                        "Brisbane",
                        1.0 if run_time == before else 2.0,
                    ),
                )

    dao = SimulationDao(test_db_path, dummy_request)
    assert dao.get_forecast_window(before).pv_kw == [2.0]
    assert dao.get_forecast_window(after).pv_kw == [4.0]


def test_forecast_window_uses_daily_demand_for_later_run(test_db_path, dummy_request):
    run_time = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    run_text = run_time.isoformat()
    daily_run = profile_time(run_text).replace(hour=0, minute=0).isoformat()
    with sqlite3.connect(test_db_path) as conn:
        conn.execute(
            "INSERT INTO forecast_pricing_30m VALUES (?, ?, ?, 0.30, 0.05, 'aemo')",
            (run_text, run_text, "Brisbane"),
        )
        conn.execute(
            "INSERT INTO forecast_emissions_30m VALUES (?, ?, ?, 0.8)",
            (run_text, run_text, "Brisbane"),
        )
        for cfg_id, demand in (("config_a", 2.0), ("config_b", 3.0)):
            conn.execute(
                "INSERT INTO forecast_demand_30m VALUES (?, ?, ?, ?)",
                (daily_run, profile_time(run_text).isoformat(), cfg_id, demand),
            )
            conn.execute(
                "INSERT INTO forecast_pv_cycle_30m VALUES (?, ?, ?, ?, 1.0)",
                (
                    available_pv_cycle_at(run_time).isoformat(),
                    run_text,
                    cfg_id,
                    "Brisbane",
                ),
            )

    dao = SimulationDao(test_db_path, dummy_request)
    window = dao.get_forecast_window(run_time)
    assert window.demand_kw == [5.0]
    assert window.pv_kw == [2.0]


def test_forecast_window_reads_next_days_demand(test_db_path, dummy_request):
    request = SimulationRequestFactory.build(
        location_name="Brisbane",
        battery_config_id=dummy_request.battery_config_id,
        houses={"config_a": 1},
    )
    run_time = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    run_text = run_time.isoformat()
    targets = [run_time, run_time + timedelta(minutes=30)]
    with sqlite3.connect(test_db_path) as conn:
        for target in targets:
            target_text = target.isoformat()
            conn.execute(
                "INSERT INTO forecast_pricing_30m VALUES (?, ?, ?, 0.30, 0.05, 'aemo')",
                (run_text, target_text, "Brisbane"),
            )
            conn.execute(
                "INSERT INTO forecast_emissions_30m VALUES (?, ?, ?, 0.8)",
                (run_text, target_text, "Brisbane"),
            )
            conn.execute(
                "INSERT INTO forecast_demand_30m VALUES (?, ?, ?, ?)",
                (
                    profile_time(target_text).replace(hour=0, minute=0).isoformat(),
                    profile_time(target_text).isoformat(),
                    "config_a",
                    2.0 if target == run_time else 3.0,
                ),
            )
            conn.execute(
                "INSERT INTO forecast_pv_cycle_30m VALUES (?, ?, ?, ?, 0.0)",
                (
                    available_pv_cycle_at(run_time).isoformat(),
                    target_text,
                    "config_a",
                    "Brisbane",
                ),
            )

    dao = SimulationDao(test_db_path, request)
    assert dao.get_forecast_window(run_time).demand_kw == [2.0, 3.0]


def test_get_realised_windows(test_db_path, dummy_request):
    """Tests that realised SQL joins and groups correctly across house configs."""
    dao = SimulationDao(test_db_path, dummy_request)
    run_time = datetime(2026, 1, 1, 0, 0, 0)

    windows = dao.get_realised_windows(run_time)

    assert len(windows.run_times_utc) == 1
    assert windows.agg_demand_kw[0] == 4.0
    assert windows.agg_pv_kw[0] == 2.0
    assert windows.buy_tariff_aud_per_kwh[0] == 0.30
    assert windows.carbon_intensity_t_per_mwh[0] == 0.8


def test_get_realised_windows_quantity_scaling(test_db_path):
    """Community totals scale correctly when a config has quantity > 1."""
    request = SimulationRequestFactory.build(
        location_name="Brisbane",
        battery_config_id="tesla_powerwall_3",
        houses={"config_a": 2},
    )
    dao = SimulationDao(test_db_path, request)
    run_time = datetime(2026, 1, 1, 0, 0, 0)

    windows = dao.get_realised_windows(run_time)

    assert len(windows.run_times_utc) == 1
    assert windows.agg_demand_kw[0] == 3.0
    assert windows.agg_pv_kw[0] == 1.0


def test_fixed_tariffs_change_request_id():
    """Market-price ids are unchanged; any fixed tariff, even 0, gets a new id."""
    data = SimulationRequestFactory.build().model_dump(exclude={"request_id"})
    market = SimulationRequest(**data)
    zero_buy = SimulationRequest(**{**data, "fixed_buy_aud_per_kwh": 0.0})
    other_buy = SimulationRequest(**{**data, "fixed_buy_aud_per_kwh": 0.25})

    assert len({market.request_id, zero_buy.request_id, other_buy.request_id}) == 3
    assert SimulationRequest(**data).request_id == market.request_id


def test_fixed_sell_above_fixed_buy_rejected():
    with pytest.raises(ValidationError, match="cannot exceed"):
        SimulationRequestFactory.build(
            fixed_buy_aud_per_kwh=0.1, fixed_sell_aud_per_kwh=0.2
        )


def test_fixed_tariffs_round_trip(test_db_path, dummy_request):
    """Saved tariffs reload exactly, keeping 0 distinct from omitted."""
    request = dummy_request.model_copy(update={"fixed_buy_aud_per_kwh": 0.0})
    dao = SimulationDao(test_db_path, request)
    try:
        dao.save_simulation_request()
    finally:
        dao.close()
    runs = RunDao(test_db_path)
    try:
        [loaded] = runs.get_all_simulation_requests()
    finally:
        runs.close()
    assert loaded.fixed_buy_aud_per_kwh == 0.0
    assert loaded.fixed_sell_aud_per_kwh is None


@pytest.mark.parametrize(
    ("fixed_buy", "fixed_sell"),
    [
        (None, None),
        (0.40, None),
        (0.0, 0.0),
    ],
)
def test_windows_ignore_fixed_tariffs(
    test_db_path, dummy_request, fixed_buy, fixed_sell
):
    request = dummy_request.model_copy(
        update={
            "fixed_buy_aud_per_kwh": fixed_buy,
            "fixed_sell_aud_per_kwh": fixed_sell,
        }
    )
    dao = SimulationDao(test_db_path, request)
    run_time = datetime(2026, 1, 1, tzinfo=UTC)

    forecast = dao.get_forecast_window(run_time)
    realised = dao.get_realised_windows(run_time)

    assert forecast.buy_tariff_aud_per_kwh == [0.30]
    assert forecast.sell_tariff_aud_per_kwh == [0.05]
    assert realised.buy_tariff_aud_per_kwh == [0.30]
    assert realised.sell_tariff_aud_per_kwh == [0.05]


@pytest.mark.parametrize(
    ("fixed_buy", "expected_import_aud"),
    [
        (None, 2.0 * 5 / 60 * 0.30),  # market price
        (0.40, 2.0 * 5 / 60 * 0.40),  # fixed price
    ],
)
def test_no_battery_baselines_use_fixed_tariff(
    test_db_path, dummy_request, fixed_buy, expected_import_aud
):
    """Known example: one 5-minute interval where each house imports 1 kW net,
    so both baselines import 2 kW for 5 minutes and export nothing."""
    request = dummy_request.model_copy(
        update={
            "end_time_utc": dummy_request.start_time_utc + timedelta(minutes=5),
            "fixed_buy_aud_per_kwh": fixed_buy,
        }
    )
    dao = ResultsDao(test_db_path, request)
    try:
        shared, individual = dao.get_no_battery_summaries()
    finally:
        dao.close()

    for summary in (shared, individual):
        assert summary["grid_import_aud"] == pytest.approx(expected_import_aud)
        assert summary["grid_export_aud"] == 0.0


@pytest.mark.parametrize("fixed", [False, True])
def test_frontend_forecasts_follow_replay_and_match_optimiser(
    test_db_path, dummy_request, fixed
):
    from database_v2.data_access_objects.frontend_dao import FrontendDao

    start = datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
    request = dummy_request.model_copy(
        update={
            "start_time_utc": start,
            "end_time_utc": start + timedelta(hours=1),
            "houses": {"config_a": 2, "config_b": 3},
            "fixed_buy_aud_per_kwh": 0.42 if fixed else None,
            "fixed_sell_aud_per_kwh": 0.12 if fixed else None,
        }
    )
    engine = SimulationDao(test_db_path, request)
    frontend = FrontendDao(test_db_path)
    try:
        engine.save_simulation_request()
        # The second optimiser interval crosses an ECMWF availability boundary.
        for interval in range(2):
            issue = start + timedelta(minutes=30 * interval)
            cycle = available_pv_cycle_at(issue).isoformat()
            for step in range(48):
                target = issue + timedelta(minutes=30 * step)
                stamp = target.isoformat()
                engine.conn.execute(
                    "INSERT INTO forecast_pricing_30m VALUES (?, ?, ?, ?, ?, 'aemo')",
                    (issue.isoformat(), stamp, "Brisbane", 0.2 + interval, 0.1),
                )
                engine.conn.execute(
                    "INSERT INTO forecast_emissions_30m VALUES (?, ?, ?, 0.8)",
                    (issue.isoformat(), stamp, "Brisbane"),
                )
                profile = profile_time(stamp)
                day = profile.replace(hour=0, minute=0).isoformat()
                for config, value in (("config_a", 1.0), ("config_b", 2.0)):
                    engine.conn.execute(
                        "INSERT OR REPLACE INTO forecast_demand_30m "
                        "VALUES (?, ?, ?, ?)",
                        (day, profile.isoformat(), config, value),
                    )
                    engine.conn.execute(
                        "INSERT OR REPLACE INTO forecast_pv_cycle_30m "
                        "VALUES (?, ?, ?, ?, ?)",
                        (cycle, stamp, config, "Brisbane", value + interval),
                    )
        engine.conn.commit()
        frontend.set_current_request({"request_id": request.request_id})

        def displayed(moment):
            return [
                dict(
                    zip(
                        frontend.get_current_forecast_data.dtype.names, row, strict=True
                    )
                )
                for row in frontend.get_current_forecast_data(moment)
            ]

        initial = displayed(start)
        later = displayed(start + timedelta(minutes=30))
        assert len(initial) == len(later) == 48
        assert initial[0]["pv_kw"] == 8.0
        assert later[0]["pv_kw"] == 13.0
        assert later[-1]["target_time_utc"] > request.end_time_utc.isoformat()
        for moment, rows in ((start, initial), (start + timedelta(minutes=30), later)):
            forecast = engine.get_forecast_window(moment)
            assert [row["demand_kw"] for row in rows] == forecast.demand_kw
            assert [row["pv_kw"] for row in rows] == forecast.pv_kw
            assert [
                row["buy_tariff_aud_per_kwh"] for row in rows
            ] == forecast.buy_tariff_aud_per_kwh
            assert [
                row["sell_tariff_aud_per_kwh"] for row in rows
            ] == forecast.sell_tariff_aud_per_kwh
        assert later[0]["buy_tariff_aud_per_kwh"] == 1.2
        assert displayed(start + timedelta(minutes=25)) == initial
        assert displayed(start) == initial  # Backward seek.
        assert frontend.get_current_forecast_data(request.end_time_utc) == []
        engine.conn.execute(
            "DELETE FROM forecast_pricing_30m WHERE run_time_utc = ?",
            ((start + timedelta(minutes=30)).isoformat(),),
        )
        engine.conn.commit()
        assert frontend.get_current_forecast_data(start + timedelta(minutes=30)) == []
        # An incomplete issue must never appear as a complete forecast.
        engine.conn.execute(
            "DELETE FROM forecast_pricing_30m "
            "WHERE run_time_utc = ? AND target_time_utc = ?",
            (start.isoformat(), initial[-1]["target_time_utc"]),
        )
        engine.conn.commit()
        assert frontend.get_current_forecast_data(start) == []
    finally:
        engine.close()
        for dao in (
            frontend.location_dao,
            frontend.house_dao,
            frontend.battery_dao,
            frontend.run_dao,
        ):
            dao.conn.close()
