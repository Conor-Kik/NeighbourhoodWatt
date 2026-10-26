from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend_v2.community_optimiser.optimiser_constants import PERIODS_PER_DAY
from backend_v2.simulation_engine import SimulationEngine
from database_v2.models.optimiser_models import (
    ForecastWindow,
    LiveControllerOutput,
    OptimiserOutput,
    RealisedWindows,
    SimulationStep,
    Solver,
)
from database_v2.models.request_models import ObjectiveMode
from tests.factories import BatteryConfigFactory, SimulationRequestFactory


@pytest.fixture
def dummy_request():
    """SimulationRequest that spans 2 hours (four 30-min steps)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)

    return SimulationRequestFactory.build(
        start_time_utc=start,
        end_time_utc=end,
    )


@pytest.fixture
def mock_forecast_window(dummy_request) -> ForecastWindow:
    """A valid 24-hour forecast returned by the mocked DAO."""
    start = dummy_request.start_time_utc
    return ForecastWindow(
        run_time_utc=start,
        carbon_price_aud_per_t=0.0,
        target_times_utc=[
            start + timedelta(minutes=30 * i) for i in range(PERIODS_PER_DAY)
        ],
        buy_tariff_aud_per_kwh=[0.30] * PERIODS_PER_DAY,
        sell_tariff_aud_per_kwh=[0.05] * PERIODS_PER_DAY,
        carbon_intensity_t_per_mwh=[0.50] * PERIODS_PER_DAY,
        demand_kw=[1.0] * PERIODS_PER_DAY,
        pv_kw=[0.5] * PERIODS_PER_DAY,
    )


@pytest.fixture
def mock_realised_windows(dummy_request) -> RealisedWindows:
    """A valid six-interval realised block returned by the mocked DAO."""
    start = dummy_request.start_time_utc
    return RealisedWindows(
        run_times_utc=[start + timedelta(minutes=5 * i) for i in range(6)],
        agg_demand_kw=[1.0] * 6,
        agg_pv_kw=[0.5] * 6,
        buy_tariff_aud_per_kwh=[0.3] * 6,
        sell_tariff_aud_per_kwh=[0.05] * 6,
        carbon_intensity_t_per_mwh=[0.5] * 6,
    )


@pytest.mark.parametrize("periods", [4, 49])
@patch(
    "backend_v2.simulation_engine.evaluate_perfect_foresight",
    return_value=(MagicMock(), []),
)
@patch("backend_v2.simulation_engine.ResultsDao")
@patch("backend_v2.simulation_engine.SimulationDao")
@patch("backend_v2.simulation_engine.OptimiserRunner")
@patch("backend_v2.simulation_engine.LiveController")
@patch("backend_v2.simulation_engine.BatteryDao")
def test_engine_run_loop(
    MockBatteryDao,
    MockLiveController,
    MockOptimiserRunner,
    MockSimulationDao,
    MockResultsDao,
    MockPerfectForesight,
    mock_forecast_window,
    mock_realised_windows,
    dummy_request,
    periods,
):
    """Both controllers evolve independently, including cycle budget expiry."""
    dummy_request.end_time_utc = dummy_request.start_time_utc + timedelta(
        minutes=30 * periods
    )
    mock_realised_windows.agg_demand_kw = [0, 2, 0, 0, 0, 0]
    mock_realised_windows.agg_pv_kw = [2, 0, 0, 0, 0, 0]

    mock_dao_instance = MockSimulationDao.return_value
    mock_dao_instance.run_exists.return_value = False
    mock_dao_instance.get_forecast_window.return_value = mock_forecast_window
    mock_dao_instance.get_realised_windows.return_value = mock_realised_windows

    MockBatteryDao.return_value.get_battery_config.return_value = (
        BatteryConfigFactory.build(
            capacity_kwh=1,
            max_power_kw=2,
            efficiency=1,
            min_soc_frac=0,
            max_soc_frac=1,
            max_cycles_24h=0.1,
        )
    )

    mock_lc_instance = MockLiveController.return_value
    MockOptimiserRunner.return_value.run.return_value = OptimiserOutput(
        solver=Solver.HIGHS,
        objective_mode=ObjectiveMode.COST,
        objective=1.0,
        shadow_price=0.2,
        soc_kwh=1.0,
        charge_kw=0.0,
        discharge_kw=0.0,
        grid_import_kw=0.5,
        grid_export_kw=0.0,
        curtailed_pv_kw=0.0,
        rolling_throughput_kwh=0.0,
    )
    mock_lc_instance.step.return_value = LiveControllerOutput(
        charge_kw=0.0,
        discharge_kw=1.0,
        grid_import_kw=0.0,
        grid_export_kw=0.0,
        curtailed_pv_kw=0.0,
        next_soc_kwh=1.0,
        cycle_throughput_kwh=0.1,
        reasons=("test",),
    )

    mock_callback = MagicMock()
    mock_on_step = MagicMock()

    engine = SimulationEngine(request=dummy_request, db_path=":memory:")

    steps = engine.run(step_callback=mock_callback, on_step=mock_on_step)

    assert engine.solver_name is Solver.GUROBI
    MockOptimiserRunner.assert_called_once_with(Solver.GUROBI)
    assert mock_dao_instance.get_forecast_window.call_count == periods + 1
    assert mock_dao_instance.get_realised_windows.call_count == periods
    assert MockOptimiserRunner.return_value.run.call_count == periods + 4
    assert MockLiveController.call_count == periods
    assert mock_lc_instance.step.call_count == 6 * periods
    assert mock_callback.call_count == periods
    assert [call.args[0] for call in mock_on_step.call_args_list] == steps
    assert len(steps) == periods
    assert isinstance(steps[0], SimulationStep)
    assert all(len(step.live_steps) == 6 for step in steps)
    MockPerfectForesight.assert_called_once_with(steps, engine.solver_name)
    mock_dao_instance.save_perfect_foresight.assert_called_once()
    assert mock_dao_instance.get_forecast_window.call_args_list[0].args == (
        dummy_request.end_time_utc,
    )
    MockResultsDao.return_value.save_terminal_value.assert_called_once_with(
        "optimised_battery", 1.0, 0.0, 0.0, 24.0
    )
    final_solves = [
        call.args[0]
        for call in MockOptimiserRunner.return_value.run.call_args_list[-4:]
    ]
    assert [d.objective_mode for d in final_solves] == [
        ObjectiveMode.COST,
        ObjectiveMode.COST,
        ObjectiveMode.EMISSIONS,
        ObjectiveMode.EMISSIONS,
    ]
    assert all(d.horizon_periods == 48 for d in final_solves)
    assert (
        final_solves[0].battery_throughput_history
        == final_solves[1].battery_throughput_history
    )
    assert final_solves[0].initial_soc_kwh == 0
    assert final_solves[1].initial_soc_kwh == 1

    MockResultsDao.return_value.calculate_and_save_all.assert_called_once()
    reactive = [
        call.kwargs["outputs"]
        for call in mock_dao_instance.save_live_controller_outputs.call_args_list
        if call.kwargs["scenario_name"] == "reactive_battery"
    ]
    assert len(reactive) == periods
    assert reactive[0][0].data.soc_kwh == 0
    assert reactive[0][0].output.charge_kw == pytest.approx(2)
    assert reactive[0][1].output.discharge_kw > 0
    assert reactive[1][0].data.soc_kwh == pytest.approx(
        reactive[0][-1].output.next_soc_kwh
    )
    assert reactive[1][0].data.soc_kwh != 1  # Smart controller's SOC.
    throughput = [
        sum(s.output.cycle_throughput_kwh for s in block) for block in reactive
    ]
    assert throughput[0] == pytest.approx(0.2)
    assert all(v == pytest.approx(0) for v in throughput[1:48])
    if periods > 48:
        assert throughput[48] == pytest.approx(0.2)
    assert all(
        sum(throughput[max(0, i - 47) : i + 1]) <= 0.2 + 1e-9 for i in range(periods)
    )


@patch("backend_v2.simulation_engine.BatteryDao")
@patch("backend_v2.simulation_engine.ResultsDao")
@patch("backend_v2.simulation_engine.SimulationDao")
@patch("backend_v2.simulation_engine.OptimiserRunner")
def test_engine_cancellation(
    MockOptimiserRunner,
    MockSimulationDao,
    MockBatteryDao,
    MockResultsDao,
    dummy_request,
):
    """Tests that the cancellation flag successfully breaks the loop."""
    MockSimulationDao.return_value.run_exists.return_value = False

    engine = SimulationEngine(request=dummy_request, db_path=":memory:")
    engine.run(is_cancelled=lambda: True)

    MockOptimiserRunner.return_value.run.assert_not_called()
    MockSimulationDao.return_value.get_forecast_window.assert_not_called()
    MockResultsDao.return_value.calculate_and_save_all.assert_not_called()


@patch("backend_v2.simulation_engine.BatteryDao")
@patch("backend_v2.simulation_engine.ResultsDao")
@patch("backend_v2.simulation_engine.SimulationDao")
@patch("backend_v2.simulation_engine.OptimiserRunner")
def test_engine_reuses_completed_run(
    MockOptimiserRunner,
    MockSimulationDao,
    MockResultsDao,
    MockBatteryDao,
    dummy_request,
):
    """Tests that a completed run is not simulated again."""
    MockSimulationDao.return_value.run_exists.return_value = True

    engine = SimulationEngine(request=dummy_request, db_path=":memory:")
    assert engine.run() == []
    MockOptimiserRunner.assert_not_called()

    MockSimulationDao.return_value.save_simulation_request.assert_not_called()
    MockOptimiserRunner.return_value.run.assert_not_called()
