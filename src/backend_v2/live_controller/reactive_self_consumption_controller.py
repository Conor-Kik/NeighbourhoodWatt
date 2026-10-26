"""Stores surplus solar and supplies demand using current readings without forecasts.

The controller charges only from current aggregate PV surplus and discharges
only into current aggregate demand. Positive internal battery power means
discharge and negative power means charge.
"""

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    OPTIMISER_PERIOD_HOURS,
)
from backend_v2.live_controller.controller_utils import (
    apply_throughput_limit,
    battery_power_limits,
    negative_price_curtailment,
    next_soc_kwh,
)
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    LiveControllerOutput,
)
from database_v2.models.request_models import BatteryConfig


class ReactiveSelfConsumptionController:
    """Provide a causal, forecast-free comparison strategy."""

    NET_LOAD_DEADBAND_KW = 0.1

    def __init__(
        self,
        battery_constants: BatteryConfig,
        live_step_minutes: float = LIVE_CONTROL_STEP_MINUTES,
        control_period_minutes: float = OPTIMISER_PERIOD_HOURS * 60.0,
        cycle_throughput_remaining_kwh: float | None = None,
    ) -> None:
        """Set up battery limits, control timing and the remaining battery energy-use
        allowance.
        """
        if live_step_minutes <= 0.0:
            raise ValueError("live_step_minutes must be greater than zero")
        if control_period_minutes <= 0.0:
            raise ValueError("control_period_minutes must be greater than zero")

        self.battery = battery_constants
        self.dt_h = live_step_minutes / 60.0
        self.period_h = control_period_minutes / 60.0
        if self.dt_h > self.period_h:
            raise ValueError("live step cannot be longer than the control period")
        steps_per_period = self.period_h / self.dt_h
        if abs(steps_per_period - round(steps_per_period)) > 1e-9:
            raise ValueError(
                "live step must divide the control period into whole steps"
            )

        maximum_throughput = (
            2.0 * self.battery.max_cycles_24h * self.battery.capacity_kwh
        )
        if cycle_throughput_remaining_kwh is None:
            cycle_throughput_remaining_kwh = maximum_throughput
        if cycle_throughput_remaining_kwh < 0.0:
            raise ValueError("cycle_throughput_remaining_kwh cannot be negative")
        self.cycle_throughput_remaining_kwh = min(
            cycle_throughput_remaining_kwh,
            maximum_throughput,
        )
        self.elapsed_h = 0.0

    def finished(self) -> bool:
        """Return whether the current control period has completed."""
        return self.elapsed_h >= self.period_h - 1e-12

    def _power_limits(self, soc_kwh: float) -> tuple[float, float]:
        """Calculate allowed charging and discharging from current stored energy and
        power limits.
        """
        return battery_power_limits(self.battery, soc_kwh, self.dt_h)

    def _choose_command(
        self,
        net_load_kw: float,
        soc_kwh: float,
        reasons: list[str],
    ) -> float:
        """Charge from spare solar or discharge to meet demand within battery limits."""
        max_charge_kw, max_discharge_kw = self._power_limits(soc_kwh)
        if net_load_kw > self.NET_LOAD_DEADBAND_KW:
            reasons.append("reactive-deficit-discharge")
            return min(net_load_kw, max_discharge_kw)
        if net_load_kw < -self.NET_LOAD_DEADBAND_KW:
            reasons.append("reactive-surplus-charge")
            return -min(-net_load_kw, max_charge_kw)
        return 0.0

    def _build_output(
        self,
        live_data: LiveControllerInput,
        net_load_kw: float,
        command_kw: float,
        curtailment_kw: float,
        delivered_throughput_kwh: float,
        reasons: list[str],
    ) -> LiveControllerOutput:
        """Package battery actions, grid flows and updated stored energy into the
        result.
        """
        if not reasons:
            reasons.append("idle")
        charge_kw = max(-command_kw, 0.0)
        discharge_kw = max(command_kw, 0.0)
        grid_exchange_kw = net_load_kw - command_kw + curtailment_kw
        return LiveControllerOutput(
            charge_kw=charge_kw,
            discharge_kw=discharge_kw,
            grid_import_kw=max(grid_exchange_kw, 0.0),
            grid_export_kw=max(-grid_exchange_kw, 0.0),
            curtailed_pv_kw=curtailment_kw,
            next_soc_kwh=next_soc_kwh(
                self.battery,
                live_data.soc_kwh,
                command_kw,
                self.dt_h,
            ),
            cycle_throughput_kwh=delivered_throughput_kwh,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def step(self, live_data: LiveControllerInput) -> LiveControllerOutput:
        """Choose one action from current readings and update the remaining battery-use
        allowance.
        """
        if self.finished():
            raise RuntimeError("the current control period is already complete")

        net_load_kw = live_data.demand_kw - live_data.pv_kw
        reasons: list[str] = []
        command_kw = self._choose_command(net_load_kw, live_data.soc_kwh, reasons)
        command_kw, delivered_throughput, self.cycle_throughput_remaining_kwh = (
            apply_throughput_limit(
                self.battery,
                command_kw,
                self.dt_h,
                self.cycle_throughput_remaining_kwh,
                reasons,
            )
        )
        curtailment_kw = negative_price_curtailment(
            net_load_kw,
            command_kw,
            live_data,
            reasons,
            self.NET_LOAD_DEADBAND_KW,
        )
        self.elapsed_h += self.dt_h
        return self._build_output(
            live_data,
            net_load_kw,
            command_kw,
            curtailment_kw,
            delivered_throughput,
            reasons,
        )
