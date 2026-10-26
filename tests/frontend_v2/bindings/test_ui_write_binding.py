from frontend_v2.bindings.bindings import UiWriteBinding
from frontend_v2.utils.status import Status
from tests.frontend_v2.engine.test_data_manager import make_manager


def test_chart_duration_updates_frontend_state_without_a_database_write():
    manager = make_manager()
    writes = []
    manager._request_write.connect(lambda *args: writes.append(args))
    binding = UiWriteBinding(manager)
    results = []
    binding.result.connect(results.append)
    binding.submit({"key": "time_duration", "value": "7d"})
    assert manager.get("time_duration") == "7d"
    assert writes == []
    assert results[0].status is Status.SUCCESS
