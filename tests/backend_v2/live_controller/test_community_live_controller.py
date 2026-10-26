import pytest

from backend_v2.live_controller.live_controller import LiveController
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    OptimiserOutput,
    Solver,
)
from database_v2.models.request_models import BatteryConfig, ObjectiveMode


@pytest.fixture
def battery() -> BatteryConfig:
    return BatteryConfig(
        battery_config_id="",
        capacity_kwh=20.0,
        max_power_kw=5.0,
        efficiency=0.95,
        min_soc_frac=0.1,
        max_soc_frac=0.9,
        replacement_cost_aud=0,
        lifetime_cycles=6000,
        max_cycles_24h=1.0,
    )


def make_plan(
    *,
    charge_kw: float = 0.0,
    discharge_kw: float = 0.0,
    expected_net_load_kw: float = 0.0,
    shadow_price: float = 0.2,
    objective: ObjectiveMode = ObjectiveMode.COST,
    rolling_throughput_kwh: float = 0.0,
    cycle_remaining_kwh: float | None = None,
) -> OptimiserOutput:
    # Choose grid flow so the optimiser balance implies expected_net_load_kw.
    grid_difference = expected_net_load_kw - discharge_kw + charge_kw
    return OptimiserOutput(
        solver=Solver.HIGHS,
        objective_mode=objective,
        objective=1.0,
        shadow_price=shadow_price,
        soc_kwh=10.0,
        charge_kw=charge_kw,
        discharge_kw=discharge_kw,
        grid_import_kw=max(grid_difference, 0.0),
        grid_export_kw=max(-grid_difference, 0.0),
        curtailed_pv_kw=0.0,
        rolling_throughput_kwh=rolling_throughput_kwh,
        expected_net_load_kw=expected_net_load_kw,
        cycle_throughput_remaining_kwh=cycle_remaining_kwh,
    )


def live_data(
    *,
    demand_kw: float,
    pv_kw: float,
    soc_kwh: float = 10.0,
    buy: float = 0.3,
    sell: float = 0.1,
    carbon_intensity: float | None = None,
    carbon_price: float | None = None,
) -> LiveControllerInput:
    return LiveControllerInput(
        demand_kw=demand_kw,
        pv_kw=pv_kw,
        soc_kwh=soc_kwh,
        buy_tariff_aud_per_kwh=buy,
        sell_tariff_aud_per_kwh=sell,
        carbon_intensity_t_per_mwh=carbon_intensity,
        carbon_price_aud_per_t=carbon_price,
    )


def test_controller_validates_step_length(battery: BatteryConfig) -> None:
    plan = make_plan()
    with pytest.raises(ValueError, match="greater than zero"):
        LiveController(battery, plan, live_step_minutes=0.0)
    with pytest.raises(ValueError, match="longer than the optimiser period"):
        LiveController(battery, plan, live_step_minutes=31.0)
    with pytest.raises(ValueError, match="whole steps"):
        LiveController(battery, plan, live_step_minutes=7.0)


@pytest.mark.parametrize("solver", [Solver.GUROBI, Solver.HIGHS])
@pytest.mark.parametrize("charge", [False, True])
@pytest.mark.parametrize("power", [1e-10, 0.05, 0.1, 0.2])
def test_small_solver_commands_are_suppressed_before_flow_and_soc_accounting(
    battery, solver, charge, power
):
    plan = make_plan(
        charge_kw=power if charge else 0,
        discharge_kw=0 if charge else power,
        expected_net_load_kw=0 if charge else 1,
        shadow_price=0.5,
    ).model_copy(update={"solver": solver})
    controller = LiveController(battery, plan)
    data = live_data(demand_kw=0 if charge else 1, pv_kw=0)
    allowance = controller.cycle_throughput_remaining_kwh
    result = controller.step(data)
    delivered = power if power >= 0.1 else 0.0
    assert result.charge_kw == pytest.approx(delivered if charge else 0)
    assert result.discharge_kw == pytest.approx(0 if charge else delivered)
    assert data.pv_kw + result.grid_import_kw + result.discharge_kw == pytest.approx(
        data.demand_kw
        + result.charge_kw
        + result.grid_export_kw
        + result.curtailed_pv_kw
    )
    throughput = (
        battery.efficiency * result.charge_kw + result.discharge_kw / battery.efficiency
    ) * controller.dt_h
    assert result.cycle_throughput_kwh == pytest.approx(throughput)
    assert controller.cycle_throughput_remaining_kwh == pytest.approx(
        allowance - throughput
    )
    assert result.next_soc_kwh == pytest.approx(
        data.soc_kwh
        + (
            battery.efficiency * result.charge_kw
            - result.discharge_kw / battery.efficiency
        )
        * controller.dt_h
    )
    assert controller.plan == plan


def test_small_throughput_limited_command_keeps_allowance_and_curtails_after_filter(
    battery,
):
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2,
            expected_net_load_kw=-4,
            shadow_price=0,
            cycle_remaining_kwh=0.001,
        ),
    )
    data = live_data(demand_kw=0, pv_kw=4, sell=-0.1)
    result = controller.step(data)
    assert result.charge_kw == result.discharge_kw == result.cycle_throughput_kwh == 0
    assert result.next_soc_kwh == data.soc_kwh
    assert controller.cycle_throughput_remaining_kwh == 0.001
    assert result.curtailed_pv_kw == 4
    assert result.grid_import_kw == result.grid_export_kw == 0


@pytest.mark.parametrize("charge", [False, True])
def test_soc_limited_tiny_command_does_not_move_soc_or_consume_plan(battery, charge):
    soc = battery.max_soc_kwh - 0.001 if charge else battery.min_soc_kwh + 0.001
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2 if charge else 0,
            discharge_kw=0 if charge else 2,
            expected_net_load_kw=-4 if charge else 4,
            shadow_price=0 if charge else 0.5,
        ),
    )
    result = controller.step(
        live_data(demand_kw=0 if charge else 4, pv_kw=4 if charge else 0, soc_kwh=soc)
    )
    assert result.charge_kw == result.discharge_kw == result.cycle_throughput_kwh == 0
    assert result.next_soc_kwh == soc
    assert controller.charge_remaining_kwh == (1 if charge else 0)
    assert controller.discharge_remaining_kwh == (0 if charge else 1)
    assert result.grid_export_kw == (4 if charge else 0)
    assert result.grid_import_kw == (0 if charge else 4)


def test_follows_preferred_surplus_charge(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(charge_kw=2.0, expected_net_load_kw=-3.0, shadow_price=0.0),
    )

    result = controller.step(live_data(demand_kw=0.0, pv_kw=3.0))

    assert result.charge_kw == pytest.approx(2.0)
    assert result.discharge_kw == pytest.approx(0.0)
    assert result.grid_export_kw == pytest.approx(1.0)


def test_follows_preferred_demand_serving_discharge(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(discharge_kw=2.0, expected_net_load_kw=3.0, shadow_price=0.5),
    )

    result = controller.step(live_data(demand_kw=3.0, pv_kw=0.0))

    assert result.charge_kw == pytest.approx(0.0)
    assert result.discharge_kw == pytest.approx(2.0)
    assert result.grid_import_kw == pytest.approx(1.0)


def test_suppresses_charge_when_expected_surplus_does_not_arrive(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(charge_kw=2.0, expected_net_load_kw=-3.0, shadow_price=0.05),
    )

    result = controller.step(live_data(demand_kw=0.0, pv_kw=0.0, buy=0.3))

    assert result.charge_kw == pytest.approx(0.0)
    assert result.discharge_kw == pytest.approx(0.0)
    assert "unexpected-grid-charge-suppressed" in result.reasons


def test_keeps_valuable_deliberate_grid_charge(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(charge_kw=2.0, expected_net_load_kw=3.0, shadow_price=0.5),
    )

    result = controller.step(live_data(demand_kw=3.0, pv_kw=0.0, buy=0.3))

    assert result.charge_kw == pytest.approx(2.0)
    assert result.grid_import_kw == pytest.approx(5.0)


def test_charge_is_limited_by_soc_headroom(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(charge_kw=5.0, expected_net_load_kw=-10.0, shadow_price=0.0),
    )

    result = controller.step(live_data(demand_kw=0.0, pv_kw=10.0, soc_kwh=17.9))

    assert result.charge_kw == pytest.approx(1.2 / battery.efficiency)


def test_discharge_is_limited_by_minimum_soc(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(discharge_kw=5.0, expected_net_load_kw=10.0, shadow_price=0.0),
    )

    result = controller.step(live_data(demand_kw=10.0, pv_kw=0.0, soc_kwh=2.1))

    assert result.discharge_kw == pytest.approx(1.14)


def test_rejects_soc_outside_operating_window(battery: BatteryConfig) -> None:
    controller = LiveController(battery, make_plan())

    with pytest.raises(ValueError, match="outside the battery operating window"):
        controller.step(live_data(demand_kw=1.0, pv_kw=0.0, soc_kwh=1.0))


def test_high_import_tariff_adds_value_based_discharge(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(battery, make_plan(shadow_price=0.1))

    result = controller.step(live_data(demand_kw=3.0, pv_kw=0.0, buy=0.5))

    assert result.discharge_kw == pytest.approx(3.0)
    assert result.grid_import_kw == pytest.approx(0.0)


@pytest.mark.parametrize("buy", [0.1, -0.1])
def test_cheap_import_does_not_add_grid_charging(battery: BatteryConfig, buy) -> None:
    """Leave unplanned grid buying to the MPC, even at negative prices."""
    controller = LiveController(battery, make_plan(shadow_price=0.5))

    result = controller.step(live_data(demand_kw=2.0, pv_kw=0.0, buy=buy))

    assert result.charge_kw == 0
    assert result.discharge_kw == 0
    assert result.grid_import_kw == pytest.approx(2.0)
    assert result.next_soc_kwh == pytest.approx(10.0)


@pytest.mark.parametrize("power_kw", [5.0, 500.0])
@pytest.mark.parametrize(
    "mode,import_value,advantage",
    [
        (ObjectiveMode.COST, 0.1, 0.02),
        (ObjectiveMode.BALANCED, 0.11, 0.02),
        (ObjectiveMode.EMISSIONS, 0.0005, 0.00002),
    ],
)
def test_no_unplanned_grid_charging_across_sizes_and_objectives(
    battery, power_kw, mode, import_value, advantage
):
    """Keep grid charging at zero despite a storage advantage in each objective."""
    scale = power_kw / battery.max_power_kw
    sized_battery = battery.model_copy(
        update={"capacity_kwh": battery.capacity_kwh * scale, "max_power_kw": power_kw}
    )
    controller = LiveController(
        sized_battery,
        make_plan(
            objective=mode,
            shadow_price=(import_value + advantage) / battery.efficiency,
        ),
    )
    result = controller.step(
        live_data(
            demand_kw=0,
            pv_kw=0,
            soc_kwh=10 * scale,
            buy=0.1,
            carbon_intensity=0.5 if mode is ObjectiveMode.EMISSIONS else 0.2,
            carbon_price=50,
        )
    )

    assert result.charge_kw == 0
    assert result.grid_import_kw == 0
    assert result.next_soc_kwh == pytest.approx(10 * scale)
    assert result.discharge_kw == 0
    assert "value-based-import-charge" not in result.reasons
    assert controller.charge_remaining_kwh == 0


@pytest.mark.parametrize("advantage", [0.0, -0.02, 0.1, 0.2])
def test_planned_grid_charging_is_rechecked_without_increasing_it(battery, advantage):
    """Retain valuable planned purchases and suppress those that lose value."""
    controller = LiveController(
        battery,
        make_plan(charge_kw=2, shadow_price=(0.1 + advantage) / battery.efficiency),
    )
    result = controller.step(live_data(demand_kw=0, pv_kw=0, buy=0.1))

    delivered = 2 if advantage >= 0 else 0
    assert result.charge_kw == pytest.approx(delivered)
    assert result.grid_import_kw == pytest.approx(delivered)
    assert controller.charge_remaining_kwh == pytest.approx(1 - delivered / 12)
    assert result.discharge_kw == 0
    assert ("grid-charge-suppressed" in result.reasons) == (advantage < 0)


def test_price_spike_suppresses_planned_purchase_and_serves_demand(battery):
    controller = LiveController(
        battery,
        make_plan(charge_kw=2, expected_net_load_kw=3, shadow_price=0.5),
    )
    result = controller.step(live_data(demand_kw=3, pv_kw=0, buy=10))

    assert result.charge_kw == 0
    assert result.discharge_kw == pytest.approx(3)
    assert result.grid_import_kw == 0
    assert result.next_soc_kwh == pytest.approx(10 - 3 / 12 / battery.efficiency)
    assert result.cycle_throughput_kwh == pytest.approx(3 / 12 / battery.efficiency)
    assert "grid-charge-suppressed" in result.reasons
    assert "value-based-discharge" in result.reasons
    assert controller.charge_remaining_kwh == pytest.approx(1)
    assert controller.discharge_remaining_kwh == 0

    # A suppressed purchase does not consume the plan; resume if value returns.
    resumed = controller.step(
        live_data(demand_kw=3, pv_kw=0, buy=0.1, soc_kwh=result.next_soc_kwh)
    )
    assert resumed.charge_kw == pytest.approx(2)
    assert resumed.discharge_kw == 0
    assert resumed.grid_import_kw == pytest.approx(5)
    assert controller.charge_remaining_kwh == pytest.approx(1 - 2 / 12)


def test_grid_charge_guard_preserves_solar_funded_part_of_plan(battery):
    controller = LiveController(
        battery,
        make_plan(charge_kw=4, expected_net_load_kw=-3, shadow_price=0),
    )
    result = controller.step(live_data(demand_kw=0, pv_kw=1, buy=10))

    assert result.charge_kw == pytest.approx(1)
    assert result.discharge_kw == 0
    assert result.grid_import_kw == result.grid_export_kw == 0
    assert result.next_soc_kwh == pytest.approx(10 + battery.efficiency / 12)
    assert controller.charge_remaining_kwh == pytest.approx(2 - 1 / 12)
    assert "grid-charge-suppressed" in result.reasons
    assert "unexpected-grid-charge-suppressed" in result.reasons


@pytest.mark.parametrize(
    "mode,shadow,buy,carbon,carbon_price",
    [
        (ObjectiveMode.COST, 0.2, 0.19, None, None),
        (ObjectiveMode.EMISSIONS, 0.0002, 100, 0.19, None),
        (ObjectiveMode.BALANCED, 0.2, 0.1, 0.9, 100),
    ],
)
@pytest.mark.parametrize("valuable", [False, True])
def test_planned_grid_charge_uses_objective_value(
    battery, mode, shadow, buy, carbon, carbon_price, valuable
):
    # Each case is at break-even; lowering the shadow value must stop buying.
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2,
            shadow_price=shadow if valuable else shadow * 0.9,
            objective=mode,
        ),
    )
    result = controller.step(
        live_data(
            demand_kw=0,
            pv_kw=0,
            buy=buy,
            carbon_intensity=carbon,
            carbon_price=carbon_price,
        )
    )

    expected_charge = 2 if valuable else 0
    assert result.charge_kw == pytest.approx(expected_charge)
    assert result.grid_import_kw == pytest.approx(expected_charge)
    assert result.discharge_kw == 0
    assert ("grid-charge-suppressed" in result.reasons) == (not valuable)


@pytest.mark.parametrize("replacement_cost,expected_charge", [(0, 2), (24000, 0)])
def test_planned_grid_charge_includes_battery_wear(
    battery, replacement_cost, expected_charge
):
    battery = battery.model_copy(update={"replacement_cost_aud": replacement_cost})
    controller = LiveController(battery, make_plan(charge_kw=2, shadow_price=0.3))
    result = controller.step(live_data(demand_kw=0, pv_kw=0, buy=0.2))

    assert result.charge_kw == pytest.approx(expected_charge)
    assert result.grid_import_kw == pytest.approx(expected_charge)
    assert result.discharge_kw == 0


@pytest.mark.parametrize("limit", ["soc", "throughput"])
def test_planned_grid_charging_respects_energy_limits(battery, limit):
    """Limit planned grid charging by stored-energy headroom and cycle allowance."""
    remaining = 0.01
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2,
            shadow_price=0.5,
            cycle_remaining_kwh=remaining if limit == "throughput" else None,
        ),
    )
    soc = battery.max_soc_kwh - remaining if limit == "soc" else 10
    result = controller.step(live_data(demand_kw=0, pv_kw=0, soc_kwh=soc, buy=0.1))

    assert result.charge_kw == pytest.approx(remaining / battery.efficiency * 12)
    assert result.cycle_throughput_kwh == pytest.approx(remaining)
    assert result.next_soc_kwh <= battery.max_soc_kwh
    assert result.discharge_kw == 0


def test_absorbs_surplus_when_storage_beats_export(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(battery, make_plan(shadow_price=0.3))

    result = controller.step(live_data(demand_kw=0.0, pv_kw=4.0, sell=0.1))

    assert result.charge_kw == pytest.approx(4.0)
    assert result.grid_export_kw == pytest.approx(0.0)


def test_suppresses_low_value_battery_export(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(discharge_kw=2.0, expected_net_load_kw=-5.0, shadow_price=0.3),
    )

    result = controller.step(live_data(demand_kw=0.0, pv_kw=5.0, sell=0.29))

    assert result.discharge_kw == pytest.approx(0.0)
    assert result.charge_kw == pytest.approx(0.0)
    assert result.grid_export_kw == pytest.approx(5.0)


def test_curtails_residual_negative_value_export(battery: BatteryConfig) -> None:
    controller = LiveController(battery, make_plan(shadow_price=0.0))

    result = controller.step(live_data(demand_kw=0.0, pv_kw=4.0, sell=-0.1))

    assert result.charge_kw == pytest.approx(0.0)
    assert result.curtailed_pv_kw == pytest.approx(4.0)
    assert result.grid_export_kw == pytest.approx(0.0)


def test_simultaneous_targets_are_time_shared(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2.0,
            discharge_kw=2.0,
            expected_net_load_kw=3.0,
            shadow_price=0.5,
        ),
    )

    result = controller.step(live_data(demand_kw=3.0, pv_kw=0.0))

    assert result.discharge_kw > 0.0
    assert result.charge_kw == pytest.approx(0.0)
    assert controller.charge_remaining_kwh == pytest.approx(1.0)
    assert controller.discharge_remaining_kwh < 1.0


def test_emissions_mode_does_not_compare_shadow_price_with_tariff(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=2.0,
            expected_net_load_kw=-3.0,
            shadow_price=0.0001,
            objective=ObjectiveMode.EMISSIONS,
        ),
    )

    result = controller.step(
        live_data(
            demand_kw=0.0,
            pv_kw=0.0,
            buy=100.0,
            sell=-100.0,
            carbon_intensity=0.095,
        )
    )

    assert result.charge_kw == pytest.approx(2.0)
    assert "unexpected-grid-charge-retained" in result.reasons


def test_emissions_mode_uses_live_carbon_for_extra_discharge(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(
            shadow_price=0.0001,
            objective=ObjectiveMode.EMISSIONS,
        ),
    )

    result = controller.step(
        live_data(demand_kw=3.0, pv_kw=0.0, buy=-10.0, carbon_intensity=0.7)
    )

    assert result.discharge_kw == pytest.approx(3.0)
    assert "value-based-discharge" in result.reasons


def test_emissions_mode_requires_live_carbon_intensity(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(objective=ObjectiveMode.EMISSIONS),
    )

    with pytest.raises(
        ValueError, match="emissions live control requires carbon intensity"
    ):
        controller.step(live_data(demand_kw=3.0, pv_kw=0.0))


def test_balanced_mode_includes_carbon_value(battery: BatteryConfig) -> None:
    controller = LiveController(
        battery,
        make_plan(
            shadow_price=0.2,
            objective=ObjectiveMode.BALANCED,
        ),
    )

    result = controller.step(
        live_data(
            demand_kw=3.0,
            pv_kw=0.0,
            buy=0.1,
            carbon_intensity=2.0,
            carbon_price=30.0,
        )
    )

    assert result.discharge_kw == pytest.approx(0.0)


def test_large_import_is_not_capacity_limited(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(battery, make_plan(shadow_price=0.3))

    result = controller.step(live_data(demand_kw=20.0, pv_kw=0.0))

    assert result.discharge_kw == pytest.approx(0.0)
    assert result.grid_import_kw == pytest.approx(20.0)


def test_rolling_cycle_limit_scales_live_command(
    battery: BatteryConfig,
) -> None:
    maximum_throughput = 2.0 * battery.max_cycles_24h * battery.capacity_kwh
    controller = LiveController(
        battery,
        make_plan(
            shadow_price=0.1,
            rolling_throughput_kwh=maximum_throughput,
        ),
    )

    result = controller.step(live_data(demand_kw=10.0, pv_kw=0.0, buy=0.5))

    assert result.discharge_kw == pytest.approx(0.0)


def test_cycle_limit_rechecks_negative_price_curtailment(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(
            charge_kw=4.0,
            expected_net_load_kw=-4.0,
            shadow_price=0.0,
            cycle_remaining_kwh=0.0,
        ),
    )

    result = controller.step(live_data(demand_kw=0.0, pv_kw=4.0, sell=-0.1))

    assert result.charge_kw == pytest.approx(0.0)
    assert result.curtailed_pv_kw == pytest.approx(4.0)
    assert result.grid_export_kw == pytest.approx(0.0)
    assert "cycle-throughput-limit" in result.reasons


def test_output_reports_next_soc_and_realised_throughput(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(discharge_kw=2.0, expected_net_load_kw=3.0, shadow_price=0.5),
    )

    result = controller.step(live_data(demand_kw=3.0, pv_kw=0.0))

    assert result.next_soc_kwh == pytest.approx(10.0 - 2.0 / 0.95 / 12.0)
    assert result.cycle_throughput_kwh == pytest.approx(2.0 / 0.95 / 12.0)
    assert "preferred-discharge" in result.reasons


def test_controller_finishes_after_six_five_minute_steps(
    battery: BatteryConfig,
) -> None:
    controller = LiveController(
        battery,
        make_plan(charge_kw=2.0, expected_net_load_kw=-3.0, shadow_price=0.0),
    )
    measurement = live_data(demand_kw=0.0, pv_kw=3.0)

    for _ in range(6):
        controller.step(measurement)

    assert controller.finished()
    with pytest.raises(RuntimeError, match="already complete"):
        controller.step(measurement)
