import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from backend_v2.community_optimiser.optimiser_constants import (
    LIVE_CONTROL_STEP_MINUTES,
    OPTIMISER_PERIOD_HOURS,
    PERIODS_PER_DAY,
)
from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from backend_v2.community_optimiser.perfect_foresight import evaluate_perfect_foresight
from backend_v2.community_optimiser.terminal_value import (
    TerminalValue,
    estimate_balanced_terminal_value,
    estimate_terminal_value,
)
from backend_v2.live_controller.live_controller import LiveController
from backend_v2.live_controller.reactive_self_consumption_controller import (
    ReactiveSelfConsumptionController,
)
from database_v2.data_access_objects.battery_dao import BatteryDao
from database_v2.data_access_objects.results_dao import ResultsDao
from database_v2.data_access_objects.simulation_dao import SimulationDao
from database_v2.models.optimiser_models import (
    LiveControllerInput,
    LiveStep,
    OptimiserInput,
    OptimiserOutput,
    SimulationStep,
    Solver,
)
from database_v2.models.request_models import (
    BatteryConfig,
    SimulationRequest,
)

logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    The core orchestrator of the community battery simulation.
    Strictly coordinates data fetching, optimization, and dispatch.
    """

    battery: BatteryConfig

    def __init__(
        self,
        request: SimulationRequest,
        db_path: str,
        solver_name: Solver = Solver.GUROBI,
    ) -> None:
        self.request = request
        self.dao = SimulationDao(db_path, request)
        self.results_dao = ResultsDao(db_path, request)
        self.solver_name = solver_name
        self.perfect_result: tuple[OptimiserInput, list[OptimiserOutput]] | None = None

        battery = BatteryDao(db_path).get_battery_config(request.battery_config_id)
        if battery is None:
            raise RuntimeError(
                f"Battery could not be retrieved from db: {request.battery_config_id}"
            )
        self.battery = battery

    def estimate_saved_balanced_terminal_value(self, soc_kwh: float) -> TerminalValue:
        """Read a completed run's end forecast and value energy without dispatch."""
        if abs(soc_kwh - self.battery.min_soc_kwh) <= 1e-6:
            return TerminalValue(0, 0, 0, 0)
        data = self._forecast_input(
            self.request.end_time_utc,
            soc_kwh,
            self.dao.get_terminal_throughput_history(),
        )
        return estimate_balanced_terminal_value(data, OptimiserRunner(self.solver_name))

    def _forecast_input(
        self, run_time: datetime, soc_kwh: float, throughput_history: list[float]
    ) -> OptimiserInput:
        """Load and validate the normal forecast for rolling or terminal valuation."""
        forecast = self.dao.get_forecast_window(run_time)

        # Validate all series have 48 intervals
        if not all(
            len(series) == PERIODS_PER_DAY
            for series in (
                forecast.target_times_utc,
                forecast.demand_kw,
                forecast.pv_kw,
                forecast.buy_tariff_aud_per_kwh,
                forecast.sell_tariff_aud_per_kwh,
                forecast.carbon_intensity_t_per_mwh,
            )
        ):
            raise RuntimeError(
                f"Incomplete {PERIODS_PER_DAY}-period forecast at {run_time}"
            )

        return OptimiserInput(
            objective_mode=self.request.objective_mode,
            battery_constants=self.battery,
            initial_soc_kwh=soc_kwh,
            battery_throughput_history=throughput_history,
            carbon_price_aud_per_t=forecast.carbon_price_aud_per_t,
            tariffs_buy_aud_per_kwh=forecast.buy_tariff_aud_per_kwh,
            tariffs_sell_aud_per_kwh=forecast.sell_tariff_aud_per_kwh,
            carbon_intensity_t_per_mwh=forecast.carbon_intensity_t_per_mwh,
            demand_aggregate_kw=forecast.demand_kw,
            pv_aggregate_kw=forecast.pv_kw,
        )

    def run(
        self,
        step_callback: Callable[[str], None] | None = None,
        *,
        on_step: Callable[[SimulationStep], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[SimulationStep]:
        """
        Executes the simulation loop.
        step_callback reports each completed timestamp for UI progress.
        on_step receives each completed 30-minute result.
        """
        try:
            if self.dao.run_exists():
                logger.info(
                    "Simulation %s already completed for this configuration",
                    self.request.request_id,
                )
                return []  # The frontend reads completed results from the database.

            if is_cancelled is not None and is_cancelled():
                logger.warning("Simulation cancelled.")
                return []

            # Save the simulation request, clearing any incomplete earlier run.
            self.dao.save_simulation_request()
            self.optimiser_runner = OptimiserRunner(self.solver_name)

            current_time = self.request.start_time_utc
            end_time = self.request.end_time_utc

            # Initialise the battery soc and throughput histories
            # Throughput histories are a sliding 24hr window used for
            # battery degradation constraints
            curr_soc_kwh = self.battery.min_soc_kwh
            battery_throughput_history = [0.0] * PERIODS_PER_DAY
            reactive_soc_kwh = self.battery.min_soc_kwh
            reactive_throughput_history = [0.0] * PERIODS_PER_DAY
            steps: list[SimulationStep] = []

            # Check the final forecast before the long run; no extra dispatch is made.
            # So we fail fast on missing data instead of getting to the end and failing.
            terminal_forecast_input = self._forecast_input(
                end_time, self.battery.min_soc_kwh, battery_throughput_history
            )

            logger.info(
                "Starting simulation %s: %s to %s, %d houses, objective=%s",
                self.request.request_id,
                current_time.isoformat(),
                end_time.isoformat(),
                len(self.request.houses),
                self.request.objective_mode.value,
            )

            # 30-minute MPC loop
            while current_time < end_time:
                if is_cancelled is not None and is_cancelled():
                    logger.warning(
                        "Simulation %s cancelled after %d optimiser steps",
                        self.request.request_id,
                        len(steps),
                    )
                    return []

                optimiser_input = self._forecast_input(
                    current_time, curr_soc_kwh, battery_throughput_history
                )

                optimiser_output = self.optimiser_runner.run(optimiser_input)

                live_controller = LiveController(
                    battery_constants=self.battery,
                    optimiser_output=optimiser_output,
                )

                reactive_controller = ReactiveSelfConsumptionController(
                    battery_constants=self.battery,
                    cycle_throughput_remaining_kwh=max(
                        2.0 * self.battery.max_cycles_24h * self.battery.capacity_kwh
                        - sum(reactive_throughput_history[1:]),
                        0.0,
                    ),
                )

                realised_windows = self.dao.get_realised_windows(current_time)

                if not all(
                    len(series) == 6
                    for series in (
                        realised_windows.run_times_utc,
                        realised_windows.agg_demand_kw,
                        realised_windows.agg_pv_kw,
                        realised_windows.buy_tariff_aud_per_kwh,
                        realised_windows.sell_tariff_aud_per_kwh,
                        realised_windows.carbon_intensity_t_per_mwh,
                    )
                ):
                    raise RuntimeError(
                        f"Incomplete six-step realised data at {current_time}"
                    )
                logger.debug(
                    "Loaded six realised intervals at %s", current_time.isoformat()
                )

                cycle_throughput_kwh = 0.0
                live_steps: list[LiveStep] = []
                reactive_steps: list[LiveStep] = []

                # 5-minute controller loop
                # Live and reactive controllers run in parallel
                for t in range(len(realised_windows.run_times_utc)):
                    live_controller_input = LiveControllerInput(
                        soc_kwh=curr_soc_kwh,
                        demand_kw=realised_windows.agg_demand_kw[t],
                        pv_kw=realised_windows.agg_pv_kw[t],
                        buy_tariff_aud_per_kwh=realised_windows.buy_tariff_aud_per_kwh[
                            t
                        ],
                        sell_tariff_aud_per_kwh=(
                            realised_windows.sell_tariff_aud_per_kwh[t]
                        ),
                        carbon_intensity_t_per_mwh=(
                            realised_windows.carbon_intensity_t_per_mwh[t]
                        ),
                        carbon_price_aud_per_t=optimiser_input.carbon_price_aud_per_t,
                    )

                    live_controller_output = live_controller.step(
                        live_data=live_controller_input
                    )

                    live_steps.append(
                        LiveStep(
                            timestamp_utc=current_time
                            + timedelta(minutes=LIVE_CONTROL_STEP_MINUTES * t),
                            data=live_controller_input,
                            output=live_controller_output,
                        )
                    )

                    curr_soc_kwh = live_controller_output.next_soc_kwh
                    cycle_throughput_kwh += live_controller_output.cycle_throughput_kwh
                    reactive_input = live_controller_input.model_copy(
                        update={"soc_kwh": reactive_soc_kwh}
                    )
                    reactive_output = reactive_controller.step(reactive_input)
                    reactive_steps.append(
                        LiveStep(
                            timestamp_utc=live_steps[-1].timestamp_utc,
                            data=reactive_input,
                            output=reactive_output,
                        )
                    )
                    reactive_soc_kwh = reactive_output.next_soc_kwh

                # Save 5-min and 30-min results to DB
                self.dao.save_live_controller_outputs(
                    scenario_name="optimised_battery",
                    outputs=live_steps,
                )

                self.dao.save_live_controller_outputs(
                    scenario_name="reactive_battery",
                    outputs=reactive_steps,
                )

                self.dao.save_optimiser_output(current_time, optimiser_output)

                completed_step = SimulationStep(
                    timestamp_utc=current_time,
                    forecast=optimiser_input,
                    plan=optimiser_output,
                    live_steps=tuple(live_steps),
                )

                steps.append(completed_step)
                logger.debug(
                    "Completed step %s: objective=%.4f, SOC=%.3f kWh",
                    current_time.isoformat(),
                    optimiser_output.objective,
                    curr_soc_kwh,
                )

                # Report back if anything is listening
                if step_callback:
                    step_callback(current_time.isoformat())
                if on_step:
                    on_step(completed_step)

                current_time += timedelta(hours=OPTIMISER_PERIOD_HOURS)

                # Slide throughput histories
                battery_throughput_history.append(cycle_throughput_kwh)
                battery_throughput_history.pop(0)
                reactive_throughput_history.append(
                    sum(s.output.cycle_throughput_kwh for s in reactive_steps)
                )
                reactive_throughput_history.pop(0)

            logger.info(
                "Simulation %s finished: %d optimiser steps, %d live intervals",
                self.request.request_id,
                len(steps),
                sum(len(step.live_steps) for step in steps),
            )

            if steps and not (is_cancelled is not None and is_cancelled()):
                # Solve and save perfect foresight LP problem
                self.perfect_result = evaluate_perfect_foresight(
                    steps, self.solver_name
                )
                self.dao.save_perfect_foresight(*self.perfect_result)

                # Calculate summaries and comparisons and save to DB
                self.results_dao.calculate_and_save_all()
                terminal_input = terminal_forecast_input.model_copy(
                    update={
                        "initial_soc_kwh": curr_soc_kwh,
                        "battery_throughput_history": battery_throughput_history,
                    }
                )

                # Valuate electricity left in the battery and save to DB
                cost_value, emissions_value = estimate_terminal_value(
                    terminal_input, self.optimiser_runner
                )
                self.results_dao.save_terminal_value(
                    "optimised_battery",
                    curr_soc_kwh,
                    cost_value,
                    emissions_value,
                    terminal_input.horizon_periods * terminal_input.period_hours,
                )

            return steps

        finally:
            # Make sure DB connections are always closed
            self.dao.close()
            self.results_dao.close()
