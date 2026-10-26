"""Cache controls confirm deletion and coordinate with running simulations."""

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

from backend_v2.aemo import cache_storage
from frontend_v2.engine import cache_manager
from frontend_v2.engine.optimiser_gateway import OptimiserGateway
from frontend_v2.sources.replay_clock import ReplayClock
from frontend_v2.ui.pages.setup.cache_controls import CacheControls
from tests.frontend_v2.ui.test_settings_page import APP


def settle(widget):
    for _ in range(100):
        APP.processEvents()
        if not widget.manager.busy:
            return
        QTest.qWait(10)
    pytest.fail("Cache operation did not finish")


@pytest.fixture
def controls(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "aemo"
    root.mkdir()
    (root / "archive.zip").write_bytes(b"download" * 100)
    monkeypatch.setattr(cache_storage, "AEMO_CACHE_DIR", root)
    database = tmp_path / "app.db"
    database.write_bytes(b"d" * 128)
    widget = CacheControls(OptimiserGateway(str(database), ReplayClock()))
    widget.show()
    settle(widget)
    yield widget, root
    widget.manager.close()
    APP.processEvents()
    widget.close()


def test_cancel_preserves_files_and_confirm_clears_only_cache(controls, monkeypatch):
    widget, root = controls
    assert widget._size == 800
    assert "128" in widget.database_label.text()
    assert widget.clear_button.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel
    )
    widget.clear_button.click()
    assert (root / "archive.zip").exists()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    widget.clear_button.click()
    assert not widget.clear_button.isEnabled()
    settle(widget)
    assert widget._size == 0
    assert not (root / "archive.zip").exists()
    assert not widget.gateway._cache_busy
    assert "128" in widget.database_label.text()
    assert root.parent.joinpath("app.db").read_bytes() == b"d" * 128


def test_simulation_blocks_cleanup_and_cleanup_blocks_start(controls):
    widget, root = controls
    widget.gateway.is_running = True
    widget.gateway.simulation_state_changed.emit(True)
    assert not widget.clear_button.isEnabled()
    widget.manager.clear()  # Recheck even if a simulation starts during confirmation.
    assert "simulation" in widget.status.text()
    assert (root / "archive.zip").exists()
    widget.gateway.is_running = False
    widget.gateway.simulation_state_changed.emit(False)
    widget.gateway.set_cache_busy(True)
    errors = []
    widget.gateway.simulation_error.connect(errors.append)
    widget.gateway.start_simulation({})
    assert errors == ["Wait for AEMO cache cleanup to finish."]
    assert not widget.gateway.is_running
    widget.gateway.set_cache_busy(False)


def test_reset_popup_cancel_preserves_data_confirm_schedules_then_closes(
    controls, monkeypatch
):
    widget, root = controls
    scheduled = []
    closed = []
    monkeypatch.setattr(cache_manager, "schedule_database_reset", scheduled.append)
    widget.manager.reset_scheduled.disconnect()
    widget.manager.reset_scheduled.connect(lambda: closed.append(True))
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Cancel
    )
    widget.reset_button.click()
    assert not scheduled and not closed
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Yes
    )
    widget.reset_button.click()
    assert scheduled == [widget.gateway.db_path]
    assert closed == [True]
    assert (root / "archive.zip").exists()
    assert root.parent.joinpath("app.db").read_bytes() == b"d" * 128


def test_reset_is_blocked_during_simulation_and_write_failure_does_not_close(
    controls, monkeypatch
):
    widget, _ = controls
    widget.gateway.is_running = True
    widget.gateway.simulation_state_changed.emit(True)
    assert not widget.reset_button.isEnabled()
    widget.manager.reset_on_next_launch()
    assert "simulation" in widget.status.text()
    widget.gateway.is_running = False
    widget.gateway.simulation_state_changed.emit(False)
    closed = []
    widget.manager.reset_scheduled.disconnect()
    widget.manager.reset_scheduled.connect(lambda: closed.append(True))

    def fail(_):
        raise OSError("Could not save reset request")

    monkeypatch.setattr(cache_manager, "schedule_database_reset", fail)
    widget.manager.reset_on_next_launch()
    assert "Could not save" in widget.status.text()
    assert not closed and not widget.gateway._cache_busy


def test_large_sizes_are_not_truncated_to_qt_32_bit_integers(controls, monkeypatch):
    widget, _ = controls
    monkeypatch.setattr(cache_manager, "aemo_cache_size", lambda: 5_275_411_789)
    widget.manager.refresh()
    settle(widget)
    assert widget._size == 5_275_411_789
    assert "5.28" in widget.size_label.text()


def test_background_failure_restores_controls_and_preserves_files(
    controls, monkeypatch
):
    widget, root = controls

    def fail():
        raise PermissionError("File is in use")

    monkeypatch.setattr(cache_manager, "clear_aemo_cache", fail)
    widget.manager.clear()
    settle(widget)
    assert "File is in use" in widget.status.text()
    assert not widget.gateway._cache_busy
    assert widget.refresh_button.isEnabled()
    assert (root / "archive.zip").exists()
