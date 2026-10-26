import json

from PyQt6.QtCore import QSettings

from frontend_v2.engine.data_manager import DataManager
from frontend_v2.sources.replay_clock import UNSET_START_TIME


def test_duration_survives_restart_and_obsolete_layout_is_ignored(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(DataManager, "_build_fetcher_conns", lambda self: None)
    path = str(tmp_path / "preferences.ini")
    preferences = QSettings(path, QSettings.Format.IniFormat)
    manager = DataManager(preferences=preferences)
    layout = [{"id": "panel", "row": 0, "col": 1, "width": 1, "height": 1}]
    preferences.setValue("layout_test", json.dumps(layout))
    manager.set_ui("time_duration", "7d")
    preferences.sync()
    restarted = DataManager(preferences=QSettings(path, QSettings.Format.IniFormat))
    assert restarted.get("layout_test") is None
    assert preferences.contains("layout_test")
    assert restarted.get("time_duration") == "7d"
    assert restarted.replay_clock.start_time == UNSET_START_TIME
