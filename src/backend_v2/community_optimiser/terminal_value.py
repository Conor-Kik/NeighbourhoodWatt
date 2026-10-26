"""Estimates the future value of energy left in the battery when a run ends."""

from typing import NamedTuple

from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from database_v2.models.optimiser_models import OptimiserInput, OptimiserOutput
from database_v2.models.request_models import ObjectiveMode


class TerminalValue(NamedTuple):
    """Baseline minus final-SOC totals from the same future strategy."""

    objective: float
    financial_aud: float
    emissions_tonnes: float
    carbon_aud: float


def _terminal_runs(
    data: OptimiserInput, runner: OptimiserRunner, mode: ObjectiveMode
) -> tuple[OptimiserOutput, OptimiserOutput] | None:
    """Compare future operation starting with remaining energy against minimum stored
    energy.
    """
    minimum = data.battery_constants.min_soc_kwh
    if abs(data.initial_soc_kwh - minimum) <= 1e-6:
        return None
    terminal_input = data.model_copy(update={"objective_mode": mode})
    baseline = runner.run(
        terminal_input.model_copy(update={"initial_soc_kwh": minimum})
    )
    terminal = runner.run(terminal_input)
    return baseline, terminal


def estimate_objective_terminal_value(
    data: OptimiserInput, runner: OptimiserRunner, mode: ObjectiveMode
) -> float:
    """Estimate how remaining battery energy improves the selected future objective."""
    pair = _terminal_runs(data, runner, mode)
    return pair[0].objective - pair[1].objective if pair else 0.0


def estimate_balanced_terminal_value(
    data: OptimiserInput, runner: OptimiserRunner
) -> TerminalValue:
    """Estimate financial and emissions credits from the same two Balanced forecasts."""
    pair = _terminal_runs(data, runner, ObjectiveMode.BALANCED)
    if pair is None:
        return TerminalValue(0, 0, 0, 0)
    baseline, terminal = pair
    values = []
    for field in (
        "objective",
        "financial_cost_aud",
        "emissions_tonnes",
        "carbon_cost_aud",
    ):
        before, after = getattr(baseline, field), getattr(terminal, field)
        if before is None or after is None:
            raise ValueError("Terminal solve is missing objective components")
        values.append(before - after)
    return TerminalValue(*values)


def estimate_terminal_value(
    data: OptimiserInput, runner: OptimiserRunner
) -> tuple[float, float]:
    """Return Cost (AUD) and Emissions (tonnes) values from separate paired solves.

    Both starting states share the forecast and throughput history. Keep signed
    values: extra energy can reduce the opportunity to charge at negative prices.
    These two values describe different optimal future policies.
    """
    return (
        estimate_objective_terminal_value(data, runner, ObjectiveMode.COST),
        estimate_objective_terminal_value(data, runner, ObjectiveMode.EMISSIONS),
    )
