import pytest
from pydantic import ValidationError

from database_v2.models.optimiser_models import (
    OptimiserInput,
)
from database_v2.models.request_models import BatteryConfig, ObjectiveMode


def test_optimiser_input_valid():
    horizon = 48
    data = OptimiserInput(
        objective_mode=ObjectiveMode.COST,
        horizon_periods=horizon,
        battery_constants=BatteryConfig(
            battery_config_id="",
            capacity_kwh=100.0,
            max_power_kw=50.0,
            efficiency=0.95,
            min_soc_frac=0.1,
            max_soc_frac=0.9,
            replacement_cost_aud=200_000,
            lifetime_cycles=6000,
            max_cycles_24h=1.0,
        ),
        initial_soc_kwh=50.0,
        battery_throughput_history=[0.0] * 48,
        carbon_price_aud_per_t=50.0,
        tariffs_buy_aud_per_kwh=[0.3] * horizon,
        tariffs_sell_aud_per_kwh=[0.1] * horizon,
        carbon_intensity_t_per_mwh=[0.8] * horizon,
        demand_aggregate_kw=[10.0] * horizon,
        pv_aggregate_kw=[5.0] * horizon,
    )
    assert data.horizon_periods == 48


def test_optimiser_input_invalid_history_length():
    horizon = 48
    with pytest.raises(ValidationError):
        OptimiserInput(
            battery_constants=BatteryConfig(
                battery_config_id="",
                capacity_kwh=100.0,
                max_power_kw=50.0,
                efficiency=0.95,
                min_soc_frac=0.1,
                max_soc_frac=0.9,
                replacement_cost_aud=200_000,
                lifetime_cycles=6000,
                max_cycles_24h=1.0,
            ),
            initial_soc_kwh=50.0,
            battery_throughput_history=[0.0] * 10,  # Expected 48
            carbon_price_aud_per_t=50.0,
            tariffs_buy_aud_per_kwh=[0.3] * horizon,
            tariffs_sell_aud_per_kwh=[0.1] * horizon,
            carbon_intensity_t_per_mwh=[0.8] * horizon,
            demand_aggregate_kw=[10.0] * horizon,
            pv_aggregate_kw=[5.0] * horizon,
        )


def test_optimiser_input_invalid_horizon_length():
    horizon = 48
    with pytest.raises(ValidationError):
        OptimiserInput(
            battery_constants=BatteryConfig(
                battery_config_id="",
                capacity_kwh=100.0,
                max_power_kw=50.0,
                efficiency=0.95,
                min_soc_frac=0.1,
                max_soc_frac=0.9,
                replacement_cost_aud=200_000,
                lifetime_cycles=6000,
                max_cycles_24h=1.0,
            ),
            initial_soc_kwh=50.0,
            battery_throughput_history=[0.0] * 48,
            carbon_price_aud_per_t=50.0,
            tariffs_buy_aud_per_kwh=[0.3] * 10,  # Expected 48
            tariffs_sell_aud_per_kwh=[0.1] * horizon,
            carbon_intensity_t_per_mwh=[0.8] * horizon,
            demand_aggregate_kw=[10.0] * horizon,
            pv_aggregate_kw=[5.0] * horizon,
        )


def test_optimiser_input_rejects_initial_soc_outside_operating_window():
    horizon = 48
    with pytest.raises(ValidationError, match="battery operating window"):
        OptimiserInput(
            battery_constants=BatteryConfig(
                battery_config_id="",
                capacity_kwh=100.0,
                max_power_kw=50.0,
                efficiency=0.95,
                min_soc_frac=0.1,
                max_soc_frac=0.9,
                replacement_cost_aud=200_000,
                lifetime_cycles=6000,
                max_cycles_24h=1.0,
            ),
            initial_soc_kwh=5.0,
            battery_throughput_history=[0.0] * 48,
            carbon_price_aud_per_t=50.0,
            tariffs_buy_aud_per_kwh=[0.3] * horizon,
            tariffs_sell_aud_per_kwh=[0.1] * horizon,
            carbon_intensity_t_per_mwh=[0.8] * horizon,
            demand_aggregate_kw=[10.0] * horizon,
            pv_aggregate_kw=[5.0] * horizon,
        )


@pytest.mark.parametrize(
    ("objective", "rejected"),
    [
        (ObjectiveMode.COST, True),
        (ObjectiveMode.BALANCED, True),
        (ObjectiveMode.EMISSIONS, False),
    ],
)
def test_sell_above_buy_rejected_unless_emissions(objective, rejected):
    """A fixed buy of 0 against a market sell price would make the LP unbounded."""
    horizon = 48
    kwargs = dict(
        objective_mode=objective,
        battery_constants=BatteryConfig(
            battery_config_id="",
            capacity_kwh=100.0,
            max_power_kw=50.0,
            efficiency=0.95,
            min_soc_frac=0.1,
            max_soc_frac=0.9,
            replacement_cost_aud=200_000,
            lifetime_cycles=6000,
            max_cycles_24h=1.0,
        ),
        initial_soc_kwh=50.0,
        battery_throughput_history=[0.0] * 48,
        tariffs_buy_aud_per_kwh=[0.0] * horizon,
        tariffs_sell_aud_per_kwh=[0.08] * horizon,
        carbon_intensity_t_per_mwh=[0.8] * horizon,
        demand_aggregate_kw=[10.0] * horizon,
        pv_aggregate_kw=[5.0] * horizon,
    )
    if rejected:
        with pytest.raises(ValidationError, match="unbounded"):
            OptimiserInput(**kwargs)
    else:
        OptimiserInput(**kwargs)
