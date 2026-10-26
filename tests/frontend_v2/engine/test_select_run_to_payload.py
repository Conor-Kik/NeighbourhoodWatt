"""End-to-end: pick a saved run, let the fetcher answer, read the selectors.

Drives a DataManager the way the app does at runtime: buffers are seeded with
the saved runs, selecting one writes the picked run back through the fetcher,
the fetch the write triggers refills every buffer, and the selectors the
Operation page binds to turn those buffers into payloads.
"""

from datetime import UTC, datetime

from PyQt6.QtCore import QMutex, QObject, QWaitCondition

from frontend_v2.bindings.bindings import ReadBinding
from frontend_v2.bindings.selectors.tabular import (
    select_current_replay_time,
    select_replay_state,
)
from frontend_v2.engine.data_manager import (
    FETCH_OPERATION,
    GETTER_TYPE,
    SETTER_TYPE,
    UI_DURATION_KEY,
    UI_OPTIMISER_KEY,
    UI_TIME_KEY,
    WRITE_OPERATION,
    BufferEntry,
    DaoEntry,
    DataManager,
)
from frontend_v2.sources.replay_clock import ReplayClock
from frontend_v2.utils.buffer import Buffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status

RUN_DTYPE = [
    ("request_id", "U16"),
    ("location_name", "U32"),
    ("start_time_utc", "U32"),
    ("end_time_utc", "U32"),
]
LOCATION_DTYPE = [("location_name", "U32"), ("timezone", "U32")]
SETTING_DTYPE = [("row", "i8"), ("request_id", "U16")]

RUN_ID = "run-1"
LOCATION = "Brisbane"
TIMEZONE = "Australia/Brisbane"
RUN_START = "2026-10-11T08:00:00+00:00"
RUN_END = "2026-10-12T08:00:00+00:00"
RUN_MINUTES = 24 * 60


def make_manager() -> DataManager:
    """A DataManager wired by hand, so no thread or database is needed."""
    manager = DataManager.__new__(DataManager)
    QObject.__init__(manager)
    manager._mutex = QMutex()
    manager._wait_cond = QWaitCondition()
    manager._ui = {}
    manager._latest_req_id = 0
    manager._pending_run = None
    manager._run_picks = {}

    manager._fetch_in_flight = None
    manager._fetch_pending = False
    manager.replay_clock = ReplayClock()
    manager.registry = {
        "get_all_simulation_requests": BufferEntry(
            Buffer(RUN_DTYPE, "request_id"),
            DaoEntry(lambda: [], RUN_DTYPE, GETTER_TYPE, ("request_id",)),
        ),
        "get_all_locations": BufferEntry(
            Buffer(LOCATION_DTYPE, "location_name"),
            DaoEntry(lambda: [], LOCATION_DTYPE, GETTER_TYPE, ("location_name",)),
        ),
        "get_current_request": BufferEntry(
            Buffer(SETTING_DTYPE, "row"),
            DaoEntry(lambda: [], SETTING_DTYPE, GETTER_TYPE, ("row",)),
        ),
        "set_current_request": BufferEntry(
            None, DaoEntry(lambda row: None, None, SETTER_TYPE)
        ),
    }
    return manager


def serve_fetches(manager: DataManager, database: dict[str, list[tuple]]) -> None:
    """Stand in for the fetcher thread: answer each request from `database`,
    a dict of getter key to the rows the DAO would return. A write is applied to
    the settings in `database` the way FrontendDao stores them, as JSON text."""

    def answer_fetch(req_id: int, keys: list[str]) -> None:
        manager._handle_result(
            Result(
                Status.SUCCESS,
                data={
                    "operation": FETCH_OPERATION,
                    "request_id": req_id,
                    "results": {key: database.get(key, []) for key in keys},
                },
            )
        )

    def answer_write(req_id: int, form: dict[str, list[dict]]) -> None:
        database["get_current_request"] = [
            (0, row["request_id"]) for row in form["set_current_request"]
        ]
        manager._handle_result(
            Result(
                Status.SUCCESS,
                data={"operation": WRITE_OPERATION, "request_id": req_id},
            )
        )

    manager._request_fetch.connect(answer_fetch)
    manager._request_write.connect(answer_write)


def needed_values(manager: DataManager, selector) -> dict:
    """The selector's declared keys with their values, as a Grid builds them."""
    return {key: manager.get(key) for key in selector.needs}


def test_initial_fetch_leaves_the_replay_unset_until_a_run_is_picked():
    manager = make_manager()
    manager._apply_fetch(
        0,
        {
            "get_all_simulation_requests": [(RUN_ID, LOCATION, RUN_START, RUN_END)],
            "get_current_request": [],
        },
    )
    assert manager.replay_clock.start_time.year == 1


def test_selecting_a_saved_run_fills_the_buffers_the_selectors_read():
    manager = make_manager()
    database = {
        "get_all_simulation_requests": [(RUN_ID, LOCATION, RUN_START, RUN_END)],
        "get_all_locations": [(LOCATION, TIMEZONE)],
    }
    serve_fetches(manager, database)

    # The run list is on screen before anything is picked, so its buffer is full.
    manager._apply_fetch(0, database)

    manager.select_run(RUN_ID)

    assert manager.get("get_all_simulation_requests").tolist() == [
        (RUN_ID, LOCATION, RUN_START, RUN_END)
    ]
    assert manager.get("get_all_locations").tolist() == [(LOCATION, TIMEZONE)]
    assert manager.get("get_current_request").tolist() == [(0, RUN_ID)]
    assert manager.replay_clock.get_time() == datetime.fromisoformat(RUN_START)
    assert manager.replay_clock.duration_minutes == RUN_MINUTES


def test_selecting_a_saved_run_returns_the_payloads_the_page_shows():
    manager = make_manager()
    database = {
        "get_all_simulation_requests": [(RUN_ID, LOCATION, RUN_START, RUN_END)],
        "get_all_locations": [(LOCATION, TIMEZONE)],
    }
    serve_fetches(manager, database)
    manager._apply_fetch(0, database)
    manager.select_run(RUN_ID)

    replay_time = ReadBinding(select_current_replay_time).translate(
        needed_values(manager, select_current_replay_time)
    )
    assert replay_time.rows == [("2026-10-11 18:00",)]

    replay_state = ReadBinding(select_replay_state).translate(
        needed_values(manager, select_replay_state)
    )
    assert replay_state.rows == [
        (datetime(2026, 10, 11, 8, 0, tzinfo=UTC), RUN_MINUTES, None)
    ]
    assert manager.get(UI_TIME_KEY) == datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
    assert manager.get(UI_DURATION_KEY) == RUN_MINUTES
    assert manager.get(UI_OPTIMISER_KEY) is None
