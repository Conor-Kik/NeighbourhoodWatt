from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QMutex, QObject, QWaitCondition

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
    DataFetcher,
)
from frontend_v2.sources.replay_clock import (
    DEFAULT_MAX_T,
    STEP_DURATION,
    UNSET_START_TIME,
    UNSET_T_OPTIMISER,
    ReplayClock,
)
from frontend_v2.utils.buffer import Buffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status

# Columns of get_all_simulation_requests the replay window is read from
RUN_DTYPE = [
    ("request_id", "U16"),
    ("start_time_utc", "U32"),
    ("end_time_utc", "U32"),
]


def make_manager():
    from frontend_v2.engine.data_manager import DataManager

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
    manager.registry = {}
    # noinspection bad-argument-type
    manager.registry = {
        "get_values": BufferEntry(
            Buffer([("id", "i4"), ("value", "i4")], "id"),
            DaoEntry(lambda: [], None, GETTER_TYPE),
        ),
        "get_all_simulation_requests": BufferEntry(
            Buffer(RUN_DTYPE, "request_id"),
            DaoEntry(lambda: [], RUN_DTYPE, GETTER_TYPE, ("request_id",)),
        ),
        "set_value": BufferEntry(
            None,
            DaoEntry(lambda row: None, None, SETTER_TYPE),
        ),
        "set_current_request": BufferEntry(
            None,
            DaoEntry(lambda row: None, None, SETTER_TYPE),
        ),
    }
    return manager


def add_run(manager, request_id: str, start_time_utc: str, end_time_utc: str) -> None:
    """Put one saved run in the manager's run buffer, as a fetch would."""
    manager.get_buffer("get_all_simulation_requests").add(
        [(request_id, start_time_utc, end_time_utc)]
    )


def finish_write(manager, request_id):
    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={"operation": WRITE_OPERATION, "request_id": request_id},
        )
    )


def test_fetch_emits_success_result_with_operation_metadata():
    fetcher = DataFetcher.__new__(DataFetcher)
    DataFetcher.__init__(fetcher)
    fetcher._registry = {
        "get_values": DaoEntry(lambda: [("value",)], None, GETTER_TYPE)
    }
    emitted = []
    fetcher.result.connect(emitted.append)

    fetcher.fetch(7, ["get_values"])

    result = emitted[0]
    assert isinstance(result, Result)
    assert result.status is Status.SUCCESS
    assert result.data == {
        "operation": FETCH_OPERATION,
        "request_id": 7,
        "results": {"get_values": [("value",)]},
    }


def test_write_emits_success_result_after_all_rows_are_written():
    written = []
    fetcher = DataFetcher.__new__(DataFetcher)
    DataFetcher.__init__(fetcher)
    fetcher._registry = {"set_value": DaoEntry(written.append, None, SETTER_TYPE)}
    emitted = []
    fetcher.result.connect(emitted.append)

    fetcher.write(8, {"set_value": [{"value": 1}, {"value": 2}]})

    assert written == [{"value": 1}, {"value": 2}]
    assert emitted[0].status is Status.SUCCESS
    assert emitted[0].data == {
        "operation": WRITE_OPERATION,
        "request_id": 8,
    }


def test_get_returns_buffer_for_valid_getter_and_emits_updated_keys_on_fetch():
    manager = make_manager()
    updated = []
    manager.updated_keys.connect(updated.append)
    records = [(1, 42)]

    manager._apply_fetch(0, {"get_values": records})

    assert manager.get("get_values").tolist() == records
    assert updated == [{"get_values"}]


def test_get_raises_for_unknown_key():
    manager = make_manager()

    with pytest.raises(
        ValueError, match='Unknown key given to DataManager: "get_missing"'
    ):
        manager.get("get_missing")


def test_full_fetch_does_not_repeatedly_thin_history():
    manager = make_manager()
    records = [(i, i) for i in range(2304)]
    manager._apply_fetch(0, {"get_values": records})
    expected = manager.get("get_values").copy()
    for _ in range(12):
        manager._apply_fetch(0, {"get_values": records})
        assert manager.get("get_values").tolist() == expected.tolist()


def test_fetch_removes_rows_no_longer_in_the_database():
    manager = make_manager()
    manager._apply_fetch(0, {"get_values": [(1, 2), (3, 4)]})
    manager._apply_fetch(0, {"get_values": [(3, 5)]})
    assert manager.get("get_values").tolist() == [(3, 5)]
    manager._apply_fetch(0, {"get_values": []})
    assert manager.get("get_values").tolist() == []


def test_get_ui_returns_existing_value_and_none_for_unset_value():
    manager = make_manager()

    assert manager.get("selected_value") is None
    manager.set_ui("selected_value", 7)

    assert manager.get("selected_value") == 7


def test_set_ui_creates_and_modifies_value_and_emits_updated_keys():
    manager = make_manager()
    updated = []
    manager.updated_keys.connect(updated.append)

    assert manager.set_ui("selected_value", 7) is Status.CREATED
    assert manager.set_ui("selected_value", 8) is Status.MODIFIED
    assert manager.get("selected_value") == 8
    assert updated == [{"selected_value"}, {"selected_value"}]


def test_set_ui_raises_for_database_key():
    manager = make_manager()

    with pytest.raises(ValueError, match="is a DB key"):
        manager.set_ui("get_values", 7)


def test_set_queues_valid_setter():
    manager = make_manager()
    requests = []
    manager._request_write.connect(
        lambda request_id, write_form: requests.append((request_id, write_form))
    )

    form = {"set_value": [{"value": 3}]}
    manager.set(form)

    assert requests == [(1, form)]


def test_set_refresh_emits_updated_keys_after_successful_write():
    manager = make_manager()
    updated = []
    requests = []
    manager.updated_keys.connect(updated.append)
    manager._request_write.connect(
        lambda request_id, write_form: requests.append((request_id, write_form))
    )

    form = {"set_value": [{"value": 3}]}
    manager.set(form)
    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={"operation": WRITE_OPERATION, "request_id": requests[0][0]},
        )
    )
    manager._apply_fetch(2, {"get_values": [(1, 3)]})

    assert updated == [
        {UI_TIME_KEY},
        {UI_DURATION_KEY},
        {UI_OPTIMISER_KEY},
        {"get_values"},
    ]


def test_set_raises_for_unknown_key():
    manager = make_manager()

    with pytest.raises(ValueError, match="unknown key given"):
        manager.set({"missing": [{"value": 3}]})


def test_set_raises_when_given_getter():
    manager = make_manager()

    with pytest.raises(ValueError, match="references a getter"):
        manager.set({"get_values": [{"id": 1, "value": 3}]})


def test_handle_result_raises_for_non_dict_data():
    manager = make_manager()

    with pytest.raises(ValueError, match="non-dict"):
        manager._handle_result(Result(Status.SUCCESS, data=[]))


def test_handle_result_raises_for_non_integer_fetch_request_id():
    manager = make_manager()

    result = Result(
        Status.SUCCESS,
        data={
            "operation": FETCH_OPERATION,
            "request_id": "wrong",
            "results": {},
        },
    )

    with pytest.raises(ValueError, match="non-int"):
        manager._handle_result(result)


def test_update_fields_publishes_clock_fields_as_ui_keys():
    manager = make_manager()
    manager.replay_clock.seek(3)
    manager.replay_clock.set_t_optimiser(2)

    manager._update_fields()

    assert manager.get(UI_TIME_KEY) == manager.replay_clock.get_time()
    assert manager.get(UI_DURATION_KEY) == manager.replay_clock.duration_minutes
    assert manager.get(UI_OPTIMISER_KEY) == manager.replay_clock.t_optimiser


def test_update_fields_emits_ui_keys_before_the_fetch():
    manager = make_manager()
    updated = []
    fetches = []
    manager.updated_keys.connect(updated.append)
    manager._request_fetch.connect(lambda request_id, keys: fetches.append(keys))

    manager._update_fields()

    assert updated == [{UI_TIME_KEY}, {UI_DURATION_KEY}, {UI_OPTIMISER_KEY}]
    assert fetches == [["get_values", "get_all_simulation_requests"]]


def test_seek_publishes_the_new_replay_time():
    manager = make_manager()
    manager.replay_clock.time_changed.connect(manager._update_fields)

    manager.set_time(5)

    assert manager.get(UI_TIME_KEY) == manager.replay_clock.get_time()


def test_select_run_clears_buffers_after_the_write_succeeds():
    manager = make_manager()
    requests = []
    manager._request_write.connect(
        lambda request_id, write_form: requests.append((request_id, write_form))
    )
    manager.get_buffer("get_values").add([(1, 2), (3, 4)])

    request_id = manager.select_run(9)
    assert manager.get("get_values").tolist() == [(1, 2), (3, 4)]
    finish_write(manager, request_id)

    assert len(manager.get_buffer("get_values").get()) == 0
    assert requests == [
        (
            request_id,
            {"set_current_request": [{"request_id": 9}]},
        )
    ]


def test_select_run_raises_when_app_setting_setter_is_unknown():
    manager = make_manager()
    manager.registry.pop("set_current_request")

    with pytest.raises(ValueError, match="unknown key given"):
        manager.select_run(9)


def test_select_run_sends_the_replay_clock_back_to_an_unset_day():
    manager = make_manager()
    manager.replay_clock.set_day(datetime(2026, 10, 11))
    manager.replay_clock.seek(3)
    manager.replay_clock.set_t_optimiser(2)

    finish_write(manager, manager.select_run(9))

    assert manager.replay_clock.start_time == UNSET_START_TIME
    assert manager.replay_clock.t == 0
    assert manager.replay_clock.t_optimiser is UNSET_T_OPTIMISER


def test_select_run_clears_the_replay_keys_the_bar_reads():
    manager = make_manager()
    manager.replay_clock.time_changed.connect(manager._update_fields)
    manager.replay_clock.optimiser_changed.connect(manager._publish_optimiser_field)
    manager.replay_clock.set_day(datetime(2026, 10, 11))
    manager.replay_clock.seek(3)
    manager._update_fields()
    assert manager.get(UI_TIME_KEY) is not None

    finish_write(manager, manager.select_run(9))

    assert manager.get(UI_TIME_KEY) == UNSET_START_TIME
    assert manager.get(UI_OPTIMISER_KEY) is UNSET_T_OPTIMISER


def test_select_run_gives_the_clock_the_window_of_the_run_it_switched_to():
    manager = make_manager()
    add_run(
        manager,
        "9",
        "2026-10-11T00:00:00+00:00",
        "2026-10-13T00:00:00+00:00",
    )

    finish_write(manager, manager.select_run(9))

    assert manager.replay_clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)
    assert manager.replay_clock.duration_minutes == 2 * 24 * 60


def test_select_run_leaves_the_clock_unset_for_a_run_the_buffers_do_not_hold():
    manager = make_manager()

    finish_write(manager, manager.select_run(9))

    assert manager.replay_clock.start_time == UNSET_START_TIME
    assert manager.replay_clock.duration_minutes == DEFAULT_MAX_T * STEP_DURATION


def test_set_of_a_picked_run_points_the_clock_at_its_window():
    """The Settings picker and the simulation form write the current run
    straight through set, so the switch has to move the clock from there too."""
    manager = make_manager()
    add_run(
        manager,
        "9",
        "2026-10-11T00:00:00+00:00",
        "2026-10-12T00:00:00+00:00",
    )

    finish_write(
        manager,
        manager.set({"set_current_request": [{"request_id": "9"}]}),
    )

    assert manager.replay_clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)
    assert manager.replay_clock.duration_minutes == 24 * 60


def test_set_of_another_app_setting_leaves_the_clock_where_it_was():
    manager = make_manager()
    manager.replay_clock.set_day(datetime(2026, 10, 11))
    manager.replay_clock.seek(3)

    manager.set_ui("layout_setup-grid", None)

    assert manager.replay_clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)
    assert manager.replay_clock.t == 3


def test_set_of_a_new_run_points_the_clock_once_the_refetch_lands():
    """A simulation writes the run it just created before that run is in the
    buffers, so the clock only learns its window from the write's own refetch."""
    manager = make_manager()
    written = []
    manager._request_write.connect(lambda req_id, form: written.append(req_id))

    manager.set({"set_current_request": [{"request_id": "9"}]})
    assert manager.replay_clock.start_time == UNSET_START_TIME

    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={"operation": WRITE_OPERATION, "request_id": written[0]},
        )
    )
    manager._apply_fetch(
        manager._latest_req_id,
        {
            "get_all_simulation_requests": [
                ("9", "2026-10-11T00:00:00+00:00", "2026-10-12T00:00:00+00:00")
            ]
        },
    )

    assert manager.replay_clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)
    assert manager.replay_clock.duration_minutes == 24 * 60


def test_set_of_a_picked_run_whose_write_fails_leaves_the_clock_unset():
    """A pick that never reaches the database must not point the clock when a
    later fetch lands, even if that fetch happens to hold the run."""
    manager = make_manager()
    written = []
    manager._request_write.connect(lambda req_id, form: written.append(req_id))

    manager.set({"set_current_request": [{"request_id": "9"}]})
    manager._handle_result(
        Result(
            Status.FAILURE,
            data={"operation": WRITE_OPERATION, "request_id": written[0]},
        )
    )
    manager._apply_fetch(
        manager._latest_req_id,
        {
            "get_all_simulation_requests": [
                ("9", "2026-10-11T00:00:00+00:00", "2026-10-12T00:00:00+00:00")
            ]
        },
    )

    assert manager.replay_clock.start_time == UNSET_START_TIME


def test_failed_known_run_pick_preserves_the_previous_replay_and_buffers():
    manager = make_manager()
    manager.replay_clock.set_day(datetime(2026, 10, 10))
    manager.replay_clock.seek(12)
    manager.get_buffer("get_values").add([(1, 42)])
    add_run(manager, "9", "2026-10-11T00:00:00+00:00", "2026-10-12T00:00:00+00:00")
    request_id = manager.select_run("9")
    manager._handle_result(
        Result(
            Status.FAILURE,
            data={"operation": WRITE_OPERATION, "request_id": request_id},
        )
    )
    assert manager.replay_clock.start_time == datetime(2026, 10, 10, tzinfo=UTC)
    assert manager.replay_clock.t == 12
    assert manager.get("get_values").tolist() == [(1, 42)]


def test_known_run_pick_cancels_an_older_pending_run():
    manager = make_manager()
    manager._pending_run = "old-missing"
    add_run(manager, "9", "2026-10-11T00:00:00+00:00", "2026-10-12T00:00:00+00:00")
    finish_write(manager, manager.select_run("9"))
    assert manager._pending_run is None
    assert manager.replay_clock.start_time == datetime(2026, 10, 11, tzinfo=UTC)


def test_replay_burst_keeps_one_fetch_and_one_pending_refresh():
    manager = make_manager()
    fetches = []
    manager._request_fetch.connect(lambda req, keys: fetches.append(req))
    for _ in range(20):
        manager._update_fields()
    assert fetches == [1]
    assert manager._fetch_pending
    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={
                "operation": FETCH_OPERATION,
                "request_id": 1,
                "results": {"get_values": [(1, 42)]},
            },
        )
    )
    assert fetches == [1, 2]
    assert manager.get("get_values").tolist() == [(1, 42)]
    assert not manager._fetch_pending


def test_failed_fetch_releases_slot_and_dispatches_pending_refresh():
    manager = make_manager()
    fetches = []
    manager._request_fetch.connect(lambda req, keys: fetches.append(req))
    manager._update_fields()
    manager._update_fields()
    manager._handle_result(
        Result(
            Status.FAILURE,
            data={
                "operation": FETCH_OPERATION,
                "request_id": 1,
            },
        )
    )
    assert fetches == [1, 2]
    assert manager._fetch_in_flight == 2


def test_identical_snapshots_do_not_announce_database_changes():
    manager = make_manager()
    updates = []
    manager.updated_keys.connect(updates.append)
    manager._apply_fetch(0, {"get_values": [(1, 42)]})
    manager._apply_fetch(0, {"get_values": [(1, 42)]})
    assert updates == [{"get_values"}]
    manager.clear_buffers()
    manager._apply_fetch(0, {"get_values": [(1, 42)]})
    assert updates == [{"get_values"}, {"get_values"}]


def test_write_supersedes_in_flight_fetch_without_losing_post_write_refresh():
    manager = make_manager()
    manager._update_fields()
    request_id = manager.set({"set_value": [{"value": 5}]})
    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={
                "operation": FETCH_OPERATION,
                "request_id": 1,
                "results": {"get_values": [(1, 42)]},
            },
        )
    )
    assert manager.get("get_values").tolist() == []
    manager._handle_result(
        Result(
            Status.SUCCESS,
            data={
                "operation": WRITE_OPERATION,
                "request_id": request_id,
            },
        )
    )
    assert manager._fetch_in_flight == 3


@pytest.mark.parametrize(
    "fetched_step,active_step,accepted", [(0, 5, True), (0, 6, False), (6, 0, False)]
)
def test_forecast_results_follow_interval_without_discarding_other_getters(
    fetched_step, active_step, accepted
):
    from frontend_v2.engine.data_manager import FORECAST_GETTER

    manager = make_manager()
    manager.registry[FORECAST_GETTER] = BufferEntry(
        Buffer([("id", "i4"), ("value", "i4")], "id"),
        DaoEntry(lambda: [], None, GETTER_TYPE),
    )
    manager.replay_clock.set_run(
        "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
    )
    manager.replay_clock.seek(fetched_step)
    fetched_at = manager.replay_clock.get_time()
    manager.replay_clock.seek(active_step)
    manager._apply_fetch(
        0, {FORECAST_GETTER: [(1, 7)], "get_values": [(1, 42)]}, fetched_at
    )
    assert manager.get("get_values").tolist() == [(1, 42)]
    assert manager.get(FORECAST_GETTER).tolist() == ([(1, 7)] if accepted else [])


@pytest.mark.parametrize(
    "getter", ["get_current_forecast_data", "get_current_replay_cost"]
)
def test_worker_passes_replay_context_only_to_replay_getters(getter):
    fetcher = DataFetcher()
    moments = []
    fetcher._registry = {
        getter: DaoEntry(
            lambda moment: moments.append(moment) or [], None, GETTER_TYPE
        ),
        "get_values": DaoEntry(lambda: [(1, 42)], None, GETTER_TYPE),
    }
    emitted = []
    fetcher.result.connect(emitted.append)
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    fetcher.fetch(7, [getter, "get_values"], moment)
    assert moments == [moment]
    assert emitted[0].data["replay_time"] == moment
    assert emitted[0].data["results"]["get_values"] == [(1, 42)]


@pytest.mark.parametrize("fails", [False, True])
def test_balanced_credit_is_memoised_in_worker_and_failures_are_unavailable(
    monkeypatch, fails
):
    from backend_v2.community_optimiser.terminal_value import TerminalValue
    from frontend_v2.engine import data_manager as module
    from tests.factories import SimulationRequestFactory

    request = SimulationRequestFactory.build(objective_mode="Balanced")
    fetcher = DataFetcher()
    fetcher.dao = MagicMock()
    fetcher.dao.run_dao.get_all_simulation_requests.return_value = [request]
    engine = MagicMock()
    factory = MagicMock(return_value=engine)
    if fails:
        engine.estimate_saved_balanced_terminal_value.side_effect = RuntimeError(
            "no forecast"
        )
    else:
        engine.estimate_saved_balanced_terminal_value.return_value = TerminalValue(
            3.5, 2, 0.01, 1.5
        )
    monkeypatch.setattr(module, "SimulationEngine", factory)
    summary = {
        "request_id": request.request_id,
        "terminal_soc_kwh": 5.0,
        module.BALANCED_CREDIT: float("nan"),
        "unavailable": float("nan"),
    }
    first = fetcher._saved_balanced_credit(summary)
    summary["unavailable"] = float("nan")
    second = fetcher._saved_balanced_credit(summary)
    if fails:
        assert all(value != value for estimate in (first, second) for value in estimate)
    else:
        assert first == second == TerminalValue(3.5, 2, 0.01, 1.5)
    factory.assert_called_once()
    engine.dao.close.assert_called_once()
    engine.results_dao.close.assert_called_once()
    summary["terminal_soc_kwh"] = 6
    fetcher._saved_balanced_credit(summary)
    assert factory.call_count == 2
