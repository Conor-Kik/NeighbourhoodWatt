"""Run cache file operations off the GUI thread, excluding active simulations."""

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from backend_v2.aemo.cache_storage import aemo_cache_size, clear_aemo_cache
from frontend_v2.engine.database_reset import schedule_database_reset
from frontend_v2.engine.optimiser_gateway import OptimiserGateway


class CacheWorker(QThread):
    result = pyqtSignal(object, object, str)

    def __init__(self, clear: bool, db_path: str, parent: QObject):
        super().__init__(parent)
        self.clear = clear
        self.db_path = Path(db_path)

    def run(self) -> None:
        try:
            size = clear_aemo_cache() if self.clear else aemo_cache_size()
            database_size = self.db_path.stat().st_size if self.db_path.is_file() else 0
            self.result.emit(size, database_size, "")
        except (OSError, ValueError) as error:
            self.result.emit(-1, -1, str(error))


class CacheManager(QObject):
    updated = pyqtSignal(object, object, str)
    busy_changed = pyqtSignal(bool)
    reset_scheduled = pyqtSignal()

    def __init__(self, gateway: OptimiserGateway, parent: QObject):
        super().__init__(parent)
        self.gateway = gateway
        self.busy = False
        self._worker = None

    def refresh(self) -> None:
        self._start(False)

    def clear(self) -> None:
        self._start(True)

    def reset_on_next_launch(self) -> None:
        """Reserve storage, save the reset request, then let the UI close the app."""
        if self.busy:
            return
        try:
            self.gateway.set_cache_busy(True)
            schedule_database_reset(self.gateway.db_path)
        except (OSError, ValueError) as error:
            self.gateway.set_cache_busy(False)
            self.updated.emit(-1, -1, str(error))
            return
        self.reset_scheduled.emit()

    def _start(self, clear: bool) -> None:
        if self.busy:
            return
        if clear:
            try:
                self.gateway.set_cache_busy(True)
            except ValueError as error:
                self.updated.emit(-1, -1, str(error))
                return
        self.busy = True
        self.busy_changed.emit(True)
        self._worker = CacheWorker(clear, self.gateway.db_path, self)
        self._worker.result.connect(self.updated.emit)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _finished(self) -> None:
        if self._worker.clear:
            self.gateway.set_cache_busy(False)
        self._worker.deleteLater()
        self._worker = None
        self.busy = False
        self.busy_changed.emit(False)

    def close(self) -> None:
        """Finish filesystem work before Qt destroys its thread on app exit."""
        if self._worker is not None:
            self._worker.wait()
