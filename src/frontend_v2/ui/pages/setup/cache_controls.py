"""Storage controls for downloaded AEMO files."""

from PyQt6.QtCore import QLocale
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.engine.cache_manager import CacheManager
from frontend_v2.engine.optimiser_gateway import OptimiserGateway


class CacheControls(QWidget):
    def __init__(self, gateway: OptimiserGateway):
        super().__init__()
        self.gateway = gateway
        self.manager = CacheManager(gateway, self)
        self._size = None
        self.setObjectName("form")
        self.setMaximumWidth(720)
        self.size_label = QLabel("AEMO cache: calculating…")
        self.database_label = QLabel("Database: calculating…")
        self.note = QLabel(
            "Downloaded AEMO files can be cleared to free space. Saved runs, "
            "configurations and database data are kept. Future simulations may "
            "download these files again."
        )
        self.status = QLabel()
        self.refresh_button = QPushButton("Refresh size")
        self.clear_button = QPushButton("Clear AEMO cache")
        self.reset_button = QPushButton("Reset database…")
        layout = QVBoxLayout(self)
        for widget in (self.size_label, self.database_label, self.note, self.status):
            widget.setWordWrap(True)
            layout.addWidget(widget)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.clear_button)
        layout.addWidget(self.reset_button)
        layout.addStretch()
        self.refresh_button.clicked.connect(self.manager.refresh)
        self.clear_button.clicked.connect(self._confirm_clear)
        self.reset_button.clicked.connect(self._confirm_reset)
        self.manager.updated.connect(self._show_result)
        self.manager.busy_changed.connect(self._update_buttons)
        gateway.simulation_state_changed.connect(self._update_buttons)
        QApplication.instance().aboutToQuit.connect(self.manager.close)
        self.manager.reset_scheduled.connect(QApplication.instance().quit)
        self._update_buttons()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self.manager.refresh()

    def _show_result(self, size: int, database_size: int, error: str) -> None:
        self._size = size if size >= 0 else None
        for label, title, value in (
            (self.size_label, "AEMO cache", size),
            (self.database_label, "Database", database_size),
        ):
            shown = (
                QLocale().formattedDataSize(
                    value, 2, QLocale.DataSizeFormat.DataSizeSIFormat
                )
                if value >= 0
                else "unavailable"
            )
            label.setText(f"{title}: {shown}")
        self.status.setText(error)
        self._update_buttons()

    def _update_buttons(self, *_args) -> None:
        self.clear_button.setText(
            "Working…" if self.manager.busy else "Clear AEMO cache"
        )
        self.refresh_button.setEnabled(not self.manager.busy)
        self.reset_button.setEnabled(
            not self.manager.busy and not self.gateway.is_running
        )
        self.clear_button.setEnabled(
            not self.manager.busy
            and not self.gateway.is_running
            and self._size is not None
            and self._size > 0
        )
        if self.gateway.is_running:
            self.status.setText("Cache cleanup is unavailable while a simulation runs.")
        elif self.status.text().startswith("Cache cleanup is unavailable"):
            self.status.clear()

    def _confirm_clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear AEMO cache?",
            f"{self.size_label.text()}\n\nDelete downloaded AEMO files? "
            "They will be downloaded again when needed. Saved runs and database "
            "contents will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.clear()

    def _confirm_reset(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Reset database?",
            "All saved runs, custom configurations, locations and database data "
            "will be deleted and defaults restored. The app will close; reset "
            "happens on next launch. AEMO cache files are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.reset_on_next_launch()
