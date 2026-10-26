import logging
import multiprocessing as mp
import random
import re
import sqlite3
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from backend_v2.aemo.aemo_constants import AEMO_MARKET_TIMEZONE
from backend_v2.aemo.aemo_emissions_service import IncompleteEmissionsDataError
from backend_v2.community_optimiser.optimiser_printer import SimulationPrinter
from backend_v2.pipeline_runner import ensure_data
from backend_v2.pipelines.demand_pipeline import DemandPipeline
from backend_v2.pipelines.emissions_pipeline import EmissionsPipeline
from backend_v2.pipelines.price_pipeline import PricePipeline
from backend_v2.pipelines.pv_pipeline import PvPipeline
from backend_v2.pipelines.weather_pipeline import WeatherPipeline
from backend_v2.simulation_engine import SimulationEngine
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.house_dao import HouseConfigDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.db_constants import DB_PATH
from database_v2.db_initilisation import reset_db as nuke_db
from database_v2.models.optimiser_models import Solver
from database_v2.models.request_models import (
    HouseConfig,
    ObjectiveMode,
    SimulationRequest,
)
from frontend_v2.main import main

cli = typer.Typer(
    name="gridsync",
    help="GridSync CLI tool for grid simulation and optimisation.",
    add_completion=False,
)
console = Console()


def _house_config_weight(config: HouseConfig) -> float:
    """A rough mix of solar system sizes and household appliances.

    The seed catalogue contains only solar homes. These weights favour common
    6.6-10 kW systems and make pool pumps less common than the seed rows imply.
    They are sampling heuristics, not measured population frequencies.
    """
    capacity = config.pv_peak_capacity
    pv_weight = 5.0 if capacity <= 10 else 3.0 if capacity <= 13 else 1.0
    appliance_weight = (1.7 if config.has_aircon else 1.0) * (
        0.3 if config.has_poolpump else 1.0
    )
    return pv_weight * appliance_weight


def _archive_cutoff_utc(now_utc: datetime) -> datetime:
    """End of the latest conservatively available AEMO archive month."""
    market_now = now_utc.astimezone(AEMO_MARKET_TIMEZONE)
    this_month = market_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Monthly archives arrive after month end. Use the month before last so
    # the default does not depend on an archive published only recently.
    cutoff_market = (this_month - timedelta(days=1)).replace(day=1)
    return cutoff_market.astimezone(UTC)


def _default_simulation_start(
    now_utc: datetime, hours: float, timezone: str = "UTC", local_hour: int = 0
) -> datetime:
    """Choose the latest local start that fits before the archive cutoff."""
    latest_start = _archive_cutoff_utc(now_utc) - timedelta(hours=hours + 24)
    local_zone = ZoneInfo(timezone)
    local_date = latest_start.astimezone(local_zone).date()
    start = datetime.combine(local_date, time(local_hour), local_zone).astimezone(UTC)
    if start > latest_start:
        start = datetime.combine(
            local_date - timedelta(days=1), time(local_hour), local_zone
        ).astimezone(UTC)
    return start


def _parse_simulation_start(value: str, timezone: str) -> datetime:
    """Interpret a date at 04:00 local; timestamps remain explicitly UTC."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            local_date = date.fromisoformat(value)
            return datetime.combine(local_date, time(4), ZoneInfo(timezone)).astimezone(
                UTC
            )
        start = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            "start must be an ISO date (YYYY-MM-DD) or datetime"
        ) from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start.astimezone(UTC)


@cli.command()
def reset_db():
    """Initialize the database; delete any existing data first."""
    nuke_db()


@cli.command()
def run():
    """Run the GridSync desktop application."""
    raise SystemExit(main())


@cli.command()
def run_simulation(
    location: Annotated[str, typer.Option(help="Seeded location name")] = "Brisbane",
    start: Annotated[
        str | None,
        typer.Option(help="Date: 4 am local; datetime: UTC (default: 4 am local)"),
    ] = None,
    days: Annotated[int, typer.Option(help="Whole simulation days")] = 0,
    hours: Annotated[
        float | None, typer.Option(help="Additional simulation hours")
    ] = None,
    houses: Annotated[int, typer.Option(help="Number of houses")] = 3,
    house_config: Annotated[
        str | None,
        typer.Option(help="One house config ID; omit when using --random-configs"),
    ] = None,
    random_configs: Annotated[
        int | None,
        typer.Option(help="Assign houses across this many weighted seeded configs"),
    ] = None,
    assignment_seed: Annotated[
        int | None,
        typer.Option(help="Seed for reproducible random config assignment"),
    ] = None,
    battery: Annotated[
        str, typer.Option(help="Seeded battery configuration ID")
    ] = "sungrow-ps255cs-2h",
    objective: Annotated[
        ObjectiveMode, typer.Option(help="Cost, Emissions, or Balanced")
    ] = ObjectiveMode.COST,
    solver: Annotated[
        Solver, typer.Option(help="highs or gurobi_direct")
    ] = Solver.GUROBI,
    prepare_data: Annotated[
        bool, typer.Option(help="Fetch weather, PV, emissions and prices")
    ] = True,
    fixed_buy: Annotated[
        float | None, typer.Option(help="Fixed buy price AUD/kWh (default: AEMO)")
    ] = None,
    fixed_sell: Annotated[
        float | None, typer.Option(help="Fixed sell price AUD/kWh (default: AEMO)")
    ] = None,
):
    """Run the v2 MPC simulation and print realised five-minute results."""
    if not DB_PATH.exists():
        raise typer.BadParameter("Database missing; run gridsync reset-db first")
    if houses <= 0:
        raise typer.BadParameter("houses must be positive")
    if days < 0 or (hours is not None and hours < 0):
        raise typer.BadParameter("days and hours must be non-negative")
    duration_hours = 1.0 if days == 0 and hours is None else days * 24 + (hours or 0.0)
    if duration_hours <= 0 or (duration_hours * 2) % 1:
        raise typer.BadParameter("simulation length must be positive and in half-hours")
    db_path = str(DB_PATH)
    selected_location = LocationDao(db_path).get_location(location)
    if selected_location is None:
        raise typer.BadParameter(f"Unknown location: {location}")
    if start is None:
        start_utc = _default_simulation_start(
            datetime.now(UTC), duration_hours, selected_location.timezone, local_hour=4
        )
    else:
        start_utc = _parse_simulation_start(start, selected_location.timezone)
    if start_utc.minute % 30 or start_utc.second or start_utc.microsecond:
        raise typer.BadParameter("start must be on a UTC half-hour boundary")

    config_dao = HouseConfigDao(db_path)
    try:
        if random_configs is None:
            selected_id = house_config or "house-1--6.6KW-5KW-INV"
            selected_house_config = config_dao.get_house_config(selected_id)
            if selected_house_config is None:
                raise typer.BadParameter(f"Unknown house configuration: {selected_id}")
            house_quantities = {selected_house_config.house_config_id: houses}
        else:
            if house_config is not None:
                raise typer.BadParameter(
                    "--house-config cannot be combined with --random-configs"
                )
            configs = config_dao.get_all_house_configs()
            if not 1 <= random_configs <= min(houses, len(configs)):
                raise typer.BadParameter(
                    "random-configs must be between 1 and the smaller of "
                    "houses and available house configs"
                )
            rng = random.Random(assignment_seed)
            available = [(config, _house_config_weight(config)) for config in configs]
            chosen = []
            for _ in range(random_configs):
                selected = rng.choices(
                    available, weights=[weight for _, weight in available], k=1
                )[0]
                chosen.append(selected)
                available.remove(selected)
            chosen_ids = [config.house_config_id for config, _ in chosen]
            counts = Counter(chosen_ids)
            counts.update(
                rng.choices(
                    chosen_ids,
                    weights=[weight for _, weight in chosen],
                    k=houses - random_configs,
                )
            )
            house_quantities = dict(counts)
    finally:
        config_dao.close()

    selected_battery = BatteryDao(db_path).get_battery_config(battery)
    if selected_battery is None:
        raise typer.BadParameter(f"Unknown battery configuration: {battery}")
    request = SimulationRequest(
        objective_mode=objective,
        start_time_utc=start_utc,
        end_time_utc=start_utc + timedelta(hours=duration_hours),
        location_name=selected_location.location_name,
        battery_config_id=selected_battery.battery_config_id,
        houses=house_quantities,
        fixed_buy_aud_per_kwh=fixed_buy,
        fixed_sell_aud_per_kwh=fixed_sell,
    )
    if random_configs is not None:
        console.print(
            "Assigned houses: "
            + ", ".join(
                f"{config_id}={count}"
                for config_id, count in sorted(house_quantities.items())
            )
        )
    printer = SimulationPrinter(request, db_path)
    dao = SimulationDao(db_path, request)
    try:
        if dao.run_exists():
            printer.print_reactive_summary()
            printer.print_terminal_value_summary()
            printer.print_stored_summaries()
            return
    finally:
        dao.close()
    startup_started = perf_counter()
    if prepare_data:
        try:
            ensure_data(request, DB_PATH)
        except IncompleteEmissionsDataError as exc:
            console.print(f"Error: {exc}", style="red")
            raise typer.Exit(1) from None
    else:
        DemandPipeline().ensure_data(
            request.model_copy(
                update={
                    "end_time_utc": request.end_time_utc + timedelta(minutes=30),
                }
            ),
            DB_PATH,
        )
    startup_seconds = perf_counter() - startup_started
    engine = SimulationEngine(request, db_path, solver)
    steps = engine.run(on_step=printer.print_step)
    printer.print_summary(steps)
    if steps:
        if engine.perfect_result is not None:
            printer.print_perfect_foresight_summary(steps, *engine.perfect_result)
        printer.print_no_battery_summary()
        printer.print_reactive_summary()
        printer.print_stored_summaries()
    console.print(f"Total startup data preparation time: {startup_seconds:.2f} seconds")


@cli.command()
def run_pv_pipeline(
    location: Annotated[
        str, typer.Option("--location", "-l", help="Seeded location name")
    ] = "Brisbane",
    start: Annotated[
        datetime | None,
        typer.Option(help="UTC start, e.g. 2026-09-20 (default: 3 days ago)"),
    ] = None,
    days: Annotated[int, typer.Option(help="Window length in days")] = 1,
    houses: Annotated[int, typer.Option(help="Number of houses")] = 3,
    pv_only: Annotated[
        bool, typer.Option(help="Skip the weather pipeline (weather already stored)")
    ] = False,
):
    """Run the PV pipeline after initializing with reset-db."""
    if start is None:
        start = datetime.now(UTC) - timedelta(days=3)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)

    db_path = str(DB_PATH)
    house_location = LocationDao(db_path).get_location(location)
    if house_location is None:
        raise typer.BadParameter(f"Unknown location: {location}")
    house_config = HouseConfigDao(db_path).get_all_house_configs()[0]

    request = SimulationRequest(
        objective_mode=ObjectiveMode.COST,
        start_time_utc=start,
        end_time_utc=start + timedelta(days=days),
        location_name=house_location.location_name,
        battery_config_id=BatteryDao(db_path)
        .get_all_battery_configs()[0]
        .battery_config_id,
        houses={house_config.house_config_id: houses},
    )

    if pv_only:
        ensure_data(request, DB_PATH, pipelines=(PvPipeline(),))
    else:
        ensure_data(request, DB_PATH, pipelines=(WeatherPipeline(), PvPipeline()))
    console.print(f"PV stored for {houses} houses, {start:%Y-%m-%d} + {days} day(s).")


@cli.command()
def run_aemo_pipelines(
    location: Annotated[
        str, typer.Option("--location", "-l", help="Seeded location name")
    ] = "Brisbane",
    start: Annotated[
        datetime | None,
        typer.Option(
            help="UTC start (default fits the days before AEMO archive cutoff)"
        ),
    ] = None,
    days: Annotated[int, typer.Option(help="Number of one-day runs")] = 1,
):
    """Load AEMO data after initializing with reset-db."""
    if days <= 0:
        raise typer.BadParameter("days must be greater than zero")
    if start is None:
        start = _default_simulation_start(datetime.now(UTC), days * 24)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    start = start.astimezone(UTC)
    if start.minute % 30 or start.second or start.microsecond:
        raise typer.BadParameter("start must be on a UTC half-hour boundary")

    db_path = str(DB_PATH)
    house_location = LocationDao(db_path).get_location(location)
    if house_location is None:
        raise typer.BadParameter(f"Unknown location: {location}")
    house_config = HouseConfigDao(db_path).get_all_house_configs()[0]
    battery_config = BatteryDao(db_path).get_all_battery_configs()[0]
    for day_index in range(days):
        day_start = start + timedelta(days=day_index)
        day_end = day_start + timedelta(days=1)
        request = SimulationRequest(
            objective_mode=ObjectiveMode.COST,
            start_time_utc=day_start,
            end_time_utc=day_end,
            location_name=house_location.location_name,
            battery_config_id=battery_config.battery_config_id,
            houses={house_config.house_config_id: 1},
        )
        ensure_data(request, DB_PATH, pipelines=(EmissionsPipeline(), PricePipeline()))
    console.print(
        f"Emissions and prices stored for {location}, "
        f"{start:%Y-%m-%d} + {days} day(s). "
        "Emissions forecasts use historical half-hour medians; "
        "missing price forecasts use historical half-hour medians."
    )


@cli.command()
def show_aemo_data(
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            "-k",
            help="realised-emissions, forecast-emissions, realised-prices, "
            "or forecast-prices",
        ),
    ] = "realised-emissions",
    location: Annotated[
        str, typer.Option("--location", "-l", help="Seeded location name")
    ] = "Brisbane",
    start: Annotated[
        datetime | None,
        typer.Option(help="UTC start; show the following 24 hours"),
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum rows to display")] = 10,
):
    """Display stored AEMO emissions or price rows."""
    datasets = {
        "realised-emissions": (
            "realised_emissions_5m",
            ("timestamp_utc", "carbon_intensity_t_per_mwh"),
        ),
        "forecast-emissions": (
            "forecast_emissions_30m",
            ("run_time_utc", "target_time_utc", "carbon_intensity_t_per_mwh"),
        ),
        "realised-prices": (
            "realised_pricing_5m",
            ("timestamp_utc", "buy_tariff_aud", "sell_tariff_aud"),
        ),
        "forecast-prices": (
            "forecast_pricing_30m",
            (
                "run_time_utc",
                "target_time_utc",
                "buy_tariff_aud",
                "sell_tariff_aud",
                "forecast_source",
            ),
        ),
    }
    if kind not in datasets:
        raise typer.BadParameter(f"kind must be one of: {', '.join(datasets)}")
    if limit <= 0:
        raise typer.BadParameter("limit must be greater than zero")
    if not DB_PATH.exists():
        raise typer.BadParameter(f"Database does not exist: {DB_PATH}")

    table_name, columns = datasets[kind]
    time_column = columns[0]
    params: list[str | int] = [location]
    where = "WHERE location_name = ?"
    if start is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        start = start.astimezone(UTC)
        where += f" AND {time_column} >= ? AND {time_column} < ?"
        params.extend([start.isoformat(), (start + timedelta(days=1)).isoformat()])

    order = ", ".join(columns[:2] if kind.startswith("forecast") else columns[:1])
    with sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table_name} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table_name} {where} "
            f"ORDER BY {order} LIMIT ?",
            [*params, limit],
        ).fetchall()

    labels = {
        "timestamp_utc": "time UTC",
        "run_time_utc": "run UTC",
        "target_time_utc": "target UTC",
        "carbon_intensity_t_per_mwh": "tCO2e/MWh",
        "buy_tariff_aud": "buy AUD/kWh",
        "sell_tariff_aud": "sell AUD/kWh",
        "forecast_source": "source",
    }
    result = Table(title=f"{kind} - {location} ({len(rows)} of {count} rows)")
    for column in columns:
        result.add_column(labels[column])
    for row in rows:
        result.add_row(
            *(
                datetime.fromisoformat(row[column]).strftime("%Y-%m-%d %H:%M")
                if column.endswith("_utc")
                else str(row[column])
                for column in columns
            )
        )
    console.print(result)


def entry_point():
    """Run freeze_support before starting the CLI."""
    mp.freeze_support()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    cli()


if __name__ == "__main__":
    entry_point()
