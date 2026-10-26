from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from backend_v2.community_optimiser.optimiser_constants import (
    OPTIMISER_PERIOD_HOURS,
    PERIODS_PER_DAY,
)
from database_v2.models.request_models import BatteryConfig, ObjectiveMode

NonNegativeFloat = Annotated[float, Field(ge=0.0)]


class OptimiserInput(BaseModel):
    """Complete input data for one optimisation."""

    objective_mode: ObjectiveMode = ObjectiveMode.COST

    # Time data
    horizon_periods: int = Field(default=PERIODS_PER_DAY, ge=1)
    period_hours: float = Field(default=OPTIMISER_PERIOD_HOURS, gt=0.0)

    # Battery data
    battery_constants: BatteryConfig
    initial_soc_kwh: float = Field(default=0.0, ge=0.0)
    # Exactly one day of realised use, ordered from oldest to newest. Each new
    # forecast action replaces the sample at the matching position.
    battery_throughput_history: list[NonNegativeFloat]

    # Grid data
    carbon_price_aud_per_t: float = Field(default=0.0, ge=0.0)
    carbon_prices_aud_per_t: list[NonNegativeFloat] | None = None
    tariffs_buy_aud_per_kwh: list[float]
    tariffs_sell_aud_per_kwh: list[float]
    carbon_intensity_t_per_mwh: list[NonNegativeFloat]

    # Community data
    demand_aggregate_kw: list[NonNegativeFloat]
    pv_aggregate_kw: list[NonNegativeFloat]

    @model_validator(mode="after")
    def validate_time_series_lengths(self) -> "OptimiserInput":
        horizon = self.horizon_periods

        periods_per_day = 24.0 / self.period_hours
        if abs(periods_per_day - round(periods_per_day)) > 1e-9:
            raise ValueError("period_hours must divide 24 hours")
        if len(self.battery_throughput_history) != round(periods_per_day):
            raise ValueError("len(battery_throughput_history) != periods_per_day")

        horizon_fields = {
            "tariffs_buy_aud_per_kwh": self.tariffs_buy_aud_per_kwh,
            "tariffs_sell_aud_per_kwh": self.tariffs_sell_aud_per_kwh,
            "carbon_intensity_t_per_mwh": self.carbon_intensity_t_per_mwh,
            "demand_aggregate_kw": self.demand_aggregate_kw,
            "pv_aggregate_kw": self.pv_aggregate_kw,
        }

        for field_name, sequence in horizon_fields.items():
            if len(sequence) != horizon:
                raise ValueError(f"len({field_name}) != horizon_periods")
        if (
            self.carbon_prices_aud_per_t is not None
            and len(self.carbon_prices_aud_per_t) != horizon
        ):
            raise ValueError("len(carbon_prices_aud_per_t) != horizon_periods")

        return self

    @model_validator(mode="after")
    def validate_bounded_tariffs(self) -> "OptimiserInput":
        # Selling above the buy price lets the LP import and export without limit.
        if self.objective_mode != ObjectiveMode.EMISSIONS and any(
            sell > buy
            for buy, sell in zip(
                self.tariffs_buy_aud_per_kwh,
                self.tariffs_sell_aud_per_kwh,
                strict=True,
            )
        ):
            raise ValueError("Sell price above buy price makes the optimiser unbounded")
        return self

    @model_validator(mode="after")
    def validate_initial_soc(self) -> "OptimiserInput":
        minimum_soc = self.battery_constants.min_soc_kwh
        maximum_soc = self.battery_constants.max_soc_kwh
        if not minimum_soc <= self.initial_soc_kwh <= maximum_soc:
            raise ValueError(
                "initial_soc_kwh must be within the battery operating window "
                f"[{minimum_soc}, {maximum_soc}]"
            )
        return self


class Solver(StrEnum):
    """Supported optimisation solvers."""

    HIGHS = "highs"
    GUROBI = "gurobi_direct"


class SolverUnavailableError(RuntimeError):
    """Raised when the requested optimiser backend cannot be used."""


class OptimiserOutput(BaseModel):
    """Results returned by OptimiserRunner."""

    solver: Solver
    objective_mode: ObjectiveMode
    objective: float
    # Whole-horizon components of this solve, not the returned step's costs.
    financial_cost_aud: float | None = None
    emissions_tonnes: float | None = None
    carbon_cost_aud: float | None = None
    shadow_price: float
    # Starting SOC used for this solve, before the first returned action.
    soc_kwh: NonNegativeFloat
    charge_kw: NonNegativeFloat
    discharge_kw: NonNegativeFloat
    grid_import_kw: NonNegativeFloat
    grid_export_kw: NonNegativeFloat
    curtailed_pv_kw: NonNegativeFloat
    rolling_throughput_kwh: NonNegativeFloat
    # Forecast demand minus forecast solar before applying the battery action.
    expected_net_load_kw: float | None = None
    cycle_throughput_remaining_kwh: NonNegativeFloat | None = None
    # Stored energy after this decision; supplied for a full-horizon result.
    next_soc_kwh: NonNegativeFloat | None = None


class LiveControllerInput(BaseModel):
    """Input data for one LiveController step."""

    soc_kwh: NonNegativeFloat

    demand_kw: NonNegativeFloat
    pv_kw: NonNegativeFloat
    buy_tariff_aud_per_kwh: float
    sell_tariff_aud_per_kwh: float
    carbon_intensity_t_per_mwh: NonNegativeFloat | None = None
    carbon_price_aud_per_t: float | None = None


class LiveControllerOutput(BaseModel):
    """Results returned by LiveController."""

    optimiser_charge_kw: NonNegativeFloat | None = None
    optimiser_discharge_kw: NonNegativeFloat | None = None
    charge_kw: NonNegativeFloat
    discharge_kw: NonNegativeFloat
    grid_import_kw: NonNegativeFloat
    grid_export_kw: NonNegativeFloat
    curtailed_pv_kw: NonNegativeFloat
    next_soc_kwh: NonNegativeFloat
    cycle_throughput_kwh: NonNegativeFloat
    reasons: tuple[str, ...]


class LiveStep(BaseModel):
    """One realised five-minute input and its controller decision."""

    timestamp_utc: datetime
    data: LiveControllerInput
    output: LiveControllerOutput


class SimulationStep(BaseModel):
    """One half-hour forecast solve and its realised dispatches."""

    timestamp_utc: datetime
    forecast: OptimiserInput
    plan: OptimiserOutput
    live_steps: tuple[LiveStep, ...]


class ForecastWindow(BaseModel):
    run_time_utc: datetime
    carbon_price_aud_per_t: float
    target_times_utc: list[datetime]
    demand_kw: list[float]
    pv_kw: list[float]
    buy_tariff_aud_per_kwh: list[float]
    sell_tariff_aud_per_kwh: list[float]
    carbon_intensity_t_per_mwh: list[float]


class RealisedWindows(BaseModel):
    run_times_utc: list[datetime]
    agg_demand_kw: list[float]
    agg_pv_kw: list[float]
    buy_tariff_aud_per_kwh: list[float]
    sell_tariff_aud_per_kwh: list[float]
    carbon_intensity_t_per_mwh: list[float]
