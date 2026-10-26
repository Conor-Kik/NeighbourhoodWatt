"""Summary labels remain child widgets throughout panel construction."""

import pytest
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QWidget

from frontend_v2.ui.views.accent_view import AccentView
from frontend_v2.utils.payload import TabularPayload

APP = QApplication.instance() or QApplication([])


class WindowMonitor(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.shown_windows: list[QWidget] = []

    def eventFilter(  # noqa: N802
        self, watched: QObject | None, event: QEvent | None
    ) -> bool:
        if (
            event is not None
            and event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            self.shown_windows.append(watched)
        return False


@pytest.mark.parametrize("description", ["State of charge", "over the whole run", ""])
def test_description_does_not_open_a_window_during_construction(description):
    monitor = WindowMonitor()
    APP.installEventFilter(monitor)
    view = None
    try:
        view = AccentView("Summary", "Help", "Empty", description=description)
        assert monitor.shown_windows == []
        assert view.description.parentWidget() is view._content
        assert not view.description.isWindow()
        assert view.description.isHidden() == (description == "")
    finally:
        APP.removeEventFilter(monitor)
        if view is not None:
            view.close()


def test_populated_card_keeps_all_labels_inside_the_shown_panel():
    view = AccentView("Summary", "Help", "Empty", description="State of charge")
    view.show_payload(
        TabularPayload(headers=["Value", "Detail"], rows=[("50%", "20 kWh")])
    )
    monitor = WindowMonitor()
    APP.installEventFilter(monitor)
    try:
        view.show()
        APP.processEvents()
        assert monitor.shown_windows == [view]
        assert all(
            label.window() is view
            for label in (view.label, view.description, view.detail)
        )
        assert view.description.isVisible()
        assert view.detail.isVisible()
    finally:
        APP.removeEventFilter(monitor)
        view.close()
