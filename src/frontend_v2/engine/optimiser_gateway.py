from datetime import UTC, datetime
from math import floor

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from backend_v2.pipeline_runner import ensure_data
from backend_v2.simulation_engine import SimulationEngine
from database_v2.models.request_models import SimulationRequest
from frontend_v2.sources.replay_clock import ReplayClock


class SimulationWorker(QThread):
    """Created each time a simulation is launched to run off the main thread."""

    progress = pyqtSignal(str)
    step_completed = pyqtSignal(datetime)
    cancelled = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, request: SimulationRequest, db_path: str):
        super().__init__()
        self.request = request
        self.db_path = db_path
        self._engine = None

    def run(self):
        """Run pipelines and simulation engine on the thread."""
        try:
            # Run data pipelines
            self.progress.emit("Fetching data pipelines...")
            ensure_data(
                self.request,
                self.db_path,
                callback=self.progress.emit,
                is_cancelled=self.isInterruptionRequested,
            )

            if self.isInterruptionRequested():
                self.cancelled.emit(self.request.request_id)
                return

            # Run simulation engine
            self.progress.emit("Starting simulation engine...")
            self._engine = SimulationEngine(self.request, self.db_path)
            self._engine.run(
                step_callback=self._on_step,
                is_cancelled=self.isInterruptionRequested,
            )

            if self.isInterruptionRequested():
                self.cancelled.emit(self.request.request_id)
            else:
                self.finished.emit(self.request.request_id)

        except Exception as e:
            self.error.emit(str(e))

        finally:
            # Always release simulation engine
            self._engine = None

    def _on_step(self, completed_at: str) -> None:
        """Re-emit one completed optimiser timestamp as a datetime.

        The engine reports steps as ISO strings; converting here keeps the
        stringly-typed engine on this class alone."""
        if not self.isInterruptionRequested():
            self.step_completed.emit(datetime.fromisoformat(completed_at))
            progress_pct = floor(
                (
                    datetime.fromisoformat(completed_at).replace(tzinfo=UTC)
                    - self.request.start_time_utc
                )
                / (self.request.end_time_utc - self.request.start_time_utc)
                * 100
            )
            self.progress.emit(f"Simulation progress: {(progress_pct):02d}%")


class OptimiserGateway(QObject):
    """Manages simulation starting, cancelling, and observing from the main thread."""

    simulation_state_changed = pyqtSignal(bool)
    simulation_progress = pyqtSignal(str)
    simulation_finished = pyqtSignal(str)
    simulation_cancelled = pyqtSignal(str)
    simulation_error = pyqtSignal(str)

    def __init__(self, db_path: str, replay_clock: ReplayClock):
        super().__init__()
        self.db_path = db_path
        self.replay_clock = replay_clock
        self._worker = None
        self.is_running = False
        self._cache_busy = False

    def set_cache_busy(self, busy: bool) -> None:
        """Reserve the app's AEMO files while a cleanup is in progress."""
        if busy and self.is_running:
            raise ValueError("Wait for the simulation to finish before clearing cache.")
        self._cache_busy = busy

    def start_simulation(self, kwargs_for_request):
        """Launch a simulation worker."""

        if self._cache_busy:
            self.simulation_error.emit("Wait for AEMO cache cleanup to finish.")
            return

        # Already started
        if self._worker and self._worker.isRunning():
            return

        try:
            request = SimulationRequest(
                objective_mode=kwargs_for_request["objective_mode"],
                start_time_utc=kwargs_for_request["start_time_utc"],
                end_time_utc=kwargs_for_request["end_time_utc"],
                location_name=kwargs_for_request["location_name"],
                battery_config_id=kwargs_for_request["battery_config_id"],
                houses=kwargs_for_request["houses"],
                fixed_buy_aud_per_kwh=kwargs_for_request["fixed_buy_aud_per_kwh"],
                fixed_sell_aud_per_kwh=kwargs_for_request["fixed_sell_aud_per_kwh"],
            )
            self.replay_clock.set_window(request.start_time_utc, request.end_time_utc)
        except ValueError as e:  # pydantic ValidationError is a ValueError
            self.simulation_error.emit(str(e))
            return

        # Connect worker signals with gateway signals/functions
        self._worker = SimulationWorker(request, self.db_path)
        self._worker.progress.connect(self.simulation_progress.emit)
        self._worker.step_completed.connect(self._record_optimiser_step)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self.is_running = True
        self.simulation_state_changed.emit(True)
        self._worker.start()

    def cancel_simulation(self):
        """Request an interuption on the simulation worker."""

        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()

    def shutdown(self):
        """Cancel any running simulation and wait for the worker thread to finish.

        Called when the window closes so the process does not stay alive on a
        running QThread."""
        worker = self._worker
        if worker is None:
            return

        if worker.isRunning():
            worker.requestInterruption()
            worker.wait()

        self._cleanup_worker()

    def _record_optimiser_step(self, completed_at: datetime) -> None:
        """Move the replay clock's optimiser head to a completed optimiser step.

        Raises:
            ValueError: from the clock, if the step falls outside the replay."""
        if self._worker and self._worker.isInterruptionRequested():
            return
        self.replay_clock.set_t_optimiser(self.replay_clock.step_of(completed_at))

    def _cleanup_worker(self):
        """Safely clean up the simulation worker."""

        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _on_cancelled(self, request_id):
        self.is_running = False
        self.simulation_state_changed.emit(False)
        self.simulation_cancelled.emit(request_id)

    def _on_finished(self, request_id):
        self.is_running = False
        self.simulation_state_changed.emit(False)
        self.simulation_finished.emit(request_id)

    def _on_error(self, err_msg):
        self.is_running = False
        self.simulation_state_changed.emit(False)
        self.simulation_error.emit(err_msg)

    def close(self):
        self.shutdown()
