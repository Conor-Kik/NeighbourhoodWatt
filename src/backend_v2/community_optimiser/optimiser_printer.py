"""Prints simulation steps and comparison summaries in the command line."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    PERIODS_PER_DAY,
)
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.location_dao import LocationDao
from database_v2.data_access_objects.results_dao import ResultsDao
from database_v2.models.optimiser_models import (
    LiveControllerOutput,
    LiveStep,
    OptimiserInput,
    OptimiserOutput,
    SimulationStep,
)
from database_v2.models.request_models import (
    BatteryConfig,
    Location,
    ObjectiveMode,
    SimulationRequest,
)


class SimulationPrinter:
    """Print completed steps and a summary for one simulation request."""

    battery: BatteryConfig
    location: Location

    def __init__(
        self,
        request: SimulationRequest,
        db_path: str | Path,
        stream: TextIO | None = None,
    ) -> None:
        """Load battery and location settings and choose where simulation reports are
        printed.
        """
        self.request = request
        self.db_path = db_path
        battery = BatteryDao(db_path).get_battery_config(request.battery_config_id)
        if battery is None:
            raise ValueError(f"Battery config {request.battery_config_id} not found")
        self.battery = battery

        location = LocationDao(db_path).get_location(request.location_name)
        if location is None:
            raise ValueError(f"Location {request.location_name} not found")
        self.location = location

        self.stream = stream if stream is not None else sys.stdout
        self.timezone = ZoneInfo(self.location.timezone)

    def _stored_summaries(self) -> dict[str, dict[str, Any]]:
        """Read completed metrics by scenario, always closing the connection."""
        dao = ResultsDao(self.db_path, self.request)
        try:
            return {row["scenario_name"]: row for row in dao.get_summaries()}
        finally:
            dao.close()

    def _local_time(self, timestamp_utc: datetime) -> datetime:
        """Convert a UTC model timestamp to the requested location's local time."""
        if timestamp_utc.tzinfo is None:
            timestamp_utc = timestamp_utc.replace(tzinfo=UTC)
        return timestamp_utc.astimezone(self.timezone)

    @staticmethod
    def _number(value: float, places: int) -> str:
        """Avoid displaying tiny negative values as negative zero."""
        rendered = f"{value:.{places}f}"
        return (
            rendered[1:]
            if rendered.startswith("-") and float(rendered) == 0
            else rendered
        )

    def _soc_text(self, soc_kwh: float) -> str:
        """Show stored energy and its share of nominal battery capacity."""
        capacity_kwh = self.battery.capacity_kwh
        if capacity_kwh <= 0:
            return f"{self._number(soc_kwh, 3)} kWh (n/a)"
        return f"{self._number(soc_kwh, 3)} kWh ({soc_kwh / capacity_kwh:.1%})"

    @staticmethod
    def _realised_metrics(step: SimulationStep) -> tuple[float, float, float]:
        """Grid cost, battery wear, and emissions for this 30-minute block."""
        dt = LIVE_CONTROL_STEP_MINUTES / 60.0
        grid_cost = sum(
            (
                live.output.grid_import_kw * live.data.buy_tariff_aud_per_kwh
                - live.output.grid_export_kw * live.data.sell_tariff_aud_per_kwh
            )
            * dt
            for live in step.live_steps
        )
        degradation = 0.0
        if step.forecast.battery_constants.capacity_kwh > 0:
            degradation = sum(
                live.output.cycle_throughput_kwh
                * step.forecast.battery_constants.degradation_aud_per_kwh
                for live in step.live_steps
            )
        emissions = sum(
            live.output.grid_import_kw
            * (live.data.carbon_intensity_t_per_mwh or 0.0)
            * dt
            / 1000.0
            for live in step.live_steps
        )
        return grid_cost, degradation, emissions

    def print_step(self, step: SimulationStep) -> None:
        """Print one 30-minute solve and its realised five-minute dispatches."""
        start = self._local_time(step.timestamp_utc)
        end = self._local_time(step.timestamp_utc + timedelta(minutes=30))
        grid_cost, degradation, emissions = self._realised_metrics(step)
        mode = step.plan.objective_mode
        if mode is ObjectiveMode.EMISSIONS:
            realised_value, unit = emissions, "tCO2-e"
        elif mode is ObjectiveMode.BALANCED:
            realised_value = (
                grid_cost
                + degradation
                + step.forecast.carbon_price_aud_per_t * emissions
            )
            unit = "AUD"
        else:
            realised_value, unit = grid_cost + degradation, "AUD"
        live_demand_kw = (
            sum(live.data.demand_kw for live in step.live_steps) / len(step.live_steps)
            if step.live_steps
            else None
        )
        live_demand_text = (
            f"; live demand avg={self._number(live_demand_kw, 3)} kW"
            if live_demand_kw is not None
            else ""
        )
        print(
            f"\n[{start:%Y-%m-%d %H:%M %Z} - {end:%Y-%m-%d %H:%M %Z}]",
            file=self.stream,
        )
        print(
            f"  30m realised {mode.value}: "
            f"{self._number(realised_value, 4)} {unit}; "
            f"shadow price={self._number(step.plan.shadow_price, 4)}; "
            f"forecast demand="
            f"{self._number(step.forecast.demand_aggregate_kw[0], 3)} kW, "
            f"PV={self._number(step.forecast.pv_aggregate_kw[0], 3)} kW"
            f"{live_demand_text}",
            file=self.stream,
        )
        print(
            f"  Plan: charge={self._number(step.plan.charge_kw, 3)}, "
            f"discharge={self._number(step.plan.discharge_kw, 3)}, "
            f"import={self._number(step.plan.grid_import_kw, 3)}, "
            f"export={self._number(step.plan.grid_export_kw, 3)} kW; "
            f"SOC={self._soc_text(step.plan.soc_kwh)}",
            file=self.stream,
        )
        for live in step.live_steps:
            local_time = self._local_time(live.timestamp_utc)
            print(
                f"    {local_time:%H:%M %Z} D={self._number(live.data.demand_kw, 3)} "
                f"PV={self._number(live.data.pv_kw, 3)} | "
                f"chg={self._number(live.output.charge_kw, 3)} "
                f"dis={self._number(live.output.discharge_kw, 3)} | "
                f"import={self._number(live.output.grid_import_kw, 3)} "
                f"export={self._number(live.output.grid_export_kw, 3)} | "
                f"buy={self._number(live.data.buy_tariff_aud_per_kwh, 4)} "
                f"sell={self._number(live.data.sell_tariff_aud_per_kwh, 4)} "
                f"AUD/kWh | "
                f"SOC={self._soc_text(live.output.next_soc_kwh)} | "
                f"{', '.join(live.output.reasons)}",
                file=self.stream,
            )
        self.stream.flush()

    def print_summary(
        self,
        steps: list[SimulationStep],
        *,
        title: str = "V2 REALISED SIMULATION SUMMARY",
        solve_count: int | None = None,
        scenario_name: str = "optimised_battery",
    ) -> None:
        """Report actual operation and estimates adjusted for energy remaining at run
        end.
        """
        live = [sample for step in steps for sample in step.live_steps]
        dt = LIVE_CONTROL_STEP_MINUTES / 60.0

        def energy(field: str) -> float:
            """Convert a power field from all live steps into total energy in kWh."""
            return sum(getattr(sample.output, field) * dt for sample in live)

        demand = sum(sample.data.demand_kw * dt for sample in live)
        pv = sum(sample.data.pv_kw * dt for sample in live)
        step_metrics = [self._realised_metrics(step) for step in steps]
        grid_cost = sum(values[0] for values in step_metrics)
        degradation = sum(values[1] for values in step_metrics)
        emissions = sum(values[2] for values in step_metrics)
        emissions_cost = sum(
            step.forecast.carbon_price_aud_per_t * values[2]
            for step, values in zip(steps, step_metrics, strict=True)
        )
        economic_cost = grid_cost + degradation
        cost_value = emissions_value = 0.0
        adjusted = False
        if steps and scenario_name == "optimised_battery":
            rolling = self._stored_summaries().get("optimised_battery")
            if (
                rolling is not None
                and rolling["estimated_terminal_cost_value_aud"] is not None
                and rolling["estimated_terminal_emissions_value_tonnes"] is not None
            ):
                cost_value = rolling["estimated_terminal_cost_value_aud"]
                emissions_value = rolling["estimated_terminal_emissions_value_tonnes"]
                adjusted = True
        comparison_cost = economic_cost - cost_value
        comparison_emissions = emissions - emissions_value
        estimate_label = " (estimated after terminal credit)" if adjusted else ""
        print("\n" + "=" * 64, file=self.stream)
        print(title, file=self.stream)
        print("=" * 64, file=self.stream)
        start = self._local_time(self.request.start_time_utc)
        end_utc = (
            steps[-1].timestamp_utc + timedelta(minutes=30)
            if steps
            else self.request.start_time_utc
        )
        end = self._local_time(end_utc)
        print(f"Location: {self.request.location_name}", file=self.stream)
        print(f"Objective: {self.request.objective_mode.value}", file=self.stream)
        print(
            f"Local period: {start:%Y-%m-%d %H:%M %Z} to {end:%Y-%m-%d %H:%M %Z}",
            file=self.stream,
        )
        print(
            f"Optimiser solves: {solve_count}; live intervals: {len(live)}"
            if solve_count is not None
            else f"MPC solves: {len(steps)}; live intervals: {len(live)}",
            file=self.stream,
        )
        print(
            f"Demand: {self._number(demand, 3)} kWh; PV: {self._number(pv, 3)} kWh",
            file=self.stream,
        )
        print(
            f"Grid import: {self._number(energy('grid_import_kw'), 3)} kWh; "
            f"export: {self._number(energy('grid_export_kw'), 3)} kWh",
            file=self.stream,
        )
        print(
            f"Battery charge: {self._number(energy('charge_kw'), 3)} kWh; "
            f"discharge: {self._number(energy('discharge_kw'), 3)} kWh; "
            f"curtailed PV: {self._number(energy('curtailed_pv_kw'), 3)} kWh",
            file=self.stream,
        )
        if adjusted:
            print(
                "Estimated terminal SOC marginal value (next 24-hour forecast): "
                f"Cost=${self._number(cost_value, 4)}; "
                f"Emissions={self._number(emissions_value, 6)} tCO2-e",
                file=self.stream,
            )
        print(
            f"Grid cost: ${self._number(grid_cost, 4)}; "
            f"degradation: ${self._number(degradation, 4)}; "
            f"economic cost: ${self._number(comparison_cost, 4)}{estimate_label}",
            file=self.stream,
        )
        if steps:
            average_daily_cost = PERIODS_PER_DAY * comparison_cost / len(steps)
            print(
                f"Average daily economic cost: "
                f"${self._number(average_daily_cost, 4)}/day{estimate_label}",
                file=self.stream,
            )
        else:
            print(
                "Average daily economic cost: n/a (no completed solves)",
                file=self.stream,
            )
        print(
            f"Grid import emissions: {self._number(comparison_emissions, 6)} "
            f"tCO2-e{estimate_label}",
            file=self.stream,
        )
        if adjusted:
            print(
                "Realised totals before terminal credit: "
                f"economic cost=${self._number(economic_cost, 4)}; "
                f"grid import emissions={self._number(emissions, 6)} tCO2-e",
                file=self.stream,
            )
        print(
            "Theoretical emissions cost (Balanced carbon price): "
            f"${self._number(emissions_cost, 4)}",
            file=self.stream,
        )
        throughput = sum(sample.output.cycle_throughput_kwh for sample in live)
        print(
            f"Battery cell throughput: {self._number(throughput, 3)} kWh",
            file=self.stream,
        )
        if live:
            final_soc = self._soc_text(live[-1].output.next_soc_kwh)
            print(f"Final SOC: {final_soc}", file=self.stream)

    def print_perfect_foresight_summary(
        self,
        steps: list[SimulationStep],
        data: OptimiserInput,
        decisions: list[OptimiserOutput],
    ) -> None:
        """Compare the full-horizon five-minute optimum with rolling dispatch."""
        if len(decisions) != sum(len(step.live_steps) for step in steps):
            raise ValueError("perfect foresight must include every realised decision")
        perfect_steps = []
        offset = 0
        for step in steps:
            live_steps = []
            for sample in step.live_steps:
                decision = decisions[offset]
                if decision.next_soc_kwh is None:
                    raise ValueError("full-horizon decisions must include next SOC")
                throughput = (
                    data.battery_constants.efficiency * decision.charge_kw
                    + decision.discharge_kw / data.battery_constants.efficiency
                ) * data.period_hours
                live_steps.append(
                    LiveStep(
                        timestamp_utc=sample.timestamp_utc,
                        data=sample.data.model_copy(
                            update={"soc_kwh": decision.soc_kwh}
                        ),
                        output=LiveControllerOutput(
                            charge_kw=decision.charge_kw,
                            discharge_kw=decision.discharge_kw,
                            grid_import_kw=decision.grid_import_kw,
                            grid_export_kw=decision.grid_export_kw,
                            curtailed_pv_kw=decision.curtailed_pv_kw,
                            next_soc_kwh=decision.next_soc_kwh,
                            cycle_throughput_kwh=throughput,
                            reasons=(),
                        ),
                    )
                )
                offset += 1
            perfect_steps.append(
                step.model_copy(update={"live_steps": tuple(live_steps)})
            )
        self.print_summary(
            perfect_steps,
            title="PERFECT FORESIGHT SUMMARY (REALISED 5-MINUTE DATA)",
            solve_count=1,
            scenario_name="perfect_foresight",
        )
        metrics = [self._realised_metrics(step) for step in steps]
        rolling_economic = sum(grid + wear for grid, wear, _ in metrics)
        rolling_emissions = sum(emissions for _, _, emissions in metrics)
        rolling_emissions_cost = sum(
            step.forecast.carbon_price_aud_per_t * values[2]
            for step, values in zip(steps, metrics, strict=True)
        )
        if data.objective_mode is ObjectiveMode.EMISSIONS:
            rolling_value = rolling_emissions
            unit = "tCO2-e"
        elif data.objective_mode is ObjectiveMode.BALANCED:
            rolling_value = rolling_economic + rolling_emissions_cost
            unit = "AUD"
        else:
            rolling_value = rolling_economic
            unit = "AUD"

        perfect_value = decisions[0].objective
        print(
            f"Realised objective comparison: rolling={self._number(rolling_value, 4)}, "
            f"perfect={self._number(perfect_value, 4)}, "
            f"gap={self._number(rolling_value - perfect_value, 4)} {unit}",
            file=self.stream,
        )

    def print_stored_summaries(self) -> None:
        """Print the final table from stored totals for every evaluated method."""
        summaries = self._stored_summaries()
        print(
            f"\nFINAL RESULTS (Objective: {self.request.objective_mode.value})",
            file=self.stream,
        )
        print(
            f"{'Method':<28} | {'Total cost (AUD)':>17} | "
            f"{'Total emissions (tCO2-e)':>25} | {'Balanced cost (AUD)':>20}",
            file=self.stream,
        )
        print("-" * 99, file=self.stream)
        for scenario, label in (
            ("optimised_battery", "Smart rolling*"),
            ("reactive_battery", "Greedy self consumption"),
            ("perfect_foresight", "Perfect foresight"),
            ("no_battery_shared", "No battery (shared)"),
            ("no_battery_individual", "No battery (individual)"),
        ):
            if scenario not in summaries:
                continue
            summary = summaries[scenario]
            cost = "$" + self._number(summary["comparison_economic_cost_aud"], 4)
            emissions = self._number(summary["comparison_emissions_tonnes"], 6)
            balanced = "$" + self._number(
                summary["realised_economic_cost_aud"] + summary["emissions_cost_aud"],
                4,
            )
            print(
                f"{label:<28} | {cost:>17} | {emissions:>25} | {balanced:>20}",
                file=self.stream,
            )
        print(
            "* Rolling cost/emissions include estimated terminal SOC credits. "
            "Total cost includes battery degradation.",
            file=self.stream,
        )
        print(
            "Balanced cost = realised economic cost "
            "+ carbon-priced realised emissions.",
            file=self.stream,
        )

    def print_reactive_summary(self) -> None:
        """Compare smart dispatch with the stored forecast-free controller."""
        summaries = self._stored_summaries()
        print("\nREACTIVE SELF-CONSUMPTION COMPARISON", file=self.stream)
        print(
            "Economic cost and emissions use rolling's estimated terminal credits. "
            "Balanced costs use realised totals.",
            file=self.stream,
        )
        values = {}
        for scenario, label in (
            ("optimised_battery", "Smart rolling"),
            ("reactive_battery", "Reactive self consumption"),
        ):
            result = summaries[scenario]
            economic = result["comparison_economic_cost_aud"]
            emissions = result["comparison_emissions_tonnes"]
            balanced = (
                result["realised_economic_cost_aud"] + result["emissions_cost_aud"]
            )
            values[scenario] = {
                ObjectiveMode.COST: economic,
                ObjectiveMode.EMISSIONS: emissions,
                ObjectiveMode.BALANCED: balanced,
            }[self.request.objective_mode]
            print(
                f"{label}: economic cost=${self._number(economic, 4)}; "
                f"emissions={self._number(emissions, 6)} tCO2-e; "
                f"Balanced cost=${self._number(balanced, 4)}",
                file=self.stream,
            )
        improvement = values["reactive_battery"] - values["optimised_battery"]
        unit = (
            "tCO2-e"
            if self.request.objective_mode is ObjectiveMode.EMISSIONS
            else "AUD"
        )
        qualifier = (
            "Realised"
            if self.request.objective_mode is ObjectiveMode.BALANCED
            else "Estimated"
        )
        print(
            f"{qualifier} Smart controller improvement "
            f"({self.request.objective_mode.value}): "
            f"{self._number(improvement, 6 if unit == 'tCO2-e' else 4)} {unit}",
            file=self.stream,
        )

    def print_terminal_value_summary(self) -> None:
        """Show forecast credits without changing the measured simulation totals."""
        result = self._stored_summaries().get("optimised_battery")
        if result is None:
            return
        cost_value = result["estimated_terminal_cost_value_aud"]
        emissions_value = result["estimated_terminal_emissions_value_tonnes"]
        if cost_value is None or emissions_value is None:
            return
        print("\nROLLING TERMINAL SOC VALUE ESTIMATES", file=self.stream)
        print(
            "Next 24-hour forecast; separate Cost and Emissions solves. "
            "Credit-adjusted figures are estimates, not realised savings.",
            file=self.stream,
        )
        print(
            "optimised_battery: terminal SOC="
            f"{self._soc_text(result['terminal_soc_kwh'])}; "
            f"estimated Cost value=${self._number(cost_value, 4)}; "
            f"estimated Emissions value={self._number(emissions_value, 6)} tCO2-e",
            file=self.stream,
        )
        cost = result["comparison_economic_cost_aud"]
        emissions = result["comparison_emissions_tonnes"]
        print(
            f"  Estimated credit-adjusted economic cost=${self._number(cost, 4)}; "
            f"emissions={self._number(emissions, 6)} tCO2-e",
            file=self.stream,
        )

    def print_no_battery_summary(self) -> None:
        """Print the merged shared-community and individual-house baselines."""
        summaries = self._stored_summaries()
        print("\n" + "=" * 64, file=self.stream)
        print("NO BATTERY COMPARISON", file=self.stream)
        print("=" * 64, file=self.stream)
        for scenario, label in (
            ("no_battery_shared", "Shared community"),
            ("no_battery_individual", "Individual houses"),
        ):
            summary = summaries[scenario]
            cost = summary["grid_import_aud"] - summary["grid_export_aud"]
            print(
                f"{label}: import cost=${self._number(summary['grid_import_aud'], 4)}; "
                f"export revenue=${self._number(summary['grid_export_aud'], 4)}; "
                f"economic cost=${self._number(cost, 4)}; "
                f"emissions={self._number(summary['emissions_tonnes'], 6)} tCO2-e",
                file=self.stream,
            )
            emissions_cost = summary["emissions_cost_aud"]
            print(
                f"  Theoretical emissions cost: ${self._number(emissions_cost, 4)}; "
                f"Balanced cost: ${self._number(cost + emissions_cost, 4)}",
                file=self.stream,
            )
