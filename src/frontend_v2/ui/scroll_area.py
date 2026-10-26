"""Shared page scroll container with one vertical scroll owner."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QWidget


def scrollable_page(page: QWidget, *, horizontal: bool = True) -> QScrollArea:
    """Let content fill the viewport, scrolling vertically when it cannot fit."""
    scroll = QScrollArea()
    scroll.setObjectName("pageScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    if not horizontal:
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(page)
    return scroll
