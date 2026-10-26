"""Output checks for the v2 simulation printer."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend_v2.community_optimiser.optimiser_constants import PERIODS_PER_DAY
from backend_v2.community_optimiser.optimiser_printer import SimulationPrinter
from database_v2.data_access_objects.demand_dao import DemandDao, demand_clock_time
from database_v2.data_access_objects.emissions_dao import EmissionsDao
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.price_dao import PriceDao
from database_v2.data_access_objects.pv_dao import PvDao
from database_v2.data_access_objects.results_dao import ResultsDao
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    LiveControllerOutput,
    LiveStep,
    OptimiserInput,
    OptimiserOutput,
    SimulationStep,
    Solver,
)
from database_v2.models.request_models import (
    BatteryConfig,
    HouseConfig,
    Location,
    NemRegion,
    ObjectiveMode,
)
from tests.factories import (
    BatteryConfigFactory,
    HouseConfigFactory,
    LocationFactory,
    RealisedDemandRowFactory,
    RealisedEmissionsRowFactory,
    RealisedPriceRowFactory,
    RealisedPvRowFactory,
    SimulationRequestFactory,
)


@pytest.mark.parametrize(
    ("objective_mode", "expected_block_value"),
    [
        (ObjectiveMode.COST, "1.0600 AUD"),
        (ObjectiveMode.EMISSIONS, "0.0025 tCO2-e"),
        (ObjectiveMode.BALANCED, "1.3100 AUD"),
    ],
)
def test_printer_soc_percentage_and_average_daily_economic_cost(
    objective_mode: ObjectiveMode,
    expected_block_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 7, 15, tzinfo=UTC)

    battery = BatteryConfigFactory.build(
        battery_config_id="test",
        capacity_kwh=10,
        max_power_kw=5,
        efficiency=0.9,
        min_soc_frac=0.1,
        max_soc_frac=0.9,
        replacement_cost_aud=200,
        lifetime_cycles=100,
        max_cycles_24h=1,
    )

    location = LocationFactory.build(
        location_name="Brisbane",
        latitude=-27.47,
        longitude=153.03,
        timezone="Australia/Brisbane",
        nem_region=NemRegion.QLD,
    )

    request = SimulationRequestFactory.build(
        objective_mode=objective_mode,
        start_time_utc=start,
        end_time_utc=start + timedelta(hours=1),
        location_name=location.location_name,
        battery_config_id=battery.battery_config_id,
        houses={},
    )

    from database_v2.data_access_objects.battery_dao import BatteryDao
    from database_v2.data_access_objects.location_dao import LocationDao

    monkeypatch.setattr(BatteryDao, "get_battery_config", lambda self, id: battery)
    monkeypatch.setattr(LocationDao, "get_location", lambda self, name: location)
    monkeypatch.setattr(
        ResultsDao,
        "get_summaries",
        lambda self: [
            {
                "scenario_name": "optimised_battery",
                "estimated_terminal_cost_value_aud": 0.52,
                "estimated_terminal_emissions_value_tonnes": 0.0005,
            }
        ],
    )

    forecast = OptimiserInput(
        objective_mode=objective_mode,
        horizon_periods=1,
        battery_constants=battery,
        initial_soc_kwh=5,
        battery_throughput_history=[0] * PERIODS_PER_DAY,
        tariffs_buy_aud_per_kwh=[0.2],
        tariffs_sell_aud_per_kwh=[0.07],
        carbon_price_aud_per_t=100,
        carbon_intensity_t_per_mwh=[0],
        demand_aggregate_kw=[10],
        pv_aggregate_kw=[0],
    )

    plan = OptimiserOutput(
        solver=Solver.HIGHS,
        objective_mode=objective_mode,
        objective=123,
        shadow_price=0.2,
        soc_kwh=5,
        charge_kw=0,
        discharge_kw=0,
        grid_import_kw=10,
        grid_export_kw=0,
        curtailed_pv_kw=0,
        rolling_throughput_kwh=0,
    )
    steps = []
    for period in range(2):
        period_start = start + timedelta(minutes=30 * period)
        live_steps = tuple(
            LiveStep(
                timestamp_utc=period_start + timedelta(minutes=5 * interval),
                data=LiveControllerInput(
                    soc_kwh=5,
                    demand_kw=10,
                    pv_kw=0,
                    buy_tariff_aud_per_kwh=0.2,
                    sell_tariff_aud_per_kwh=0.07,
                    carbon_intensity_t_per_mwh=0.5,
                ),
                output=LiveControllerOutput(
                    charge_kw=0,
                    discharge_kw=0,
                    grid_import_kw=10,
                    grid_export_kw=0,
                    curtailed_pv_kw=0,
                    next_soc_kwh=6,
                    cycle_throughput_kwh=0.1,
                    reasons=(),
                ),
            )
            for interval in range(6)
        )
        steps.append(
            SimulationStep(
                timestamp_utc=period_start,
                forecast=forecast.model_copy(
                    update={"carbon_price_aud_per_t": 100 * (period + 1)}
                ),
                plan=plan,
                live_steps=live_steps,
            )
        )

    output = StringIO()
    printer = SimulationPrinter(request, ":memory:", output)
    printer.print_step(steps[0])
    printer.print_summary(steps)
    perfect_objective = {
        ObjectiveMode.COST: 1.6,
        ObjectiveMode.EMISSIONS: 0.004,
        ObjectiveMode.BALANCED: 2.2,
    }[objective_mode]
    printer.print_perfect_foresight_summary(
        steps,
        forecast.model_copy(update={"period_hours": 5 / 60}),
        [
            plan.model_copy(
                update={
                    "objective": perfect_objective,
                    "grid_import_kw": 8,
                    "soc_kwh": 4,
                    "next_soc_kwh": 4,
                }
            )
            for _ in range(12)
        ],
    )
    printed = output.getvalue()

    assert "SOC=5.000 kWh (50.0%)" in printed
    assert "SOC=6.000 kWh (60.0%)" in printed
    assert f"30m realised {objective_mode.value}: {expected_block_value}" in printed
    assert "123.0000" not in printed
    assert "buy=0.2000 sell=0.0700 AUD/kWh" in printed
    assert (
        "forecast demand=10.000 kW, PV=0.000 kW; live demand avg=10.000 kW" in printed
    )
    assert (
        "Estimated terminal SOC marginal value (next 24-hour forecast): "
        "Cost=$0.5200; Emissions=0.000500 tCO2-e" in printed
    )
    assert "economic cost: $1.6000 (estimated after terminal credit)" in printed
    assert (
        "Average daily economic cost: $38.4000/day (estimated after terminal credit)"
        in printed
    )
    assert (
        "Grid import emissions: 0.004500 tCO2-e (estimated after terminal credit)"
        in printed
    )
    assert (
        "Realised totals before terminal credit: economic cost=$2.1200; "
        "grid import emissions=0.005000 tCO2-e" in printed
    )
    assert f"Objective: {objective_mode.value}" in printed
    assert "Theoretical emissions cost (Balanced carbon price): $0.7500" in printed
    assert "Final SOC: 6.000 kWh (60.0%)" in printed
    assert "PERFECT FORESIGHT SUMMARY (REALISED 5-MINUTE DATA)" in printed
    assert "economic cost: $1.6000" in printed
    perfect_text = printed.split("PERFECT FORESIGHT SUMMARY")[1]
    assert "terminal credit" not in perfect_text
    assert "terminal SOC marginal value" not in perfect_text
    assert "Final SOC: 4.000 kWh (40.0%)" in printed
    assert f"perfect={perfect_objective:.4f}" in printed

    empty_output = StringIO()
    SimulationPrinter(request, ":memory:", empty_output).print_summary([])
    assert (
        "Average daily economic cost: n/a (no completed solves)"
        in empty_output.getvalue()
    )


def test_printer_clips_only_values_that_display_as_negative_zero() -> None:
    assert SimulationPrinter._number(-0.0004, 3) == "0.000"
    assert SimulationPrinter._number(-0.00004, 4) == "0.0000"
    assert SimulationPrinter._number(-0.001, 3) == "-0.001"
    assert SimulationPrinter._number(-13.1785, 4) == "-13.1785"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ObjectiveMode.COST, "1.5000 AUD"),
        (ObjectiveMode.EMISSIONS, "-0.000250 tCO2-e"),
        (ObjectiveMode.BALANCED, "1.0500 AUD"),
    ],
)
def test_reactive_comparison_uses_selected_objective(
    mode, expected, monkeypatch
) -> None:
    # Smart dispatch can cost less while emitting more; compare the chosen metric.
    summaries = [
        dict(
            scenario_name="optimised_battery",
            grid_import_aud=2,
            grid_export_aud=0,
            battery_degradation_aud=0.1,
            emissions_tonnes=0.001,
            emissions_cost_aud=0.1,
            realised_economic_cost_aud=2.1,
            comparison_economic_cost_aud=1.7,
            comparison_emissions_tonnes=0.00075,
        ),
        dict(
            scenario_name="reactive_battery",
            grid_import_aud=3,
            grid_export_aud=0,
            battery_degradation_aud=0.2,
            emissions_tonnes=0.0005,
            emissions_cost_aud=0.05,
            realised_economic_cost_aud=3.2,
            comparison_economic_cost_aud=3.2,
            comparison_emissions_tonnes=0.0005,
        ),
    ]
    monkeypatch.setattr(ResultsDao, "get_summaries", lambda self: summaries)
    from database_v2.data_access_objects.battery_dao import BatteryDao
    from database_v2.data_access_objects.location_dao import LocationDao

    monkeypatch.setattr(
        BatteryDao, "get_battery_config", lambda self, id: BatteryConfigFactory.build()
    )
    monkeypatch.setattr(
        LocationDao, "get_location", lambda self, name: LocationFactory.build()
    )
    output = StringIO()
    request = SimulationRequestFactory.build(objective_mode=mode)
    SimulationPrinter(request, ":memory:", output).print_reactive_summary()
    assert (
        f"Smart controller improvement ({mode.value}): {expected}" in output.getvalue()
    )
    assert "economic cost=$3.2000" in output.getvalue()
    assert "Smart rolling: economic cost=$1.7000" in output.getvalue()
    assert "Balanced cost=$3.2500" in output.getvalue()


def test_no_battery_comparisons_use_realised_house_quantities(
    test_db: Path,
    dummy_location: Location,
    dummy_battery_config: BatteryConfig,
    dummy_house_config: HouseConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = test_db
    start = datetime(2026, 7, 15, tzinfo=UTC)
    second_house = HouseConfigFactory.build(house_config_id="solar_surplus_house")
    with closing(HouseConfigDao(db_path)) as house_dao:
        house_dao.save_house_config(second_house)
    houses = [dummy_house_config.house_config_id, second_house.house_config_id]
    request = SimulationRequestFactory.build(
        start_time_utc=start,
        end_time_utc=start + timedelta(minutes=30),
        location_name=dummy_location.location_name,
        battery_config_id=dummy_battery_config.battery_config_id,
        houses={houses[0]: 2, houses[1]: 1},
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO carbon_price VALUES (?, ?)", (2026, 100))
        conn.executemany(
            "INSERT INTO simulation_house VALUES (?, ?, ?)",
            [
                (request.request_id, house, quantity)
                for house, quantity in request.houses.items()
            ],
        )
    timestamps = [start + timedelta(minutes=5 * index) for index in range(6)]
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
                        timestamp, ZoneInfo(dummy_location.timezone)
                    ),
                    house_config_id=house,
                    demand_kw=demand,
                )
                for timestamp in timestamps
                for house, demand in zip(houses, [3, 0], strict=True)
            ]
        )
        pv_dao.save_realised_pv(
            [
                RealisedPvRowFactory.build(
                    timestamp_utc=timestamp,
                    house_config_id=house,
                    location_name=request.location_name,
                    pv_kw=pv,
                )
                for timestamp in timestamps
                for house, pv in zip(houses, [0, 4], strict=True)
            ]
        )
        price_dao.save_realised_prices(
            [
                RealisedPriceRowFactory.build(
                    timestamp_utc=timestamp,
                    location_name=request.location_name,
                    buy_tariff_aud=0.3,
                    sell_tariff_aud=0.1,
                )
                for timestamp in timestamps
            ]
        )
        emissions_dao.save_realised_emissions(
            [
                RealisedEmissionsRowFactory.build(
                    timestamp_utc=timestamp,
                    location_name=request.location_name,
                    carbon_intensity_t_per_mwh=0.5,
                )
                for timestamp in timestamps
            ]
        )
    dao = ResultsDao(db_path, request)
    try:
        shared, individual = dao.get_no_battery_summaries()
        dao._save_summaries([shared, individual])
    finally:
        dao.close()
    assert shared["grid_import_aud"] == pytest.approx(0.3)
    assert shared["grid_export_aud"] == 0
    assert shared["emissions_tonnes"] == pytest.approx(0.0005)
    assert individual["grid_import_aud"] == pytest.approx(0.9)
    assert individual["grid_export_aud"] == pytest.approx(0.2)
    assert individual["emissions_tonnes"] == pytest.approx(0.0015)
    output = StringIO()
    monkeypatch.setattr(
        ResultsDao,
        "get_no_battery_summaries",
        lambda self: pytest.fail("No-battery printing must read stored summaries"),
    )
    SimulationPrinter(request, db_path, output).print_no_battery_summary()
    printed = output.getvalue()
    assert "Shared community: import cost=$0.3000" in printed
    assert "Individual houses: import cost=$0.9000" in printed
    assert "economic cost=$0.7000; emissions=0.001500 tCO2-e" in printed
    assert "Theoretical emissions cost: $0.0500; Balanced cost: $0.3500" in printed
    assert "Theoretical emissions cost: $0.1500; Balanced cost: $0.8500" in printed
