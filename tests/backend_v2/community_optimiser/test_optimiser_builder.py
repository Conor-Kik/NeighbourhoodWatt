import pyomo.environ as pyo
import pytest

from backend_v2.community_optimiser.optimiser_builder import build_community_optimiser
from database_v2.models.optimiser_models import (
    OptimiserInput,
)
from database_v2.models.request_models import BatteryConfig, ObjectiveMode


@pytest.fixture
def sample_optimiser_input():
    """Fixture providing a valid OptimiserInput for testing."""
    horizon = 48
    return OptimiserInput(
        objective_mode=ObjectiveMode.COST,
        horizon_periods=horizon,
        battery_constants=BatteryConfig(
            battery_config_id="",
            capacity_kwh=100.0,
            max_power_kw=5.0,
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
        pv_aggregate_kw=[15.0] * horizon,
    )


def test_build_model_cost_mode(sample_optimiser_input):
    sample_optimiser_input.objective_mode = ObjectiveMode.COST
    model = build_community_optimiser(sample_optimiser_input)

    assert isinstance(model, pyo.ConcreteModel)
    assert model.name == "community_optimiser"
    assert model.objective is not None
    assert hasattr(model, "SOC")
    assert hasattr(model, "V")
    assert hasattr(model, "Q")
    assert len(model.SOC) == sample_optimiser_input.horizon_periods + 1
    assert len(model.soc_update_constraint) == sample_optimiser_input.horizon_periods
    assert hasattr(model, "bus_balance_constraint")
    assert not hasattr(model, "grid_limit_constraint")
    assert hasattr(model, "initial_soc_constraint")
    assert not model.SOC[0].fixed


def test_build_model_emissions_mode(sample_optimiser_input):
    sample_optimiser_input.objective_mode = ObjectiveMode.EMISSIONS
    model = build_community_optimiser(sample_optimiser_input)

    assert isinstance(model, pyo.ConcreteModel)
    assert model.objective is not None


def test_build_model_balanced_mode(sample_optimiser_input):
    sample_optimiser_input.objective_mode = ObjectiveMode.BALANCED
    model = build_community_optimiser(sample_optimiser_input)

    assert isinstance(model, pyo.ConcreteModel)
    assert model.objective is not None


@pytest.mark.parametrize("horizon", [12, 49])
def test_rolling_cycle_window_supports_non_daily_horizons(
    sample_optimiser_input,
    horizon,
):
    values = sample_optimiser_input.model_dump()
    values.update(
        horizon_periods=horizon,
        tariffs_buy_aud_per_kwh=[0.3] * horizon,
        tariffs_sell_aud_per_kwh=[0.1] * horizon,
        carbon_intensity_t_per_mwh=[0.8] * horizon,
        demand_aggregate_kw=[10.0] * horizon,
        pv_aggregate_kw=[15.0] * horizon,
    )
    data = OptimiserInput(**values)

    model = build_community_optimiser(data)

    assert len(model.Y) == horizon
    assert len(model.rolling_window_update_constraint) == horizon - 1


def test_invalid_objective_mode(sample_optimiser_input):
    sample_optimiser_input.objective_mode = "InvalidMode"  # type: ignore
    with pytest.raises(ValueError, match="Unknown objective mode"):
        build_community_optimiser(sample_optimiser_input)
