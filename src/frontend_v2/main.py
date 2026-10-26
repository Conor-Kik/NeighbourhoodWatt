import ctypes
import logging
import multiprocessing as mp
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from database_v2.db_constants import DB_PATH
from frontend_v2.engine.database_reset import apply_pending_database_reset
from frontend_v2.ui.main_window import MainWindow

WINDOWS_APP_ID = "TheGridOptimists.GridSync"
# Determine if running as a script or a compiled PyInstaller executable
if hasattr(sys, "_MEIPASS"):
    BASE_UI_PATH = Path(sys._MEIPASS) / "ui"
else:
    BASE_UI_PATH = Path(__file__).parent / "ui"

STYLESHEET_PATH = BASE_UI_PATH / "style.qss"
ICON_PATH = BASE_UI_PATH / "drawing.png"
# macOS only: open drop-down lists below the box with a scroll bar, as on Windows,
# instead of over it.
MACOS_STYLESHEET = """
QComboBox {
    combobox-popup: 0;
}

#form QComboBox QAbstractItemView::item {
    padding: 3px 4px;
}
"""
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("Starting GridSync desktop application")
    # Set Windows taskbar identity
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)

    app = QApplication(sys.argv)
    # The native macOS style draws drop-down lists narrower than their box and
    # clips the text; Fusion draws them as on Windows.
    stylesheet = STYLESHEET_PATH.read_text()
    if sys.platform == "darwin":
        app.setStyle("Fusion")
        stylesheet += MACOS_STYLESHEET
    app.setStyleSheet(stylesheet)
    app.setWindowIcon(QIcon(str(ICON_PATH)))

    try:
        apply_pending_database_reset(DB_PATH)
    except Exception as error:
        logger.exception("Database reset failed")
        QMessageBox.critical(None, "Database reset failed", str(error))
        return 1

    window = MainWindow()
    app.aboutToQuit.connect(window.data_manager.close)
    app.aboutToQuit.connect(window.optimiser_gateway.close)
    window.setWindowIcon(QIcon(str(ICON_PATH)))
    window.show()

    exit_code = app.exec()
    logger.info("GridSync desktop application exited with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    mp.freeze_support()
    main()
