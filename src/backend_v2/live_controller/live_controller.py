"""Adjusts the optimiser plan to current community conditions every five minutes.

Positive battery power means discharge and negative battery power means charge.
The public output exposes those directions as separate non-negative fields.
"""

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    OPTIMISER_PERIOD_HOURS,
)
from backend_v2.live_controller.controller_utils import (
    POWER_TOLERANCE,
    apply_throughput_limit,
    battery_power_limits,
    negative_price_curtailment,
    next_soc_kwh,
)
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    LiveControllerOutput,
    OptimiserOutput,
)
from database_v2.models.request_models import BatteryConfig, ObjectiveMode


class LiveController:
    """Step through one optimiser period using aggregate community measurements."""

    NET_LOAD_DEADBAND_KW = 0.1
    # These margins are explicit policy knobs. Zero means act as soon as the
    # compared values break even; raising them would make live overrides rarer.
    STORAGE_VALUE_MARGIN_PER_KWH = 0.0
    DISCHARGE_VALUE_MARGIN_PER_KWH = 0.0

    def __init__(
        self,
        battery_constants: BatteryConfig,
        optimiser_output: OptimiserOutput,
        live_step_minutes: float = LIVE_CONTROL_STEP_MINUTES,
    ) -> None:
        """Set up the plan, control timing and remaining energy allowances."""
        if live_step_minutes <= 0.0:
            raise ValueError("live_step_minutes must be greater than zero")

        self.battery = battery_constants
        self.plan = optimiser_output
        self.dt_h = live_step_minutes / 60.0
        self.period_h = OPTIMISER_PERIOD_HOURS
        if self.dt_h > self.period_h:
            raise ValueError("live step cannot be longer than the optimiser period")
        steps_per_period = self.period_h / self.dt_h
        if abs(steps_per_period - round(steps_per_period)) > 1e-9:
            raise ValueError(
                "live step must divide the optimiser period into whole steps"
            )

        # Track the portion of the half-hour MPC schedule that has not yet been
        # delivered. These are energy budgets, not fixed power commands, so a
        # later live step can catch up when an earlier step is constrained.
        self.elapsed_h = 0.0
        self.charge_remaining_kwh = self.plan.charge_kw * self.period_h
        self.discharge_remaining_kwh = self.plan.discharge_kw * self.period_h

        # Expected net load is only used to distinguish planned PV-funded
        # charging from deliberate grid charging. Older optimiser outputs can
        # reconstruct it from their solved first-period power balance.
        if self.plan.expected_net_load_kw is not None:
            self.expected_net_load_kw = self.plan.expected_net_load_kw
        else:
            # Recover expected pre-battery net load from the solved power balance.
            self.expected_net_load_kw = (
                self.plan.discharge_kw
                + self.plan.grid_import_kw
                - self.plan.charge_kw
                - self.plan.grid_export_kw
                - self.plan.curtailed_pv_kw
            )

        # Prefer the explicit allowance calculated from realised history by the
        # runner. The fallback keeps manually constructed/older outputs usable.
        if self.plan.cycle_throughput_remaining_kwh is not None:
            self.cycle_throughput_remaining_kwh = (
                self.plan.cycle_throughput_remaining_kwh
            )
        else:
            planned_throughput = (
                self.battery.efficiency * self.plan.charge_kw
                + self.plan.discharge_kw / self.battery.efficiency
            ) * self.period_h
            previous_throughput = max(
                self.plan.rolling_throughput_kwh - planned_throughput,
                0.0,
            )
            maximum_throughput = (
                2.0 * self.battery.max_cycles_24h * self.battery.capacity_kwh
            )
            self.cycle_throughput_remaining_kwh = max(
                maximum_throughput - previous_throughput,
                0.0,
            )

    def finished(self) -> bool:
        """Return whether the current control period has completed."""
        return self.elapsed_h >= self.period_h - 1e-12

    @property
    def time_remaining_h(self) -> float:
        """Return remaining control time in hours, with a minimum of one live step."""
        return max(self.period_h - self.elapsed_h, self.dt_h)

    def _power_limits(self, soc_kwh: float) -> tuple[float, float]:
        """Calculate charge and discharge limits from current stored energy."""
        return battery_power_limits(self.battery, soc_kwh, self.dt_h)

    def _preferred_action(
        self,
        net_load_kw: float,
        max_charge_kw: float,
        max_discharge_kw: float,
    ) -> tuple[float, float]:
        """Choose a direction from the remaining plan and current net load."""
        # A half-hour optimiser target is an energy budget. Dividing what remains
        # by this five-minute step gives the power needed to catch up immediately.
        charge_kw = min(max_charge_kw, self.charge_remaining_kwh / self.dt_h)
        discharge_kw = min(
            max_discharge_kw,
            self.discharge_remaining_kwh / self.dt_h,
        )

        conflict = self.plan.charge_kw > 1e-12 and self.plan.discharge_kw > 1e-12
        if not conflict:
            # For a one-direction plan, do not exceed its original power target.
            # Catch-up above that target is only useful for a time-shared plan.
            charge_kw = min(charge_kw, self.plan.charge_kw)
            discharge_kw = min(discharge_kw, self.plan.discharge_kw)

        if charge_kw <= 1e-12:
            return 0.0, discharge_kw
        if discharge_kw <= 1e-12:
            return charge_kw, 0.0
        if net_load_kw < -self.NET_LOAD_DEADBAND_KW:
            return charge_kw, 0.0
        if net_load_kw > self.NET_LOAD_DEADBAND_KW:
            return 0.0, discharge_kw

        # Near balance there is no physical surplus/deficit to choose for us, so
        # prioritise whichever energy budget is least achievable in the time left.
        charge_urgency = self.charge_remaining_kwh / self.time_remaining_h
        discharge_urgency = self.discharge_remaining_kwh / self.time_remaining_h
        return (
            (charge_kw, 0.0)
            if charge_urgency >= discharge_urgency
            else (
                0.0,
                discharge_kw,
            )
        )

    def _objective_values(
        self,
        live_data: LiveControllerInput,
    ) -> tuple[float, float, float, float]:
        """Compare live values in units matching the selected objective."""
        mode = self.plan.objective_mode

        # All four returned values use the active objective's units per kWh. This
        # lets the dispatch rules below stay identical across objective modes.
        if mode is ObjectiveMode.EMISSIONS:
            if live_data.carbon_intensity_t_per_mwh is None:
                raise ValueError("emissions live control requires carbon intensity")
            live_import = live_data.carbon_intensity_t_per_mwh / 1000.0
            export_value = 0.0
            degradation = 0.0
        elif mode is ObjectiveMode.BALANCED:
            if live_data.carbon_intensity_t_per_mwh is None:
                raise ValueError("balanced live control requires carbon intensity")
            elif live_data.carbon_price_aud_per_t is None:
                raise ValueError("balanced live control requires carbon price")
            carbon_price = live_data.carbon_price_aud_per_t
            live_import = (
                live_data.buy_tariff_aud_per_kwh
                + carbon_price * live_data.carbon_intensity_t_per_mwh / 1000.0
            )
            export_value = live_data.sell_tariff_aud_per_kwh
            degradation = self.battery.degradation_aud_per_kwh
        elif mode is ObjectiveMode.COST:
            live_import = live_data.buy_tariff_aud_per_kwh
            export_value = live_data.sell_tariff_aud_per_kwh
            degradation = self.battery.degradation_aud_per_kwh
        else:
            raise ValueError(f"unsupported optimiser objective: {mode}")

        # The SOC shadow price is the horizon value of one cell-side kWh. Apply
        # efficiency and degradation to compare it with grid-side transactions.
        storage_value = self.battery.efficiency * (self.plan.shadow_price - degradation)
        discharge_cost = (
            self.plan.shadow_price + degradation
        ) / self.battery.efficiency
        return (
            live_import,
            export_value,
            storage_value,
            discharge_cost,
        )

    def _evaluate_preferred_charge(
        self,
        requested_charge_kw: float,
        net_load_kw: float,
        import_value: float,
        storage_value: float,
        reasons: list[str],
    ) -> float:
        """Keep PV-funded charging and recheck all grid purchases at live value."""
        if requested_charge_kw <= POWER_TOLERANCE:
            return 0.0

        # Split planned charging into three sources:
        #   1. charge still covered by realised PV surplus;
        #   2. charge newly forced onto the grid by a forecast miss; and
        #   3. grid charge that the MPC deliberately planned.
        # Both grid-funded portions must still be worthwhile at the live import
        # value. A planned cheap purchase can become expensive within the period.
        expected_surplus_kw = max(-self.expected_net_load_kw, 0.0)
        realised_surplus_kw = max(-net_load_kw, 0.0)
        surplus_funded_kw = min(requested_charge_kw, realised_surplus_kw)
        grid_funded_kw = requested_charge_kw - surplus_funded_kw
        newly_grid_funded_kw = min(
            grid_funded_kw,
            max(
                min(requested_charge_kw, expected_surplus_kw) - surplus_funded_kw,
                0.0,
            ),
        )
        grid_charge_has_value = storage_value + POWER_TOLERANCE >= (
            import_value + self.STORAGE_VALUE_MARGIN_PER_KWH
        )

        preferred_charge_kw = surplus_funded_kw
        if grid_charge_has_value:
            preferred_charge_kw += grid_funded_kw
            if newly_grid_funded_kw > POWER_TOLERANCE:
                reasons.append("unexpected-grid-charge-retained")
        elif grid_funded_kw > POWER_TOLERANCE:
            reasons.append("grid-charge-suppressed")
            if newly_grid_funded_kw > POWER_TOLERANCE:
                reasons.append("unexpected-grid-charge-suppressed")

        if preferred_charge_kw > POWER_TOLERANCE:
            reasons.append("preferred-charge")
        return preferred_charge_kw

    def _evaluate_preferred_discharge(
        self,
        requested_discharge_kw: float,
        net_load_kw: float,
        preferred_charge_kw: float,
        export_value: float,
        discharge_cost: float,
        reasons: list[str],
    ) -> float:
        """Keep planned discharge for demand and worthwhile exports."""
        if requested_discharge_kw <= POWER_TOLERANCE:
            return 0.0

        # Serving current demand avoids an import and is always retained from the
        # MPC plan. Any remainder would become an export, so it must cover the
        # opportunity cost of removing energy from the battery.
        demand_serving_kw = min(
            requested_discharge_kw,
            max(net_load_kw + preferred_charge_kw, 0.0),
        )
        export_discharge_kw = requested_discharge_kw - demand_serving_kw
        preferred_discharge_kw = demand_serving_kw
        if export_value + POWER_TOLERANCE >= (
            discharge_cost + self.DISCHARGE_VALUE_MARGIN_PER_KWH
        ):
            preferred_discharge_kw += export_discharge_kw
        elif export_discharge_kw > POWER_TOLERANCE:
            reasons.append("export-discharge-suppressed")

        if preferred_discharge_kw > POWER_TOLERANCE:
            reasons.append("preferred-discharge")
        return preferred_discharge_kw

    def _apply_value_adjustments(
        self,
        command_kw: float,
        net_load_kw: float,
        max_charge_kw: float,
        max_discharge_kw: float,
        import_value: float,
        export_value: float,
        storage_value: float,
        discharge_cost: float,
        reasons: list[str],
    ) -> float:
        """Adjust battery power using electricity and stored-energy values."""
        # Signed command convention throughout this class:
        #   positive = discharge, negative = charge.
        # Net load minus command is therefore positive import / negative export.
        grid_exchange_kw = net_load_kw - command_kw

        # Discharge beyond the preferred MPC slice when avoiding the current
        # import is worth more than retaining the stored energy. Never reverse an
        # existing charge command within the same live step.
        if (
            command_kw >= -POWER_TOLERANCE
            and grid_exchange_kw > self.NET_LOAD_DEADBAND_KW
            and import_value
            > discharge_cost + self.DISCHARGE_VALUE_MARGIN_PER_KWH + POWER_TOLERANCE
        ):
            extra_kw = min(
                grid_exchange_kw,
                max(max_discharge_kw - max(command_kw, 0.0), 0.0),
            )
            command_kw += extra_kw
            if extra_kw > POWER_TOLERANCE:
                reasons.append("value-based-discharge")

        # Additional live charging only absorbs PV; grid charging follows the MPC plan.
        grid_exchange_kw = net_load_kw - command_kw
        residual_export_kw = max(-grid_exchange_kw, 0.0)
        # Finally, prefer storing otherwise-exported PV when its stored value is
        # greater than the non-negative value of exporting it now.
        if (
            command_kw <= POWER_TOLERANCE
            and residual_export_kw > self.NET_LOAD_DEADBAND_KW
            and storage_value
            > max(export_value, 0.0) + self.STORAGE_VALUE_MARGIN_PER_KWH
        ):
            extra_kw = min(
                residual_export_kw,
                max(max_charge_kw - max(-command_kw, 0.0), 0.0),
            )
            command_kw -= extra_kw
            if extra_kw > POWER_TOLERANCE:
                reasons.append("surplus-absorption")
        return command_kw

    def _record_preferred_delivery(
        self,
        command_kw: float,
        preferred_charge_kw: float,
        preferred_discharge_kw: float,
    ) -> None:
        """Subtract delivered planned energy from the remaining budgets."""
        # Value-based additions are live deviations, not delivery of the MPC
        # target. Capping by the preferred amount keeps those additions from
        # incorrectly exhausting the remaining half-hour schedule.
        delivered_charge_kw = min(preferred_charge_kw, max(-command_kw, 0.0))
        delivered_discharge_kw = min(
            preferred_discharge_kw,
            max(command_kw, 0.0),
        )
        self.charge_remaining_kwh = max(
            self.charge_remaining_kwh - delivered_charge_kw * self.dt_h,
            0.0,
        )
        self.discharge_remaining_kwh = max(
            self.discharge_remaining_kwh - delivered_discharge_kw * self.dt_h,
            0.0,
        )

    def _build_output(
        self,
        live_data: LiveControllerInput,
        net_load_kw: float,
        command_kw: float,
        curtailment_kw: float,
        delivered_throughput_kwh: float,
        reasons: list[str],
    ) -> LiveControllerOutput:
        """Package battery actions, grid flows and updated stored energy."""
        if not reasons:
            reasons.append("idle")
        charge_kw = max(-command_kw, 0.0)
        discharge_kw = max(command_kw, 0.0)
        # Curtailment removes generation, so it increases net grid exchange.
        grid_exchange_kw = net_load_kw - command_kw + curtailment_kw
        return LiveControllerOutput(
            optimiser_charge_kw=self.plan.charge_kw,
            optimiser_discharge_kw=self.plan.discharge_kw,
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
        """Choose a live action and solar reduction, then update progress."""
        if self.finished():
            raise RuntimeError("the current optimiser period is already complete")

        # Stage 1: establish the realised community condition and a physically
        # feasible slice of the remaining MPC battery schedule.
        net_load_kw = live_data.demand_kw - live_data.pv_kw
        max_charge_kw, max_discharge_kw = self._power_limits(live_data.soc_kwh)
        preferred_charge_request, preferred_discharge_request = self._preferred_action(
            net_load_kw, max_charge_kw, max_discharge_kw
        )
        (
            import_value,
            export_value,
            storage_value,
            discharge_cost,
        ) = self._objective_values(live_data)
        reasons: list[str] = []

        # First preserve the parts of the MPC schedule justified by the realised
        # conditions, then allow current marginal values to adjust that command.
        preferred_charge_kw = self._evaluate_preferred_charge(
            preferred_charge_request,
            net_load_kw,
            import_value,
            storage_value,
            reasons,
        )
        preferred_discharge_kw = self._evaluate_preferred_discharge(
            preferred_discharge_request,
            net_load_kw,
            preferred_charge_kw,
            export_value,
            discharge_cost,
            reasons,
        )
        command_kw = preferred_discharge_kw - preferred_charge_kw
        command_kw = self._apply_value_adjustments(
            command_kw,
            net_load_kw,
            max_charge_kw,
            max_discharge_kw,
            import_value,
            export_value,
            storage_value,
            discharge_cost,
            reasons,
        )

        # Physical throughput has final authority over the battery command.
        # Curtailment is intentionally calculated afterward from that final power.
        command_kw, delivered_throughput_kwh, self.cycle_throughput_remaining_kwh = (
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

        # Persist realised delivery so the next five-minute slice knows both the
        # remaining MPC energy budgets and remaining rolling-cycle allowance.
        self._record_preferred_delivery(
            command_kw,
            preferred_charge_kw,
            preferred_discharge_kw,
        )
        self.elapsed_h += self.dt_h
        return self._build_output(
            live_data,
            net_load_kw,
            command_kw,
            curtailment_kw,
            delivered_throughput_kwh,
            reasons,
        )
