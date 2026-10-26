"""main_window.py
Holds the grid and replay controls, and wires together parts at startup."""

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QCloseEvent, QMoveEvent, QResizeEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QTabWidget

from database_v2.db_constants import DB_PATH
from frontend_v2.engine.data_manager import DataManager
from frontend_v2.engine.optimiser_gateway import OptimiserGateway
from frontend_v2.ui.pages.community_page import community_page
from frontend_v2.ui.pages.debrief_page import debrief_page
from frontend_v2.ui.pages.operation_page import operation_page
from frontend_v2.ui.pages.setup.setup import setup_page
from frontend_v2.ui.scroll_area import scrollable_page

TITLE = "Neighbourhood Grid Optimiser"
DEFAULT_SIZE = QSize(1400, 850)
MINIMUM_SIZE = QSize(1024, 680)
# Share of the screen the window may open at, when DEFAULT_SIZE is too big
SCREEN_FRACTION = 0.9


class MainWindow(QMainWindow):
    """Holds Grids and replay controls, and wires parts together at startup.

    Requirements:
        No business logic, caching, or refresh bookkeeping."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle(TITLE)
        self.setMinimumSize(MINIMUM_SIZE)
        self.resize(self._opening_size())

        self.data_manager = DataManager()
        self.optimiser_gateway = OptimiserGateway(
            str(DB_PATH),
            self.data_manager.replay_clock,
        )

        # One Grid per tab; hidden Grids catch up when shown
        self.tabs = QTabWidget()
        self.tabs.addTab(
            setup_page(self.data_manager, self.optimiser_gateway), "Settings"
        )
        self.tabs.addTab(operation_page(self.data_manager), "Operation")
        self.tabs.addTab(
            scrollable_page(community_page(self.data_manager)), "Community"
        )
        self.tabs.addTab(scrollable_page(debrief_page(self.data_manager)), "Debrief")

        self.setCentralWidget(self.tabs)

    def _dismiss_dropdowns(self) -> None:
        """Close popups anchored to the window's previous position or size."""
        for combo in self.findChildren(QComboBox):
            if combo.view().isVisible():
                combo.hidePopup()

    def moveEvent(self, event: QMoveEvent) -> None:
        self._dismiss_dropdowns()
        super().moveEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._dismiss_dropdowns()
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop any running simulation and close data manager threads on exit.

        A running SimulationWorker QThread keeps the process alive after the
        window is closed, so cancel it and wait before accepting the close."""
        self.optimiser_gateway.shutdown()
        self.data_manager.close()
        event.accept()

    @staticmethod
    def _opening_size() -> QSize:
        """DEFAULT_SIZE, shrunk to fit smaller screens."""
        screen = QApplication.primaryScreen().availableGeometry()
        return DEFAULT_SIZE.boundedTo(
            QSize(
                int(screen.width() * SCREEN_FRACTION),
                int(screen.height() * SCREEN_FRACTION),
            )
        )
