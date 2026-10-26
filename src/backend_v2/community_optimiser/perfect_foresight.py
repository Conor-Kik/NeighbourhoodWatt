"""Uses all actual data from a completed run to calculate a hindsight benchmark."""

from datetime import timedelta

from backend_v2.community_optimiser.optimiser_constants import LIVE_CONTROL_STEP_MINUTES
from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from database_v2.models.optimiser_models import (
    OptimiserInput,
    OptimiserOutput,
    SimulationStep,
    Solver,
)


def evaluate_perfect_foresight(
    steps: list[SimulationStep], solver: Solver
) -> tuple[OptimiserInput, list[OptimiserOutput]]:
    """Solve once at five-minute resolution with all realised data visible."""
    if not steps or not steps[0].live_steps:
        raise ValueError("perfect foresight requires completed realised steps")
    if any(
        step.forecast.objective_mode != steps[0].forecast.objective_mode
        for step in steps
    ):
        raise ValueError("all rolling steps must use the same objective")

    live = [sample for step in steps for sample in step.live_steps]
    period_hours = LIVE_CONTROL_STEP_MINUTES / 60.0
    period_delta = timedelta(minutes=LIVE_CONTROL_STEP_MINUTES)
    start = live[0].timestamp_utc
    for index, sample in enumerate(live):
        if sample.timestamp_utc != start + index * period_delta:
            raise ValueError("realised five-minute periods must be contiguous")

    carbon_prices = [
        step.forecast.carbon_price_aud_per_t for step in steps for _ in step.live_steps
    ]
    battery = steps[0].forecast.battery_constants
    data = OptimiserInput(
        objective_mode=steps[0].forecast.objective_mode,
        horizon_periods=len(live),
        period_hours=period_hours,
        battery_constants=battery,
        initial_soc_kwh=live[0].data.soc_kwh,
        battery_throughput_history=[0.0] * round(24.0 / period_hours),
        carbon_price_aud_per_t=carbon_prices[0],
        carbon_prices_aud_per_t=carbon_prices,
        tariffs_buy_aud_per_kwh=[s.data.buy_tariff_aud_per_kwh for s in live],
        tariffs_sell_aud_per_kwh=[s.data.sell_tariff_aud_per_kwh for s in live],
        carbon_intensity_t_per_mwh=[
            s.data.carbon_intensity_t_per_mwh or 0.0 for s in live
        ],
        demand_aggregate_kw=[s.data.demand_kw for s in live],
        pv_aggregate_kw=[s.data.pv_kw for s in live],
    )
    return data, OptimiserRunner(solver).run(data, all_decisions=True)
