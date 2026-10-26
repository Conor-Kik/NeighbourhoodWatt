"""Defer the existing database reset until startup, before connections open."""

from pathlib import Path

from PyQt6.QtCore import QSettings

from database_v2.db_initilisation import reset_db

RESET_KEY = "database_reset_on_startup"


def schedule_database_reset(
    db_path: str | Path, preferences: QSettings | None = None
) -> None:
    preferences = preferences or QSettings("TheGridOptimists", "GridSync")
    preferences.setValue(RESET_KEY, str(Path(db_path).resolve()))
    preferences.sync()
    if preferences.status() != QSettings.Status.NoError:
        preferences.remove(RESET_KEY)
        preferences.sync()
        raise OSError("Could not save the reset request. The app has not been closed.")


def apply_pending_database_reset(
    db_path: str | Path, preferences: QSettings | None = None
) -> bool:
    preferences = preferences or QSettings("TheGridOptimists", "GridSync")
    path = Path(db_path).resolve()
    preferences.sync()
    if preferences.status() != QSettings.Status.NoError:
        raise OSError("Could not read reset preferences; startup has been stopped.")
    if preferences.value(RESET_KEY) != str(path):
        return False
    reset_db(path)
    preferences.remove(RESET_KEY)
    preferences.sync()
    if preferences.status() != QSettings.Status.NoError:
        raise OSError("Could not clear the reset request; startup has been stopped.")
    return True
