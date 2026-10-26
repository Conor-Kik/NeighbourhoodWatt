"""Dropdown popups close when the main window changes geometry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QComboBox, QMainWindow, QVBoxLayout, QWidget

from frontend_v2.ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


class GeometryWindow(MainWindow):
    """Exercise real window handlers without starting database workers."""

    def __init__(self):
        QMainWindow.__init__(self)
        container = QWidget()
        layout = QVBoxLayout(container)
        self.combo = QComboBox()
        self.combo.addItems(["Brisbane", "Melbourne"])
        layout.addWidget(self.combo)
        self.setCentralWidget(container)

    def closeEvent(self, event):
        QMainWindow.closeEvent(self, event)


@pytest.mark.parametrize("change", ["move", "resize"])
def test_geometry_change_dismisses_dropdown_without_changing_selection(change):
    window = GeometryWindow()
    window.show()
    APP.processEvents()
    try:
        window.combo.setCurrentIndex(1)
        window.combo.showPopup()
        APP.processEvents()
        assert window.combo.view().isVisible()

        if change == "move":
            window.move(window.pos().x() + 50, window.pos().y() + 50)
        else:
            window.resize(window.width() + 50, window.height() + 50)
        APP.processEvents()

        assert not window.combo.view().isVisible()
        assert window.combo.currentText() == "Melbourne"

        window.combo.showPopup()
        APP.processEvents()
        assert window.combo.view().isVisible()
    finally:
        window.combo.hidePopup()
        window.close()
