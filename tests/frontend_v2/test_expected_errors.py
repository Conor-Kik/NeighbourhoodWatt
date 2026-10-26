"""Every error path the user can hit in the frontend, one test per path.

Each test asserts the exact message the class raises, so a failure names the
offending value instead of only the exception type. The messages live in
data_manager.py, buffer.py, replay_clock.py, bindings.py and
selectors/common.py.
"""

import re
from datetime import datetime

import pytest
from PyQt6.QtCore import QMutex, QObject, QWaitCondition

from frontend_v2.bindings.bindings import ReadBinding, WriteBinding
from frontend_v2.bindings.selectors.common import scenario_getter
from frontend_v2.engine.data_manager import (
    FETCH_OPERATION,
    GETTER_TYPE,
    BufferEntry,
    DaoEntry,
    DataManager,
)
from frontend_v2.sources.replay_clock import ReplayClock
from frontend_v2.utils.buffer import Buffer, RawBuffer, SparseBuffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status

BUFFER_DTYPE = [("id", "i4"), ("value", "i4")]


class FakeFetcher:
    """The single attribute _build_registry reads: the DAO method registry."""

    def __init__(self, registry: dict[str, DaoEntry]):
        self.registry = registry


def make_manager() -> DataManager:
    """A DataManager wired by hand, so no thread or database is needed."""
    manager = DataManager.__new__(DataManager)
    QObject.__init__(manager)
    manager._mutex = QMutex()
    manager._wait_cond = QWaitCondition()
    manager._ui = {}
    manager._latest_req_id = 0
    manager.replay_clock = ReplayClock()
    manager.registry = {}
    return manager


def make_clock(max_t: int = 10) -> ReplayClock:
    return ReplayClock(start_time=datetime(2026, 10, 11), max_t=max_t)


def register_getter(manager: DataManager) -> None:
    """Put one well-formed getter on the manager, so set/set_ui can reach the
    checks that only run for a known key."""
    manager.registry["get_values"] = BufferEntry(
        Buffer(BUFFER_DTYPE, "id"), DaoEntry(lambda: [], BUFFER_DTYPE, GETTER_TYPE)
    )


# --- DataManager -----------------------------------------------------------------


def test_get_raises_the_message_for_an_unknown_key():
    manager = make_manager()

    with pytest.raises(
        ValueError, match=re.escape('Unknown key given to DataManager: "get_missing"')
    ):
        manager.get("get_missing")


def test_get_record_raises_the_message_for_an_unknown_key():
    manager = make_manager()

    with pytest.raises(
        ValueError, match=re.escape('Unknown key given to DataManager: "get_missing"')
    ):
        manager.get_record("get_missing")


def test_get_raises_the_timeout_message_for_a_ui_key_that_never_arrives():
    manager = make_manager()

    with pytest.raises(
        KeyError, match=re.escape("Timed out waiting for key: 'selected_value'")
    ):
        manager.get("selected_value", timeout_ms=1)


def test_set_raises_the_message_for_an_unknown_key():
    manager = make_manager()

    with pytest.raises(
        ValueError,
        match=re.escape("unknown key given to DataManager: 'set_missing'"),
    ):
        manager.set({"set_missing": [{"value": 1}]})


def test_set_raises_the_message_when_the_key_is_a_getter():
    manager = make_manager()
    register_getter(manager)

    with pytest.raises(
        ValueError, match=re.escape("'get_values' references a getter. not a setter.")
    ):
        manager.set({"get_values": [{"value": 1}]})


def test_set_ui_raises_the_message_for_a_database_key():
    manager = make_manager()
    register_getter(manager)

    with pytest.raises(
        ValueError, match=re.escape('"get_values" is a DB key, not a UI key.')
    ):
        manager.set_ui("get_values", 7)


def test_build_registry_raises_the_message_for_a_getter_without_a_dtype():
    manager = make_manager()
    manager.fetcher = FakeFetcher(
        {"get_values": DaoEntry(lambda: [], None, GETTER_TYPE)}
    )

    with pytest.raises(
        ValueError,
        match=re.escape("FrontendDao getter has no dtype: 'get_values'"),
    ):
        manager._build_registry()


def test_build_registry_raises_the_message_for_a_getter_without_a_pk():
    manager = make_manager()
    manager.fetcher = FakeFetcher(
        {"get_values": DaoEntry(lambda: [], BUFFER_DTYPE, GETTER_TYPE, pk=None)}
    )

    with pytest.raises(
        ValueError, match=re.escape("FrontendDao getter has no pk: 'get_values'")
    ):
        manager._build_registry()


def test_handle_result_raises_the_message_for_a_result_without_a_dictionary():
    manager = make_manager()

    with pytest.raises(
        ValueError,
        match=re.escape("DataManager received unexpected result (non-dict): []"),
    ):
        manager._handle_result(Result(Status.SUCCESS, data=[]))


def test_handle_result_raises_the_message_for_a_non_integer_fetch_id():
    manager = make_manager()
    result = Result(
        Status.SUCCESS,
        data={"operation": FETCH_OPERATION, "request_id": "wrong", "results": {}},
    )

    with pytest.raises(
        ValueError,
        match=re.escape(
            "DataManager received unexpected result ID (non-int): wrong from "
            "{'operation': 'fetch', 'request_id': 'wrong', 'results': {}}"
        ),
    ):
        manager._handle_result(result)


# --- Buffer ----------------------------------------------------------------------


def test_sparse_buffer_raises_the_message_for_a_stride_below_one():
    with pytest.raises(
        ValueError, match=re.escape("SparseBuffer needs a positive stride, got 0")
    ):
        SparseBuffer(BUFFER_DTYPE).set_stride(0)


def test_buffer_raises_the_message_for_a_primary_key_that_is_not_leading():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Primary key must be the leading dtype fields: got ('value',), "
            "leading fields are ('id',)"
        ),
    ):
        Buffer(BUFFER_DTYPE, "value")


def test_buffer_raises_the_message_for_a_primary_key_not_in_the_dtype():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Primary key must be the leading dtype fields: got ('bogus',), "
            "leading fields are ('id',)"
        ),
    ):
        Buffer(BUFFER_DTYPE, "bogus")


def test_buffer_raises_the_message_for_a_record_with_the_wrong_field_count():
    buffer = Buffer(BUFFER_DTYPE, "id")

    with pytest.raises(
        ValueError, match=re.escape("Record 0 expected 2 fields, got 3")
    ):
        buffer.add([(1, 2, 3)])


def test_raw_buffer_raises_the_message_for_a_pop_past_the_end():
    raw = RawBuffer(BUFFER_DTYPE)
    raw.extend([(1, 2)])

    with pytest.raises(
        IndexError, match=re.escape("cannot pop 2 record(s) at index 0 from 1 stored")
    ):
        raw.pop(0, 2)


# --- ReplayClock -----------------------------------------------------------------


def test_seek_raises_the_message_above_the_last_step():
    with pytest.raises(
        ValueError,
        match=re.escape("Cannot seek less than 0, or beyond max t: t = 10"),
    ):
        make_clock().seek(10)


def test_seek_raises_the_message_below_zero():
    with pytest.raises(
        ValueError,
        match=re.escape("Cannot seek less than 0, or beyond max t: t = -1"),
    ):
        make_clock().seek(-1)


def test_set_t_optimiser_raises_the_message_outside_the_replay():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Cannot record an optimiser step outside the replay: t = 11, max t = 10"
        ),
    ):
        make_clock().set_t_optimiser(11)


def test_set_day_raises_the_message_when_not_midnight():
    with pytest.raises(
        ValueError,
        match=re.escape("Expected start of day at 00:00:00, instead got: 09:30:00"),
    ):
        make_clock().set_day(datetime(2026, 10, 11, 9, 30))


def test_set_run_raises_the_message_when_it_ends_before_it_starts():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Run must end after it starts: start = 2026-10-11T01:00:00+00:00, "
            "end = 2026-10-11T00:00:00+00:00"
        ),
    ):
        make_clock().set_run("2026-10-11T01:00:00+00:00", "2026-10-11T00:00:00+00:00")


def test_set_run_raises_the_message_when_it_is_not_whole_steps():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Run must last whole steps of 5 minutes: "
            "start = 2026-10-11T00:00:00+00:00, end = 2026-10-11T00:07:00+00:00"
        ),
    ):
        make_clock().set_run("2026-10-11T00:00:00+00:00", "2026-10-11T00:07:00+00:00")


def test_step_of_raises_the_message_outside_the_replay():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Timestamp falls outside the replay: 2026-10-12 00:00:00+00:00, "
            "replay runs "
            "2026-10-11 00:00:00+00:00 to 2026-10-11 00:50:00+00:00"
        ),
    ):
        make_clock().step_of(datetime(2026, 10, 12))


# --- Selectors -------------------------------------------------------------------


def test_scenario_getter_raises_the_message_for_an_unknown_name():
    with pytest.raises(ValueError, match=re.escape('Unknown scenario name: "bogus"')):
        scenario_getter("bogus")


# --- Bindings --------------------------------------------------------------------


def test_read_binding_raises_the_message_when_the_select_has_no_needs():
    def select(data: dict) -> dict:
        return data

    with pytest.raises(
        ValueError,
        match=re.escape(
            "ReadBinding given a select function with no @needs when it's required"
        ),
    ):
        ReadBinding(select)


def test_write_binding_raises_the_message_for_a_result_without_a_dictionary():
    binding = WriteBinding(make_manager(), "set_value")

    with pytest.raises(
        ValueError, match=re.escape("WriteBinding given a result with no dictionary")
    ):
        binding._handle_result(Result(Status.SUCCESS, data=[]))
