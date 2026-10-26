from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from pyomo.opt import SolverStatus, TerminationCondition

from backend_v2.community_optimiser.optimiser_runner import OptimiserRunner
from database_v2.models.optimiser_models import (
    Solver,
    SolverUnavailableError,
)
from database_v2.models.request_models import ObjectiveMode


@pytest.fixture
def mock_solver():
    with patch("pyomo.environ.SolverFactory") as factory:
        solver = MagicMock(spec=["available", "solve"])
        solver.available.return_value = True
        factory.return_value = solver
        yield solver


@patch("backend_v2.community_optimiser.optimiser_runner.build_community_optimiser")
@patch("pyomo.environ.value", side_effect=lambda x: x)
@pytest.mark.parametrize("solver_name", [Solver.HIGHS, Solver.GUROBI])
@pytest.mark.parametrize(
    ("solver_export", "expected_export"),
    [(5.0, 5.0), (-3.352873534367973e-14, 0.0), (-0.01, None)],
)
def test_runner_success(
    mock_pyo,
    mock_build,
    mock_solver,
    caplog,
    solver_name,
    solver_export,
    expected_export,
):
    mock_solver.solve.return_value.solver.termination_condition = (
        TerminationCondition.optimal
    )
    mock_solver.solve.return_value.solver.status = SolverStatus.ok

    m = MagicMock(initial_soc_constraint="c1", dual={"c1": -15.5}, objective=150.0)
    for var in ["SOC", "V", "Q", "F_in", "F_out", "U", "Y"]:
        setattr(m, var, {0: 5.0})
    m.F_out = {0: solver_export}
    mock_build.return_value = m

    data = MagicMock(objective_mode=ObjectiveMode.COST)
    data.battery_constants.max_cycles_24h = 1.0
    data.battery_constants.capacity_kwh = 20.0
    data.battery_throughput_history = [0.0] * 48
    data.demand_aggregate_kw = [3.0]
    data.pv_aggregate_kw = [1.0]
    data.tariffs_buy_aud_per_kwh = [0.3]
    data.tariffs_sell_aud_per_kwh = [0.1]
    data.carbon_intensity_t_per_mwh = [0.7]

    with caplog.at_level(
        "INFO", logger="backend_v2.community_optimiser.optimiser_runner"
    ):
        if expected_export is None:
            with pytest.raises(ValidationError, match="grid_export_kw"):
                OptimiserRunner(solver_name=solver_name).run(data)
            return
        out = OptimiserRunner(solver_name=solver_name).run(data)

    assert out.objective == 150.0
    assert out.shadow_price == 15.5
    assert out.charge_kw == 5.0
    assert out.rolling_throughput_kwh == 5.0
    assert out.grid_export_kw == expected_export
    assert out.expected_net_load_kw == 2.0
    assert out.solver == solver_name
    assert f"Using optimiser solver {solver_name.value}" in caplog.text


@patch("backend_v2.community_optimiser.optimiser_runner.build_community_optimiser")
def test_runner_errors(mock_build, mock_solver):
    mock_solver.available.return_value = False
    with pytest.raises(SolverUnavailableError):
        OptimiserRunner()

    mock_solver.available.return_value = True
    runner = OptimiserRunner()

    mock_solver.solve.return_value.solver.termination_condition = (
        TerminationCondition.infeasible
    )
    with pytest.raises(
        RuntimeError, match="gurobi_direct did not find an optimal solution: infeasible"
    ):
        runner.run(MagicMock())

    mock_solver.solve.return_value.solver.termination_condition = (
        TerminationCondition.optimal
    )
    mock_solver.solve.return_value.solver.status = SolverStatus.ok
    mock_build.return_value = MagicMock(dual={})

    with pytest.raises(RuntimeError, match="dual"):
        runner.run(MagicMock())


@pytest.mark.parametrize("failure", ["unavailable", "solve_error"])
@patch("backend_v2.community_optimiser.optimiser_runner.build_community_optimiser")
@patch("pyomo.environ.SolverFactory")
def test_default_solver_falls_back_to_highs(factory, build, failure):
    gurobi, highs = MagicMock(), MagicMock()
    # Real Pyomo solvers can raise from __bool__ when bindings are absent.
    gurobi.__bool__.side_effect = AssertionError("Use available(exception_flag=False)")
    highs.__bool__.side_effect = AssertionError("Use available(exception_flag=False)")
    gurobi.available.return_value = failure != "unavailable"
    gurobi.solve.side_effect = RuntimeError("Gurobi licence unavailable")
    highs.available.return_value = True
    highs.solve.return_value.solver.termination_condition = TerminationCondition.optimal
    highs.solve.return_value.solver.status = SolverStatus.ok
    factory.side_effect = [gurobi, highs]

    runner = OptimiserRunner()
    runner._solve_model(MagicMock())

    assert [call.args[0] for call in factory.call_args_list] == [
        Solver.GUROBI.value,
        Solver.HIGHS.value,
    ]
    assert runner.solver_name is Solver.HIGHS
    highs.solve.assert_called_once_with(build.return_value, tee=False)
