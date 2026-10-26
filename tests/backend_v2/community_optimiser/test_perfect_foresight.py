"""The foresight solve consumes every realised five-minute sample."""

from contextlib import closing
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend_v2.community_optimiser.optimiser_printer import SimulationPrinter
from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from backend_v2.community_optimiser.perfect_foresight import (
    evaluate_perfect_foresight,
)
from backend_v2.live_controller.reactive_self_consumption_controller import (
    ReactiveSelfConsumptionController,
)
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.demand_dao import DemandDao, demand_clock_time
from database_v2.data_access_objects.emissions_dao import EmissionsDao
from database_v2.data_access_objects.price_dao import PriceDao
from database_v2.data_access_objects.pv_dao import PvDao
from database_v2.data_access_objects.results_dao import ResultsDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    LiveControllerOutput,
    LiveStep,
    OptimiserInput,
    OptimiserOutput,
    SimulationStep,
    Solver,
)
from database_v2.models.request_models import HouseConfig, Location, ObjectiveMode
from tests.factories import (
    BatteryConfigFactory,
    RealisedDemandRowFactory,
    RealisedEmissionsRowFactory,
    RealisedPriceRowFactory,
    RealisedPvRowFactory,
    SimulationRequestFactory,
)


@pytest.mark.parametrize(
    ("mode", "expected_objective"),
    [
        (ObjectiveMode.COST, 8.2 / 12),
        (ObjectiveMode.EMISSIONS, 19 / 12_000),
        (ObjectiveMode.BALANCED, 11.3 / 12),
    ],
)
def test_perfect_foresight_uses_five_minute_realised_data_and_rolling_objective(
    mode: ObjectiveMode,
    expected_objective: float,
    test_db: Path,
    dummy_location: Location,
    dummy_house_config: HouseConfig,
) -> None:
    # The annual carbon-price transition follows the selected location's year.
    start = datetime(
        2026, 12, 31, 23, 30, tzinfo=ZoneInfo(dummy_location.timezone)
    ).astimezone(UTC)
    battery = BatteryConfigFactory.build(
        capacity_kwh=10,
        max_power_kw=0,
        efficiency=1,
        min_soc_frac=0,
        max_soc_frac=1,
        replacement_cost_aud=0,
    )
    steps = []
    for block in range(2):
        timestamp = start + timedelta(minutes=30 * block)
        carbon_price = 100.0 if block == 0 else 200.0
        forecast = OptimiserInput(
            objective_mode=mode,
            battery_constants=battery,
            initial_soc_kwh=0,
            battery_throughput_history=[0] * 48,
            carbon_price_aud_per_t=carbon_price,
            tariffs_buy_aud_per_kwh=[0.9] * 48,
            tariffs_sell_aud_per_kwh=[0.0] * 48,
            carbon_intensity_t_per_mwh=[0.0] * 48,
            demand_aggregate_kw=[9.0] * 48,
            pv_aggregate_kw=[0.0] * 48,
        )
        plan = OptimiserOutput(
            solver=Solver.HIGHS,
            objective_mode=mode,
            objective=0,
            shadow_price=0,
            soc_kwh=0,
            charge_kw=0,
            discharge_kw=0,
            grid_import_kw=0,
            grid_export_kw=0,
            curtailed_pv_kw=0,
            rolling_throughput_kwh=0,
        )
        demand = 1.0 if block == 0 else 2.0
        buy_price = 0.2 if block == 0 else 0.4
        intensity = 0.5 if block == 0 else 1.0
        live_steps = tuple(
            LiveStep(
                timestamp_utc=timestamp + timedelta(minutes=5 * index),
                data=LiveControllerInput(
                    soc_kwh=0,
                    demand_kw=3.0 if block == 0 and index == 5 else demand,
                    pv_kw=0,
                    buy_tariff_aud_per_kwh=(
                        0.8 if block == 0 and index == 5 else buy_price
                    ),
                    sell_tariff_aud_per_kwh=0,
                    carbon_intensity_t_per_mwh=(
                        1.5 if block == 0 and index == 5 else intensity
                    ),
                    carbon_price_aud_per_t=carbon_price,
                ),
                output=LiveControllerOutput(
                    charge_kw=0,
                    discharge_kw=0,
                    grid_import_kw=3.0 if block == 0 and index == 5 else demand,
                    grid_export_kw=0,
                    curtailed_pv_kw=0,
                    next_soc_kwh=0,
                    cycle_throughput_kwh=0,
                    reasons=(),
                ),
            )
            for index in range(6)
        )
        steps.append(
            SimulationStep(
                timestamp_utc=timestamp,
                forecast=forecast,
                plan=plan,
                live_steps=live_steps,
            )
        )

    data, decisions = evaluate_perfect_foresight(steps, Solver.HIGHS)

    assert data.objective_mode is mode
    assert data.period_hours == 5 / 60
    assert len(decisions) == data.horizon_periods == 12
    assert decisions[5].grid_import_kw == pytest.approx(3)
    assert sum(d.grid_import_kw for d in decisions) / 12 == pytest.approx(20 / 12)
    assert decisions[0].objective == pytest.approx(expected_objective)
    assert data.carbon_prices_aud_per_t == [100] * 6 + [200] * 6

    # The frontend can read every scenario from the existing relational tables.
    db_path = test_db
    config = dummy_house_config.house_config_id
    request = SimulationRequestFactory.build(
        objective_mode=mode,
        start_time_utc=start,
        end_time_utc=start + timedelta(hours=1),
        battery_config_id=battery.battery_config_id,
        location_name=dummy_location.location_name,
        houses={config: 1},
    )
    dao = SimulationDao(db_path, request)
    battery_dao = BatteryDao(db_path)
    try:
        battery_dao.save_battery_config(battery)
    finally:
        battery_dao.conn.close()
    dao.conn.executemany(
        "INSERT OR REPLACE INTO carbon_price VALUES (?, ?)", [(2026, 100), (2027, 200)]
    )
    dao.save_simulation_request()
    for step in steps:
        dao.save_optimiser_output(step.timestamp_utc, step.plan)
        dao.save_live_controller_outputs("optimised_battery", list(step.live_steps))
        controller = ReactiveSelfConsumptionController(battery)
        dao.save_live_controller_outputs(
            "reactive_battery",
            [
                s.model_copy(update={"output": controller.step(s.data)})
                for s in step.live_steps
            ],
        )
    live_samples = [live for step in steps for live in step.live_steps]
    with (
        closing(DemandDao(db_path)) as demand_dao,
        closing(PvDao(db_path)) as pv_dao,
        closing(PriceDao(db_path)) as price_dao,
        closing(EmissionsDao(db_path)) as emissions_dao,
    ):
        demand_dao.save_realised_demand(
            [
                RealisedDemandRowFactory.build(
                    timestamp_utc=demand_clock_time(
                        live.timestamp_utc, ZoneInfo(dummy_location.timezone)
                    ),
                    house_config_id=config,
                    demand_kw=live.data.demand_kw,
                )
                for live in live_samples
            ]
        )
        pv_dao.save_realised_pv(
            [
                RealisedPvRowFactory.build(
                    timestamp_utc=live.timestamp_utc,
                    house_config_id=config,
                    location_name=request.location_name,
                    pv_kw=live.data.pv_kw,
                )
                for live in live_samples
            ]
        )
        price_dao.save_realised_prices(
            [
                RealisedPriceRowFactory.build(
                    timestamp_utc=live.timestamp_utc,
                    location_name=request.location_name,
                    buy_tariff_aud=live.data.buy_tariff_aud_per_kwh,
                    sell_tariff_aud=live.data.sell_tariff_aud_per_kwh,
                )
                for live in live_samples
            ]
        )
        emissions_dao.save_realised_emissions(
            [
                RealisedEmissionsRowFactory.build(
                    timestamp_utc=live.timestamp_utc,
                    location_name=request.location_name,
                    carbon_intensity_t_per_mwh=live.data.carbon_intensity_t_per_mwh,
                )
                for live in live_samples
            ]
        )
    dao.save_perfect_foresight(data, decisions)
    rows = dao.conn.execute(
        "SELECT * FROM live_controller_output_5m "
        "WHERE scenario_name = 'perfect_foresight' "
        "ORDER BY timestamp_utc"
    ).fetchall()
    assert len(rows) == 12
    assert rows[5]["grid_import_kw"] == pytest.approx(3)
    assert rows[-1]["soc_kwh"] == decisions[-1].next_soc_kwh
    results = ResultsDao(db_path, request)
    results.calculate_and_save_all()
    summaries = results.get_summaries()
    assert {s["scenario_name"] for s in summaries} == {
        "optimised_battery",
        "perfect_foresight",
        "no_battery_shared",
        "no_battery_individual",
        "reactive_battery",
    }
    perfect = next(s for s in summaries if s["scenario_name"] == "perfect_foresight")
    assert perfect["emissions_tonnes"] == pytest.approx(19 / 12_000)
    assert perfect["grid_import_aud"] + perfect["emissions_cost_aud"] == pytest.approx(
        11.3 / 12
    )
    assert dao.run_exists() is False
    rolling = next(s for s in summaries if s["scenario_name"] == "optimised_battery")
    results.save_terminal_value("optimised_battery", 0, 0.2, 0.0001, 24)
    assert dao.run_exists() is True
    saved = next(
        s for s in results.get_summaries() if s["scenario_name"] == "optimised_battery"
    )
    assert saved["terminal_value_horizon_hours"] == 24
    assert saved["estimated_terminal_cost_value_aud"] == 0.2
    assert saved["terminal_soc_kwh"] == 0
    assert all(
        saved[key] == rolling[key]
        for key in (
            "grid_import_aud",
            "grid_export_aud",
            "battery_degradation_aud",
            "emissions_tonnes",
        )
    )
    assert saved["comparison_economic_cost_aud"] == pytest.approx(
        saved["realised_economic_cost_aud"] - 0.2
    )
    assert saved["comparison_emissions_tonnes"] == pytest.approx(
        saved["emissions_tonnes"] - 0.0001
    )
    for summary in results.get_summaries():
        if summary["scenario_name"] != "optimised_battery":
            assert (
                summary["comparison_economic_cost_aud"]
                == summary["realised_economic_cost_aud"]
            )
            assert summary["comparison_emissions_tonnes"] == summary["emissions_tonnes"]
    printed = StringIO()
    SimulationPrinter(request, db_path, printed).print_stored_summaries()
    assert f"FINAL RESULTS (Objective: {mode.value})" in printed.getvalue()
    table = {
        cells[0].strip(): (cells[1].strip(), cells[2].strip(), cells[3].strip())
        for line in printed.getvalue().splitlines()
        if len(cells := line.split("|")) == 4
    }
    assert table["Smart rolling*"] == ("$0.4833", "0.001483", "$0.9417")
    assert table["Perfect foresight"] == ("$0.6833", "0.001583", "$0.9417")
    assert table["Greedy self consumption"] == ("$0.6833", "0.001583", "$0.9417")
    assert "Balanced cost (AUD)" in printed.getvalue()
    assert "Emissions cost (AUD)" not in printed.getvalue()
    assert (
        "Balanced cost = realised economic cost + carbon-priced realised emissions"
        in printed.getvalue()
    )
    assert "No battery (shared)" in table
    assert "No battery (individual)" in table
    assert len(table) == 6  # Header and all five methods.
    assert "SIMULATION COST AND EMISSIONS COMPARISON" not in printed.getvalue()
    assert "Estimated rolling savings" not in printed.getvalue()
    assert (
        "Rolling cost/emissions include estimated terminal SOC credits"
        in printed.getvalue()
    )
    printer = SimulationPrinter(request, db_path, printed)
    printer.print_reactive_summary()
    assert "Reactive self consumption" in printed.getvalue()
    expected_improvement = {
        ObjectiveMode.COST: "0.2000 AUD",
        ObjectiveMode.EMISSIONS: "0.000100 tCO2-e",
        ObjectiveMode.BALANCED: "0.0000 AUD",
    }[mode]
    assert (
        f"Smart controller improvement ({mode.value}): {expected_improvement}"
        in printed.getvalue()
    )
    printer.print_terminal_value_summary()
    terminal_text = printed.getvalue().split("ROLLING TERMINAL SOC VALUE ESTIMATES")[1]
    assert "optimised_battery: terminal SOC=" in terminal_text
    assert "perfect_foresight:" not in terminal_text
    assert "reactive_battery:" not in terminal_text
    assert all(
        s["estimated_terminal_cost_value_aud"] is None
        and s["estimated_terminal_emissions_value_tonnes"] is None
        for s in results.get_summaries()
        if s["scenario_name"] in ("perfect_foresight", "reactive_battery")
    )
    assert (
        "Credit-adjusted figures are estimates, not realised savings"
        in printed.getvalue()
    )
    results.close()
    dao.close()


def test_five_minute_optimiser_can_shift_battery_energy_between_prices() -> None:
    battery = BatteryConfigFactory.build(
        capacity_kwh=2,
        max_power_kw=2,
        efficiency=1,
        min_soc_frac=0,
        max_soc_frac=1,
        replacement_cost_aud=0,
    )
    data = OptimiserInput(
        objective_mode=ObjectiveMode.COST,
        horizon_periods=12,
        period_hours=5 / 60,
        battery_constants=battery,
        initial_soc_kwh=0,
        battery_throughput_history=[0] * 288,
        tariffs_buy_aud_per_kwh=[0.1] * 6 + [1.0] * 6,
        tariffs_sell_aud_per_kwh=[0.0] * 12,
        carbon_intensity_t_per_mwh=[0.5] * 12,
        demand_aggregate_kw=[1.0] * 12,
        pv_aggregate_kw=[0.0] * 12,
    )

    decisions = OptimiserRunner(Solver.HIGHS).run(data, all_decisions=True)

    assert len(decisions) == 12
    assert decisions[5].next_soc_kwh is not None
    assert decisions[5].next_soc_kwh > 0
    assert decisions[0].objective < 0.55  # Cost with no battery shifting.
    assert all(
        before.next_soc_kwh == pytest.approx(after.soc_kwh)
        for before, after in zip(decisions, decisions[1:], strict=False)
    )
