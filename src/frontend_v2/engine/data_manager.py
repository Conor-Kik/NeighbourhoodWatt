"""data_manager.py
Routes and manages data between all buffers and every occurrence of change."""

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PyQt6.QtCore import (
    QMutex,
    QObject,
    QSettings,
    QThread,
    QWaitCondition,
    pyqtSignal,
    pyqtSlot,
)

from backend_v2.community_optimiser.terminal_value import TerminalValue
from backend_v2.simulation_engine import SimulationEngine
from database_v2.data_access_objects.frontend_dao import FrontendDao
from database_v2.utils.forecast_issue import forecast_issue_at
from frontend_v2.sources.replay_clock import ReplayClock
from frontend_v2.utils.buffer import DEFAULT_MAXLEN, Buffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status

GETTER_TYPE = "getter"
SETTER_TYPE = "setter"
GET_HEADER = "get_"
SET_HEADER = "set_"
FETCH_OPERATION = "fetch"
WRITE_OPERATION = "write"
CURRENT_REQUEST_SETTER = "set_current_request"
FORECAST_GETTER = "get_current_forecast_data"
REPLAY_GETTERS = {FORECAST_GETTER, "get_current_replay_cost"}
COST_GETTER = "get_current_operating_costs"
BALANCED_CREDIT = "estimated_terminal_balanced_value_aud"
BALANCED_COMPONENTS = {
    BALANCED_CREDIT: "objective",
    "estimated_terminal_balanced_financial_value_aud": "financial_aud",
    "estimated_terminal_balanced_emissions_value_tonnes": "emissions_tonnes",
}
logger = logging.getLogger(__name__)

UI_TIME_KEY = "time"
UI_DURATION_KEY = "duration"
UI_OPTIMISER_KEY = "optimiser_time"


@dataclass(frozen=True)
class DaoEntry:
    fn: Callable
    dtype: list[tuple[Any, Any]] | None
    fn_type: str
    pk: tuple[str, ...] | None = None


@dataclass(frozen=True)
class BufferEntry:
    buffer: Buffer | None
    dao: DaoEntry | None


class DataFetcher(QObject):
    """Manages the spawning thread and fetching data logic. Expects FrontendDao
    to enforce all public methods as getters and setters.

    NOTE: We use a dedicated QThread approach over a worker pool, simply because
    SQLite cannot concurrently write (without extra configuration like WAL).
    Update to WAL and worker pool management when required."""

    initialized = pyqtSignal()
    result = pyqtSignal(Result)

    def __init__(self):
        super().__init__()
        self.dao = None
        self._registry = {}
        self._balanced_terminal_estimate: tuple[tuple, TerminalValue] | None = None

    @pyqtSlot()
    def initialize(self):
        self.dao = FrontendDao()
        self._registry = self._build_registry()
        self.initialized.emit()

    def _build_registry(self) -> dict[str, DaoEntry]:
        registry: dict[str, DaoEntry] = {}
        for name in dir(self.dao):
            method = getattr(self.dao, name)

            if name.startswith(GET_HEADER):
                registry[name] = DaoEntry(
                    method, method.dtype, GETTER_TYPE, pk=getattr(method, "pk", None)
                )
            elif name.startswith(SET_HEADER):
                # Setters not expected to have dtype
                registry[name] = DaoEntry(
                    method, getattr(method, "dtype", None), SETTER_TYPE
                )
            # FrontendDAO expected to handle its own AttributeErrors
        return registry

    @property
    def registry(self) -> dict[str, DaoEntry]:
        return self._registry

    @pyqtSlot(int, list, object)
    def fetch(
        self, req_id: int, keys: list[str], replay_time: datetime | None = None
    ) -> None:
        try:
            results = {
                key: (
                    self._registry[key].fn(replay_time)
                    if key in REPLAY_GETTERS
                    else self._registry[key].fn()
                )
                for key in keys
            }
            if results.get(COST_GETTER):
                names = FrontendDao.get_current_operating_costs.dtype.names
                costs = []
                for record in results[COST_GETTER]:
                    summary = dict(zip(names, record, strict=True))
                    if summary["scenario_name"] == "optimised_battery":
                        estimate = self._saved_balanced_credit(summary)
                        for field, component in BALANCED_COMPONENTS.items():
                            summary[field] = getattr(estimate, component)
                    costs.append(tuple(summary[name] for name in names))
                results[COST_GETTER] = costs
        except Exception as exc:
            self.result.emit(
                Result(
                    Status.FAILURE,
                    data={
                        "operation": FETCH_OPERATION,
                        "request_id": req_id,
                    },
                    message=str(exc),
                )
            )
            return

        data = {"operation": FETCH_OPERATION, "request_id": req_id, "results": results}
        if replay_time is not None:
            data["replay_time"] = replay_time
        self.result.emit(Result(Status.SUCCESS, data=data))

    def _saved_balanced_credit(self, summary: dict) -> TerminalValue:
        """Memoise a selected summary's estimate; never solve on replay ticks."""
        if not math.isfinite(summary["terminal_soc_kwh"]):
            return TerminalValue(*(math.nan,) * 4)
        signature = tuple(
            None if isinstance(value, float) and not math.isfinite(value) else value
            for key, value in summary.items()
            if key not in BALANCED_COMPONENTS
        )
        if self._balanced_terminal_estimate is not None:
            previous, value = self._balanced_terminal_estimate
            if previous == signature:
                return value
        value = TerminalValue(*(math.nan,) * 4)
        engine = None
        try:
            request = next(
                run
                for run in self.dao.run_dao.get_all_simulation_requests()
                if run.request_id == summary["request_id"]
            )
            if request.objective_mode.value == "Balanced":
                engine = SimulationEngine(request, str(self.dao.db_path))
                value = engine.estimate_saved_balanced_terminal_value(
                    summary["terminal_soc_kwh"]
                )
        except Exception:
            logger.warning(
                "Balanced terminal estimate unavailable for %s",
                summary["request_id"],
                exc_info=True,
            )
        finally:
            if engine is not None:
                engine.dao.close()
                engine.results_dao.close()
        self._balanced_terminal_estimate = signature, value
        return value

    @pyqtSlot(int, dict)
    def write(self, req_id: int, form: dict[str, list[dict]]) -> None:
        try:
            for key, rows in form.items():
                if key == CURRENT_REQUEST_SETTER:
                    self._balanced_terminal_estimate = None
                method = self._registry[key].fn
                for row in rows:
                    method(row)
        except Exception as exc:
            self.result.emit(
                Result(
                    Status.FAILURE,
                    data={
                        "operation": WRITE_OPERATION,
                        "request_id": req_id,
                    },
                    message=str(exc),
                )
            )
            return

        self.result.emit(
            Result(
                Status.SUCCESS,
                data={
                    "operation": WRITE_OPERATION,
                    "request_id": req_id,
                },
            )
        )


class DataManager(QObject):
    """Oversees all buffers, UI values and changes.

    Requirements:
        Fills DB buffers via DAO off GUI thread.
        Apply current replay time to run data.
        Emit updated_keys after time-step, DB write and UI write.
        Discard superseeded fetches.
    """

    updated_keys = pyqtSignal(set)
    result = pyqtSignal(Result)

    _request_fetch = pyqtSignal(int, list, object)
    _request_write = pyqtSignal(int, dict)

    def __init__(self, preferences: QSettings | None = None):
        super().__init__()

        # Connect ReplayClock
        self.replay_clock = ReplayClock()
        self.replay_clock.time_changed.connect(self._update_fields)
        self.replay_clock.optimiser_changed.connect(self._publish_optimiser_field)

        # Build UI thead lock
        self._mutex = QMutex()
        self._wait_cond = QWaitCondition()
        self._ui: dict[str, Any] = {"time_duration": "24h"}
        self._preferences = preferences or QSettings("TheGridOptimists", "GridSync")
        for key in self._preferences.allKeys():
            if key == "time_duration":
                try:
                    self._ui[key] = json.loads(str(self._preferences.value(key)))
                except ValueError:
                    continue

        self._latest_req_id = 0
        self._pending_run = None
        self._run_picks: dict[int, Any] = {}

        self._fetch_in_flight: int | None = None
        self._fetch_pending = False
        self.registry: dict[str, BufferEntry] = {}
        self._build_fetcher_conns()

    def _build_fetcher_conns(self):
        self.fetcher_thread = QThread(self)
        self.fetcher = DataFetcher()

        self.fetcher.moveToThread(self.fetcher_thread)
        self.fetcher_thread.started.connect(self.fetcher.initialize)
        self.fetcher_thread.start()

        self._request_fetch.connect(self.fetcher.fetch)
        self._request_write.connect(self.fetcher.write)

        self.fetcher.initialized.connect(self._build_registry)
        self.fetcher.result.connect(self._handle_result)

    @pyqtSlot()
    def _build_registry(self):
        for key, entry in self.fetcher.registry.items():
            buffer = None
            if entry.fn_type == GETTER_TYPE:
                if entry.dtype is None:
                    raise ValueError(f"FrontendDao getter has no dtype: '{key}'")
                if entry.pk is None:
                    raise ValueError(f"FrontendDao getter has no pk: '{key}'")
                # Keep all four half-hourly scenarios for runs up to 28 days.
                maxlen = (
                    6000 if key == "get_current_scenario_timeline" else DEFAULT_MAXLEN
                )
                buffer = Buffer(dtype=entry.dtype, pk=entry.pk, maxlen=maxlen)
            self.registry[key] = BufferEntry(buffer, entry)
        self._update_fields()
        self.updated_keys.emit(set(self._ui))

    def get_record(self, key: str):
        """BufferEntry for key, raises if unknown, returns None if method is a
        setter."""
        record = self.registry.get(key)
        if record is None:
            raise ValueError(f'Unknown key given to DataManager: "{key}"')
        return record

    def get_buffer(self, key: str) -> Buffer | None:
        """Buffer for key, raises if unknown, returns None if method is a
        setter."""
        return self.get_record(key).buffer

    def get(self, key: str, timeout_ms: int = 0) -> Any:
        """DB key: its buffered records (None for setters).
        UI key: its value, waiting up to timeout_ms for it to be set if
        timeout_ms > 0.

        Fields:
            key: DAO method name or UI key.
            timeout_ms: # of ms to wait for a UI key; raises KeyError on
            timeout."""
        if key in self.registry:
            buffer = self.get_buffer(key)
            if buffer is None:
                return None
            return buffer.get()

        if key.startswith((GET_HEADER, SET_HEADER)) and key not in self._ui:
            raise ValueError(f'Unknown key given to DataManager: "{key}"')

        # Lock thread
        self._mutex.lock()
        try:
            # While loop prevents racing
            while timeout_ms and key not in self._ui:
                if not self._wait_cond.wait(self._mutex, timeout_ms):
                    raise KeyError(f"Timed out waiting for key: '{key}'")
            return self._ui.get(key)
        finally:
            self._mutex.unlock()

    def set_time(self, t: int) -> None:
        self.replay_clock.seek(t)

    def _publish_clock_fields(self) -> None:
        """Publish the replay clock as UI keys: the time being replayed, the
        length of the replay, and the optimiser's completed step.

        Called on every replay step, so the replay bar reads these three values
        off the DataManager rather than reaching into the clock."""
        clock = self.replay_clock
        self.set_ui(UI_TIME_KEY, clock.get_time())
        self.set_ui(UI_DURATION_KEY, clock.duration_minutes)
        self.set_ui(UI_OPTIMISER_KEY, clock.t_optimiser)

    def _publish_optimiser_field(self, t_optimiser: int) -> None:
        """Publish the optimiser's completed step as a UI key.

        The clock emits this whenever the optimiser head moves, which happens
        between replay steps while a simulation is running."""
        self.set_ui(UI_OPTIMISER_KEY, t_optimiser)

    def clear_buffers(self) -> None:
        """Empty every buffer, dropping the records of the run being replaced.

        Raises nothing: an empty buffer is a valid state."""
        for entry in self.registry.values():
            if entry.buffer is not None:
                entry.buffer.clear()

    def select_run(self, request_id: int) -> int:
        """Switch the app over to another saved run: the previous run's records
        are dropped from every buffer, the replay clock is sent back to the top of
        the day and given the new run's own start and end, then the new run is
        recorded in the app settings so every get_current_* getter reads from it.
        Returns the write's request id, which its Result carries in
        data["request_id"].

        The clock follows the successful write, so a failed pick preserves the
        previous replay.

        Raises:
            ValueError: from set, if the pick cannot be written or the run is not
            a whole number of steps long."""
        return self.set({CURRENT_REQUEST_SETTER: [{"request_id": request_id}]})

    def _point_clock_at_run(self, request_id: Any) -> None:
        """Drop the run the clock was holding and give it request_id's window.

        The window is read before the buffers are cleared, since the run list
        lives in one of them. A run they do not hold yet -- a simulation that
        finished moments ago -- is remembered and pointed at once the write's own
        refetch brings it in; a run still missing then leaves the clock unset
        rather than pointing at nobody's day.

        Raises:
            ValueError: from set_run, if the run is not a whole number of steps
            long."""
        window = self._replay_window(request_id)
        self._pending_run = None
        self.clear_buffers()
        self.replay_clock.reset()
        if window is None:
            self._pending_run = request_id
        else:
            self.replay_clock.set_run(*window)

    def _point_pending_run(self) -> None:
        """Give the clock the window of the run the last pick named, now that
        the write's refetch has put it in the buffers. Does nothing when no pick
        left a run to point at.

        Raises:
            ValueError: from set_run, if the run is not a whole number of steps
            long."""
        request_id = self._pending_run
        if request_id is None:
            return
        self._pending_run = None
        window = self._replay_window(request_id)
        if window is not None:
            self.replay_clock.set_run(*window)

    def _replay_window(self, request_id: int) -> tuple[str, str] | None:
        """The start and end of a saved run, or None if the buffers don't hold
        it."""
        rows = self.get_buffer("get_all_simulation_requests").get()
        run = rows[rows["request_id"] == str(request_id)]
        if not len(run):
            return None
        return (run[0]["start_time_utc"], run[0]["end_time_utc"])

    def set_ui(self, key: str, value: Any) -> Status:
        """Set UI value immediately, no DB."""
        if key in self.registry:
            raise ValueError(f'"{key}" is a DB key, not a UI key.')

        self._mutex.lock()
        try:
            status = Status.MODIFIED if key in self._ui else Status.CREATED
            self._ui[key] = value
            self._wait_cond.wakeAll()
        finally:
            self._mutex.unlock()

        if hasattr(self, "_preferences") and key == "time_duration":
            self._preferences.setValue(key, json.dumps(value))
        self.updated_keys.emit({key})
        return status

    def set(self, form: dict[str, list[dict]]) -> int:
        """Given a form that maps setter keys to rows, each row is set by
        FrontendDao, then every getter is refetched. Returns the request id,
        which the write's Result carries in data["request_id"].

        A successful write that picks the app's current run re-points the replay
        clock at that run's window. A run the buffers do not hold yet is pointed
        at once this write's refetch brings it in.

        Fields:
            form: Dictionary where key is a FrontendDao setter, and the value is
            a list of dictionaries, each passed to the setter once.

        Raises:
            ValueError: for a key that is not a registered setter, or from the
            replay clock if the picked run is not a whole number of steps long."""
        for key in form:
            if self.registry.get(key) is None:
                raise ValueError(f"unknown key given to DataManager: '{key}'")

            if self.registry.get(key).dao.fn_type != SETTER_TYPE:
                raise ValueError(f"'{key}' references a getter. not a setter.")

        self._latest_req_id += 1
        for row in form.get(CURRENT_REQUEST_SETTER, []):
            self._run_picks[self._latest_req_id] = row["request_id"]
        self._request_write.emit(self._latest_req_id, form)
        return self._latest_req_id

    def _update_fields(self, _time=None) -> None:
        """Publish the clock immediately and request a database snapshot.

        Most getters read the selected run independently of the replay position.
        Forecast results are accepted only for the active optimiser interval.
        """
        self._publish_clock_fields()
        self._fetch_pending = True
        self._start_pending_fetch()

    def _start_pending_fetch(self) -> None:
        """Dispatch at most one fetch, retaining one pending refresh."""
        if self._fetch_in_flight is not None or not self._fetch_pending:
            return
        self._fetch_pending = False
        self._latest_req_id += 1
        self._fetch_in_flight = self._latest_req_id
        getters = [
            key
            for key, entry in self.registry.items()
            if entry.dao.fn_type == GETTER_TYPE
        ]
        self._request_fetch.emit(
            self._fetch_in_flight, getters, self.replay_clock.get_time()
        )

    def _finish_fetch(self, request_id: int) -> None:
        """Release the worker slot after success or failure, then catch up."""
        if request_id == self._fetch_in_flight:
            self._fetch_in_flight = None
            self._start_pending_fetch()

    def _handle_result(self, result: Result) -> None:
        if not isinstance(result.data, dict):
            raise ValueError(
                f"DataManager received unexpected result (non-dict): {result.data}"
            )

        operation = result.data.get("operation")
        req_id = result.data.get("request_id")

        if result.status is Status.FAILURE:
            if operation == WRITE_OPERATION and isinstance(req_id, int):
                self._run_picks.pop(req_id, None)
            if operation == FETCH_OPERATION:
                self._finish_fetch(req_id)
            self.result.emit(result)
            return
        elif operation == FETCH_OPERATION:
            if not isinstance(req_id, int):
                raise ValueError(
                    "DataManager received unexpected result ID (non-int): "
                    f"{req_id} from {result.data}"
                )
            try:
                self._apply_fetch(
                    req_id, result.data["results"], result.data.get("replay_time")
                )
            finally:
                self._finish_fetch(req_id)
        elif operation == WRITE_OPERATION:
            if isinstance(req_id, int) and req_id in self._run_picks:
                self._point_clock_at_run(self._run_picks.pop(req_id))
            self._update_fields()

        self.result.emit(result)

    def _apply_fetch(
        self,
        req_id: int,
        results: dict[str, list[tuple]],
        replay_time: datetime | None = None,
    ) -> None:
        """Replace complete DAO snapshots on the GUI thread unless
        superseded, then point the clock at a run the last pick named. A
        just-finished simulation's run only reaches the buffers here, so the
        clock can only be given its window once this refetch has landed."""
        if req_id != self._latest_req_id:
            return

        changed = set()
        clock = self.replay_clock
        for key, records in results.items():
            if key == FORECAST_GETTER and replay_time is not None:
                fetched_issue = forecast_issue_at(
                    clock.start_time, clock.end_time, replay_time
                )
                active_issue = forecast_issue_at(
                    clock.start_time, clock.end_time, clock.get_time()
                )
                if fetched_issue != active_issue:
                    continue
            if self.get_buffer(key).replace(records):
                changed.add(key)
        self._point_pending_run()
        if changed:
            self.updated_keys.emit(changed)

    def close(self) -> None:
        if hasattr(self, "fetcher_thread") and self.fetcher_thread.isRunning():
            self.fetcher_thread.quit()
            self.fetcher_thread.wait()

    def __del__(self) -> None:
        self.close()
