from unittest.mock import Mock

import pytest

from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from backend_v2.community_optimiser.terminal_value import (
    TerminalValue,
    estimate_balanced_terminal_value,
    estimate_objective_terminal_value,
    estimate_terminal_value,
)
from database_v2.models.optimiser_models import OptimiserInput, Solver
from database_v2.models.request_models import ObjectiveMode
from tests.factories import BatteryConfigFactory


@pytest.fixture
def terminal_forecast() -> OptimiserInput:
    """A daily forecast with predictable import savings and battery wear."""
    return OptimiserInput(
        objective_mode=ObjectiveMode.BALANCED,
        battery_constants=BatteryConfigFactory.build(
            capacity_kwh=2,
            max_power_kw=2,
            efficiency=1,
            min_soc_frac=0,
            max_soc_frac=1,
            max_cycles_24h=1,
            replacement_cost_aud=40,
            lifetime_cycles=100,
        ),
        initial_soc_kwh=1.0,
        battery_throughput_history=[0] * 48,
        tariffs_buy_aud_per_kwh=[0.3] * 48,
        tariffs_sell_aud_per_kwh=[0] * 48,
        carbon_intensity_t_per_mwh=[0.5] * 48,
        demand_aggregate_kw=[1] * 48,
        pv_aggregate_kw=[0] * 48,
    )


@pytest.mark.parametrize("mode", list(ObjectiveMode))
def test_terminal_energy_estimates_both_objectives_including_wear(
    mode, terminal_forecast: OptimiserInput
) -> None:
    data = terminal_forecast.model_copy(update={"objective_mode": mode})
    # The one remaining kWh avoids $0.30 of imports and incurs $0.10 of wear.
    cost, emissions = estimate_terminal_value(data, OptimiserRunner(Solver.HIGHS))
    assert cost == pytest.approx(0.2)
    assert emissions == pytest.approx(0.0005)
    assert data.objective_mode is mode
    assert data.initial_soc_kwh == 1


def test_terminal_value_preserves_cycle_history_in_both_starting_states(
    terminal_forecast: OptimiserInput,
) -> None:
    data = terminal_forecast
    # The budget only becomes available in the last half-hour of the horizon.
    data.battery_throughput_history = [0.0] * 47 + [4.0]
    assert estimate_terminal_value(
        data, OptimiserRunner(Solver.HIGHS)
    ) == pytest.approx((0.1, 0.00025))


def test_signed_value_when_terminal_energy_blocks_negative_price_charging(
    terminal_forecast: OptimiserInput,
) -> None:
    data = terminal_forecast.model_copy(
        update={
            "initial_soc_kwh": 2.0,
            "tariffs_buy_aud_per_kwh": [-1] * 48,
            "tariffs_sell_aud_per_kwh": [-2] * 48,
            "demand_aggregate_kw": [0] * 48,
        }
    )
    cost, _ = estimate_terminal_value(data, OptimiserRunner(Solver.HIGHS))
    # Keep the estimate's sign rather than treating every value as a credit.
    assert cost < 0


def test_no_stored_energy_above_minimum_has_zero_value_without_solving(
    terminal_forecast: OptimiserInput,
) -> None:
    runner = Mock(spec=OptimiserRunner)
    data = terminal_forecast.model_copy(update={"initial_soc_kwh": 0.0})
    assert estimate_terminal_value(data, runner) == (0, 0)
    runner.run.assert_not_called()


def test_dedicated_balanced_estimate_uses_carbon_weight_and_both_starting_states(
    terminal_forecast,
):
    data = terminal_forecast.model_copy(update={"carbon_price_aud_per_t": 100})
    value = estimate_objective_terminal_value(
        data, OptimiserRunner(Solver.HIGHS), ObjectiveMode.BALANCED
    )
    assert value == pytest.approx(
        0.25
    )  # $0.20 financial + $0.05 carbon in this fixture.
    runner = Mock(spec=OptimiserRunner)
    runner.run.side_effect = [Mock(objective=70), Mock(objective=67)]
    assert estimate_objective_terminal_value(data, runner, ObjectiveMode.BALANCED) == 3
    inputs = [call.args[0] for call in runner.run.call_args_list]
    assert [entry.initial_soc_kwh for entry in inputs] == [0, 1]
    assert all(entry.objective_mode is ObjectiveMode.BALANCED for entry in inputs)
    assert all(entry.carbon_price_aud_per_t == 100 for entry in inputs)


def test_balanced_components_keep_the_same_dispatch_and_interval_carbon_rates(
    terminal_forecast,
):
    data = terminal_forecast.model_copy(
        update={"carbon_prices_aud_per_t": [75] * 24 + [80] * 24}
    )
    runner = OptimiserRunner(Solver.HIGHS)
    estimate = estimate_balanced_terminal_value(data, runner)
    assert estimate == pytest.approx(TerminalValue(0.24, 0.2, 0.0005, 0.04))
    assert estimate.objective == pytest.approx(
        estimate.financial_aud + estimate.carbon_aud
    )

    # The carbon component is retained from dispatch, not reweighted at one rate.
    runner = Mock(spec=OptimiserRunner)
    runner.run.side_effect = [
        Mock(
            objective=170,
            financial_cost_aud=100,
            emissions_tonnes=1,
            carbon_cost_aud=70,
        ),
        Mock(
            objective=140,
            financial_cost_aud=90,
            emissions_tonnes=0.75,
            carbon_cost_aud=50,
        ),
    ]
    assert estimate_balanced_terminal_value(data, runner) == TerminalValue(
        30, 10, 0.25, 20
    )
    assert runner.run.call_count == 2
    assert [call.args[0].initial_soc_kwh for call in runner.run.call_args_list] == [
        0,
        1,
    ]
    assert all(
        call.args[0].objective_mode is ObjectiveMode.BALANCED
        for call in runner.run.call_args_list
    )


def test_balanced_components_at_minimum_soc_need_no_solves(terminal_forecast):
    runner = Mock(spec=OptimiserRunner)
    data = terminal_forecast.model_copy(update={"initial_soc_kwh": 0.0})
    assert estimate_balanced_terminal_value(data, runner) == TerminalValue(0, 0, 0, 0)
    runner.run.assert_not_called()
