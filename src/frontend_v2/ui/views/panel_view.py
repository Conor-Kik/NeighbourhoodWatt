"""panel_view.py
Frame shared by every panel; subclasses in ui/views/ decide how a payload is
drawn."""

from html import escape
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.ui.theme import THEME

# Most legend entries graphs put in a row; more wrap onto another
MAX_LEGEND_COLUMNS = 4

# Point size of graph tick labels and legends
GRAPH_FONT_SIZE = 10


def add_graph_legend(figure: Figure) -> None:
    """Wrap a figure legend above the plot using the most columns that fit.

    A figure legend avoids squeezing the axes to fit long series names. Replace
    it on each draw or resize so it follows the available panel width.
    """
    for old in list(figure.legends):
        old.remove()
    if not figure.axes:
        return

    handles, _ = figure.axes[0].get_legend_handles_labels()
    if len(handles) < 2:
        return

    renderer = figure.canvas.get_renderer()
    for columns in range(min(len(handles), MAX_LEGEND_COLUMNS), 0, -1):
        legend = figure.legend(
            frameon=False,
            labelcolor=THEME["ink"],
            fontsize=GRAPH_FONT_SIZE,
            loc="outside upper center",
            ncol=columns,
        )
        if (
            columns == 1
            or legend.get_window_extent(renderer).width <= figure.bbox.width
        ):
            return
        legend.remove()


class GraphCanvas(FigureCanvasQTAgg):
    """Matplotlib canvas that leaves the mouse wheel to its page, so a scrolling
    page scrolls over graphs too. Graphs don't zoom on the wheel."""

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Ignored events go up to the parent, ending at the page's scroll area
        event.ignore()


class PanelView(QWidget):
    """Frame for one panel: title, content, empty state and write status.

    Requirements:
        Subclasses set PAYLOAD_TYPE and implement _draw, which draws a payload of
        that type into the content widget.
        A subclass whose content draws itself, with no payload, sets
        NEEDS_NO_PAYLOAD instead.
        Shows empty_text when there is nothing to draw.
        Shows a message when a write succeeds or fails.
        min_width and min_height are the smallest pixels a page may squeeze this
        panel to; the default 0 leaves the panel free to shrink with the window.
        Never reference DataManager, bindings or PanelController."""

    # Payload dataclass from utils/payload.py that this view draws
    PAYLOAD_TYPE: type | tuple[type] = object

    # True when the content draws itself, so it shows before any payload arrives
    NEEDS_NO_PAYLOAD: bool = False

    # Emits what the user submitted; only form views emit it
    submitted = pyqtSignal(dict)

    def __init__(
        self,
        content: QWidget,
        title: str,
        help_text: str,
        empty_text: str,
        subtitle: str = "",
        min_width: int = 0,
        min_height: int = 0,
    ):
        super().__init__()
        self.empty_text = empty_text
        self._content = content

        # Opt-in floor: a page keeps readable panels by setting these; 0 leaves
        # the panel free to shrink, letting it fit more of the grid on screen
        self.setMinimumSize(min_width, min_height)

        # Object names are targeted by style.qss
        self.setObjectName("panel")
        # Plain QWidget subclasses only paint a stylesheet background with this
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._title = QLabel(title)
        self._title.setObjectName("panelTitle")
        self._title.setWordWrap(True)
        # Rich-text tooltips wrap longer guidance within the screen width.
        tooltip = f"<qt>{escape(help_text)}</qt>" if help_text else ""
        self._title.setToolTip(tooltip)

        # Opens the help text in a dialog; hovering it shows the same text
        self._help = QToolButton(self)
        self._help.setObjectName("panelHelp")
        self._help.setText("?")
        self._help.setAccessibleName(f"Help for {title}")
        self._help.setToolTip(tooltip)
        self._help.setCursor(Qt.CursorShape.PointingHandCursor)
        self._help.setVisible(bool(help_text))
        self._help.clicked.connect(
            lambda: QMessageBox.information(self, title, help_text)
        )

        self._header = QWidget()
        title_row = QHBoxLayout(self._header)
        # Zero margins, so the header keeps the title exactly where it was
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self._title, stretch=1)
        title_row.addWidget(self._help)

        # A plain-words line under the title, always visible
        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setObjectName("panelSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))

        self._empty = QLabel(empty_text)
        self._empty.setObjectName("panelEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        # Body swaps between the content and the empty state
        self._body = QStackedWidget()
        self._body.addWidget(self._content)
        self._body.addWidget(self._empty)
        self._body.setCurrentWidget(
            self._content if self.NEEDS_NO_PAYLOAD else self._empty
        )

        self._status = QLabel()
        self._status.setObjectName("panelStatus")
        self._status.setWordWrap(True)
        self._status.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self._header)
        layout.addWidget(self._subtitle)
        layout.addWidget(self._body, stretch=1)
        layout.addWidget(self._status)

    # Not named show(): that would override QWidget.show
    def show_payload(self, payload: Any) -> None:
        """Draw payload into the content widget. Raises TypeError if payload is
        not this view's PAYLOAD_TYPE."""
        if not isinstance(payload, self.PAYLOAD_TYPE):
            if isinstance(self.PAYLOAD_TYPE, tuple):
                pl_names = ", ".join([pl.__name__ for pl in self.PAYLOAD_TYPE])
                raise TypeError(
                    f"{type(self).__name__} expects "
                    f"({pl_names}), got {type(payload).__name__}"
                )

            raise TypeError(
                f"{type(self).__name__} expects {self.PAYLOAD_TYPE.__name__}, "
                f"got {type(payload).__name__}"
            )
        self._body.setCurrentWidget(self._content)
        self._draw(payload)

    def _draw(self, payload: Any) -> None:
        """Draw payload. Each subclass implements this."""
        raise NotImplementedError

    def show_empty(self) -> None:
        """Show empty_text in place of the content."""
        self._body.setCurrentWidget(self._empty)

    def show_error(self, message: str) -> None:
        """Show a write failure under the content."""
        self._show_status(message, "error")

    def show_success(self, message: str) -> None:
        """Show a write success under the content."""
        self._show_status(message, "success")

    def _show_status(self, message: str, state: str) -> None:
        """Show message; style.qss colours it by state."""
        self._status.setText(message)
        self._status.setProperty("state", state)
        # Qt only re-applies property selectors after a re-polish
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._status.show()

    def is_empty(self) -> bool:
        """Returns True if the current widget is empty and False otherwise."""
        return self._body.currentWidget() == self._empty
