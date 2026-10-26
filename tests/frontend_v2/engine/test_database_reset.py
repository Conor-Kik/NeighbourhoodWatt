"""Reset only a confirmed database on a later startup, never during scheduling."""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings

from frontend_v2.engine import database_reset


def test_schedule_leaves_data_untouched_then_startup_restores_defaults(
    test_db, tmp_path
):
    settings_path = str(tmp_path / "preferences.ini")
    preferences = QSettings(settings_path, QSettings.Format.IniFormat)
    with closing(sqlite3.connect(test_db)) as connection, connection:
        connection.execute(
            "INSERT INTO location VALUES "
            "('Custom', -27, 153, 'Australia/Brisbane', 'QLD1')"
        )
    cache = tmp_path / "aemo.zip"
    cache.write_bytes(b"archive")
    database_reset.schedule_database_reset(test_db, preferences)
    with closing(sqlite3.connect(test_db)) as connection:
        assert connection.execute("SELECT location_name FROM location").fetchall() == [
            ("Custom",)
        ]
    restarted = QSettings(settings_path, QSettings.Format.IniFormat)
    assert database_reset.apply_pending_database_reset(test_db, restarted)
    with closing(sqlite3.connect(test_db)) as connection:
        names = {
            row[0] for row in connection.execute("SELECT location_name FROM location")
        }
        assert "Custom" not in names and "Brisbane" in names
        assert (
            connection.execute("SELECT count(*) FROM battery_config").fetchone()[0] > 0
        )
    assert cache.read_bytes() == b"archive"
    assert not restarted.contains(database_reset.RESET_KEY)
    assert not database_reset.apply_pending_database_reset(test_db, restarted)


def test_pending_reset_does_not_apply_to_another_database(tmp_path, monkeypatch):
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    database_reset.schedule_database_reset(tmp_path / "first.db", preferences)
    calls = []
    monkeypatch.setattr(database_reset, "reset_db", calls.append)
    assert not database_reset.apply_pending_database_reset(
        tmp_path / "other.db", preferences
    )
    assert not calls
    assert preferences.contains(database_reset.RESET_KEY)


def test_failed_reset_keeps_pending_request_and_propagates_error(tmp_path, monkeypatch):
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    database = tmp_path / "app.db"
    database_reset.schedule_database_reset(database, preferences)

    def fail(_):
        raise PermissionError("Database is in use")

    monkeypatch.setattr(database_reset, "reset_db", fail)
    with pytest.raises(PermissionError, match="in use"):
        database_reset.apply_pending_database_reset(database, preferences)
    assert preferences.contains(database_reset.RESET_KEY)


def test_failed_scheduling_removes_pending_flag(tmp_path, monkeypatch):
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(preferences, "status", lambda: QSettings.Status.AccessError)
    with pytest.raises(OSError, match="Could not save"):
        database_reset.schedule_database_reset(tmp_path / "app.db", preferences)
    assert not preferences.contains(database_reset.RESET_KEY)


def test_unreadable_preferences_stop_startup_before_key_lookup(tmp_path, monkeypatch):
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(preferences, "status", lambda: QSettings.Status.AccessError)
    monkeypatch.setattr(
        database_reset, "reset_db", lambda _: pytest.fail("Reset called")
    )
    with pytest.raises(OSError, match="Could not read"):
        database_reset.apply_pending_database_reset(tmp_path / "app.db", preferences)


def test_failure_clearing_pending_flag_stops_startup(tmp_path, monkeypatch):
    preferences = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    database_reset.schedule_database_reset(tmp_path / "app.db", preferences)
    monkeypatch.setattr(database_reset, "reset_db", lambda _: None)
    monkeypatch.setattr(
        preferences,
        "status",
        lambda: (
            QSettings.Status.NoError
            if preferences.contains(database_reset.RESET_KEY)
            else QSettings.Status.AccessError
        ),
    )
    with pytest.raises(OSError, match="Could not clear"):
        database_reset.apply_pending_database_reset(tmp_path / "app.db", preferences)


@pytest.mark.parametrize("failure", [False, True])
def test_startup_resets_before_creating_window_and_stops_on_failure(
    monkeypatch, failure
):
    import frontend_v2.main as startup

    events = []
    app = SimpleNamespace(
        setStyle=lambda *_: None,
        setStyleSheet=lambda *_: None,
        setWindowIcon=lambda *_: None,
        aboutToQuit=SimpleNamespace(connect=lambda *_: None),
        exec=lambda: 0,
    )
    monkeypatch.setattr(startup, "QApplication", lambda *_: app)
    monkeypatch.setattr(startup, "QIcon", lambda *_: None)
    monkeypatch.setattr(
        startup,
        "ctypes",
        SimpleNamespace(
            windll=SimpleNamespace(
                shell32=SimpleNamespace(
                    SetCurrentProcessExplicitAppUserModelID=lambda *_: None
                )
            )
        ),
    )

    def reset(_):
        events.append("reset")
        if failure:
            raise OSError("Database in use")

    def window():
        events.append("window")
        return SimpleNamespace(
            data_manager=SimpleNamespace(close=lambda: None),
            optimiser_gateway=SimpleNamespace(close=lambda: None),
            setWindowIcon=lambda *_: None,
            show=lambda: None,
        )

    monkeypatch.setattr(startup, "apply_pending_database_reset", reset)
    monkeypatch.setattr(startup, "MainWindow", window)
    monkeypatch.setattr(
        startup.QMessageBox, "critical", lambda *_: events.append("error")
    )
    assert startup.main() == int(failure)
    assert events == ["reset", "error" if failure else "window"]
