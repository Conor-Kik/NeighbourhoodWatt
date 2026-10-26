"""Solves the battery planning problem and returns the chosen actions and results."""

import logging
from typing import Literal, overload

import pyomo.environ as pyo
from pyomo.opt import SolverStatus, TerminationCondition

from backend_v2.community_optimiser.optimiser_builder import build_community_optimiser
from database_v2.models.optimiser_models import (
    OptimiserInput,
    OptimiserOutput,
    Solver,
    SolverUnavailableError,
)

logger = logging.getLogger(__name__)

# Both supported solvers can return a slightly negative value at a zero bound.
_SOLVER_BOUND_TOLERANCE = 1e-6


def _nonnegative_solver_value(variable: pyo.Var) -> float:
    """Read a solver value and round tiny negative numerical errors to zero."""
    value = float(pyo.value(variable))
    if -_SOLVER_BOUND_TOLERANCE <= value < 0.0:
        return 0.0
    return value


class OptimiserRunner:
    """Runs the community optimiser on a given solver and returns the results."""

    def __init__(self, solver_name: Solver = Solver.GUROBI, tee: bool = False) -> None:
        """Set up the requested solver, using HiGHS if Gurobi is unavailable."""
        self.tee = tee
        self.solver_name = solver_name

        self.solver = self._create_solver(self.solver_name, tee)

        # Check if the solver is available
        # Pyomo's solver truthiness calls available() with exceptions enabled.
        # Check identity first so missing optional bindings reach the fallback.
        if self.solver is None or not self.solver.available(exception_flag=False):
            # If Gurobi is unavailable, fall back to HiGHs
            if self.solver_name is Solver.GUROBI:
                logger.warning(
                    "Gurobi solver is unavailable. Falling back to HiGHs solver."
                )
                self.solver_name = Solver.HIGHS
                self.solver = self._create_solver(self.solver_name, tee)

            # If the solver is still not available, raise an error
            if self.solver is None or not self.solver.available(exception_flag=False):
                logger.error(
                    f"{self.solver_name.value} is not available or not installed."
                )
                raise SolverUnavailableError(
                    f"{self.solver_name.value} is not available or not installed."
                )

        logger.info("Using optimiser solver %s", self.solver_name.value)

    def _create_solver(self, solver_name: Solver, tee: bool):
        """Returns the Pyomo solver object with the supplied solver name.
        `tee` indicates whether to log to console."""
        if solver_name is Solver.GUROBI:
            return pyo.SolverFactory(
                solver_name.value,
                manage_env=True,
                options={"OutputFlag": int(tee), "LogToConsole": int(tee)},
            )
        else:
            return pyo.SolverFactory(
                solver_name.value, options={"log_to_console": int(tee)}
            )

    def _solve_model(self, data: OptimiserInput) -> pyo.ConcreteModel:
        """Build and solve the model, retaining every period's decisions."""
        logger.debug(
            "Solving %s objective with %s over %d periods",
            data.objective_mode.value,
            self.solver_name.value,
            data.horizon_periods,
        )
        model = build_community_optimiser(data)

        try:
            results = self.solver.solve(model, tee=self.tee)
        except Exception as exc:
            # If it failed with Gurobi, we try again with HiGHs
            # This can happen when no Gurobi licence was found
            if self.solver_name is Solver.GUROBI:
                logger.warning("Gurobi solve failed. Falling back to HiGHs solver.")
                self.solver_name = Solver.HIGHS
                self.solver = self._create_solver(self.solver_name, self.tee)

                if self.solver is None or not self.solver.available(
                    exception_flag=False
                ):
                    logger.error("HiGHs solver is unavailable during fallback")
                    raise SolverUnavailableError(
                        "HiGHs is not available or not installed for fallback."
                    ) from exc

                model = build_community_optimiser(data)
                try:
                    results = self.solver.solve(model, tee=self.tee)
                except Exception as inner_exc:
                    logger.exception(
                        f"Solve failed with fallback optimiser {self.solver_name.value}"
                    )
                    raise RuntimeError(
                        f"Solve failed with fallback optimiser"
                        f"{self.solver_name.value}: {inner_exc}"
                    ) from inner_exc
            else:
                logger.exception(
                    f"Solve failed with optimiser {self.solver_name.value}"
                )
                raise RuntimeError(
                    f"Solve failed with optimiser {self.solver_name.value}: {exc}"
                ) from exc

        termination = results.solver.termination_condition
        status = results.solver.status
        if termination != TerminationCondition.optimal:
            logger.error("Optimiser did not converge: %s", termination)
            raise RuntimeError(
                f"{self.solver_name.value} did not find an optimal solution: "
                f"{termination}"
            )
        if status not in (SolverStatus.ok, SolverStatus.warning):
            logger.error("Optimiser returned unexpected status: %s", status)
            raise RuntimeError(
                f"{self.solver_name.value} returned unexpected solver status: {status}"
            )

        return model

    @overload
    def run(
        self, data: OptimiserInput, *, all_decisions: Literal[False] = False
    ) -> OptimiserOutput:
        """Describe the return type when only the first decision is requested."""
        ...

    @overload
    def run(
        self, data: OptimiserInput, *, all_decisions: Literal[True]
    ) -> list[OptimiserOutput]:
        """Describe the return type when every planned decision is requested."""
        ...

    def run(
        self, data: OptimiserInput, *, all_decisions: bool = False
    ) -> OptimiserOutput | list[OptimiserOutput]:
        """Return the first decision, or every decision in the solved horizon.

        Each decision carries the full solve's objective and initial SOC shadow
        price, plus the battery state and power flows for its own period.
        """
        model = self._solve_model(data)

        initial_soc_constraint = model.initial_soc_constraint
        if initial_soc_constraint not in model.dual:
            logger.error("Optimiser returned no initial SOC dual")
            raise RuntimeError(
                f"The {self.solver_name.value} backend did not return a dual for the "
                "initial SOC constraint; the shadow price cannot be calculated."
            )

        # The solver reports how the total would change if starting SOC increased.
        # Negating that value gives the benefit of one extra starting kWh.
        shadow_price = -float(model.dual[initial_soc_constraint])
        objective = float(pyo.value(model.objective))
        financial_cost = float(pyo.value(model.financial_cost))
        emissions = float(pyo.value(model.emissions))
        carbon_cost = float(pyo.value(model.carbon_cost))

        # The current forecast action has not happened yet. Exclude the oldest
        # history sample because that sample expires as the new action arrives.
        max_cycle_throughput = (
            2.0
            * data.battery_constants.max_cycles_24h
            * data.battery_constants.capacity_kwh
        )
        previous_cycle_throughput = sum(data.battery_throughput_history[1:])

        def decision(t: int) -> OptimiserOutput:
            """Package one solved period into battery actions,
            grid flows and result values."""

            charge = _nonnegative_solver_value(model.V[t])
            discharge = _nonnegative_solver_value(model.Q[t])
            rolling = _nonnegative_solver_value(model.Y[t])
            throughput = (
                data.battery_constants.efficiency * charge
                + discharge / data.battery_constants.efficiency
            ) * data.period_hours
            previous_throughput = (
                previous_cycle_throughput if t == 0 else max(rolling - throughput, 0.0)
            )
            return OptimiserOutput(
                solver=self.solver_name,
                objective_mode=data.objective_mode,
                objective=objective,
                financial_cost_aud=financial_cost,
                emissions_tonnes=emissions,
                carbon_cost_aud=carbon_cost,
                shadow_price=shadow_price,
                soc_kwh=_nonnegative_solver_value(model.SOC[t]),
                charge_kw=charge,
                discharge_kw=discharge,
                grid_import_kw=_nonnegative_solver_value(model.F_in[t]),
                grid_export_kw=_nonnegative_solver_value(model.F_out[t]),
                curtailed_pv_kw=_nonnegative_solver_value(model.U[t]),
                rolling_throughput_kwh=rolling,
                expected_net_load_kw=(
                    data.demand_aggregate_kw[t] - data.pv_aggregate_kw[t]
                ),
                cycle_throughput_remaining_kwh=max(
                    max_cycle_throughput - previous_throughput,
                    0.0,
                ),
                next_soc_kwh=(
                    _nonnegative_solver_value(model.SOC[t + 1])
                    if all_decisions
                    else None
                ),
            )

        if all_decisions:
            return [decision(t) for t in range(data.horizon_periods)]
        output = decision(0)
        logger.debug(
            "Optimiser solved: objective=%.6f, charge=%.3f kW, discharge=%.3f kW",
            output.objective,
            output.charge_kw,
            output.discharge_kw,
        )
        return output
