import pytest

from backend_v2.live_controller.reactive_self_consumption_controller import (
    ReactiveSelfConsumptionController,
)
from database_v2.models.optimiser_models import (
    LiveControllerInput,
)
from database_v2.models.request_models import BatteryConfig


@pytest.fixture
def battery() -> BatteryConfig:
    return BatteryConfig(
        battery_config_id="",
        capacity_kwh=20.0,
        max_power_kw=5.0,
        efficiency=0.95,
        min_soc_frac=0.1,
        max_soc_frac=0.9,
        replacement_cost_aud=200_000,
        lifetime_cycles=6000,
        max_cycles_24h=1.0,
    )


def measurement(
    *,
    demand_kw: float,
    pv_kw: float,
    soc_kwh: float = 10.0,
    sell: float = 0.1,
) -> LiveControllerInput:
    return LiveControllerInput(
        demand_kw=demand_kw,
        pv_kw=pv_kw,
        soc_kwh=soc_kwh,
        buy_tariff_aud_per_kwh=0.3,
        sell_tariff_aud_per_kwh=sell,
    )


@pytest.mark.parametrize("charge", [False, True])
def test_tiny_soc_limited_action_is_idle_and_balance_is_preserved(battery, charge):
    soc = battery.max_soc_kwh - 0.001 if charge else battery.min_soc_kwh + 0.001
    data = measurement(
        demand_kw=0 if charge else 4, pv_kw=4 if charge else 0, soc_kwh=soc
    )
    controller = ReactiveSelfConsumptionController(battery)
    allowance = controller.cycle_throughput_remaining_kwh
    result = controller.step(data)
    assert result.charge_kw == result.discharge_kw == result.cycle_throughput_kwh == 0
    assert result.next_soc_kwh == soc
    assert controller.cycle_throughput_remaining_kwh == allowance
    assert data.pv_kw + result.grid_import_kw == pytest.approx(
        data.demand_kw + result.grid_export_kw
    )


def test_discharges_only_into_current_deficit(battery: BatteryConfig) -> None:
    controller = ReactiveSelfConsumptionController(battery)

    result = controller.step(measurement(demand_kw=4.0, pv_kw=1.0))

    assert result.discharge_kw == pytest.approx(3.0)
    assert result.charge_kw == pytest.approx(0.0)
    assert result.grid_import_kw == pytest.approx(0.0)
    assert result.optimiser_charge_kw is None
    assert result.optimiser_discharge_kw is None
    assert "reactive-deficit-discharge" in result.reasons


def test_charges_only_from_current_surplus(battery: BatteryConfig) -> None:
    controller = ReactiveSelfConsumptionController(battery)

    result = controller.step(measurement(demand_kw=1.0, pv_kw=5.0))

    assert result.charge_kw == pytest.approx(4.0)
    assert result.discharge_kw == pytest.approx(0.0)
    assert result.grid_export_kw == pytest.approx(0.0)
    assert "reactive-surplus-charge" in result.reasons


def test_large_export_is_not_capacity_limited(
    battery: BatteryConfig,
) -> None:
    controller = ReactiveSelfConsumptionController(battery)

    result = controller.step(measurement(demand_kw=0.0, pv_kw=20.0))

    assert result.charge_kw == pytest.approx(5.0)
    assert result.grid_export_kw == pytest.approx(15.0)


def test_negative_value_export_is_curtailed_after_charging(
    battery: BatteryConfig,
) -> None:
    controller = ReactiveSelfConsumptionController(battery)

    result = controller.step(
        measurement(demand_kw=0.0, pv_kw=4.0, soc_kwh=18.0, sell=-0.1)
    )

    assert result.charge_kw == pytest.approx(0.0)
    assert result.curtailed_pv_kw == pytest.approx(4.0)
    assert result.grid_export_kw == pytest.approx(0.0)


def test_cycle_allowance_can_prevent_reactive_dispatch(
    battery: BatteryConfig,
) -> None:
    controller = ReactiveSelfConsumptionController(
        battery,
        cycle_throughput_remaining_kwh=0.0,
    )

    result = controller.step(measurement(demand_kw=3.0, pv_kw=0.0))

    assert result.discharge_kw == pytest.approx(0.0)
    assert result.grid_import_kw == pytest.approx(3.0)
    assert "cycle-throughput-limit" in result.reasons


def test_cycle_limit_rechecks_negative_price_curtailment(
    battery: BatteryConfig,
) -> None:
    controller = ReactiveSelfConsumptionController(
        battery,
        cycle_throughput_remaining_kwh=0.0,
    )

    result = controller.step(measurement(demand_kw=0.0, pv_kw=4.0, sell=-0.1))

    assert result.charge_kw == pytest.approx(0.0)
    assert result.curtailed_pv_kw == pytest.approx(4.0)
    assert result.grid_export_kw == pytest.approx(0.0)
    assert "cycle-throughput-limit" in result.reasons


def test_finishes_after_one_control_period(battery: BatteryConfig) -> None:
    controller = ReactiveSelfConsumptionController(battery)
    data = measurement(demand_kw=1.0, pv_kw=1.0)

    for _ in range(6):
        controller.step(data)

    assert controller.finished()
    with pytest.raises(RuntimeError, match="already complete"):
        controller.step(data)
