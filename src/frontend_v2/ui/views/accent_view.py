"""accent_view.py
Panel that accentuates one number."""

from html import escape

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from frontend_v2.ui.theme import THEME
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import TabularPayload

# The number is drawn as large as fits the panel's width, between these sizes
MAX_NUMBER_POINT_SIZE = 36
MIN_NUMBER_POINT_SIZE = 12


class AccentView(PanelView):
    """Accentuates a particular number, with an added description, both centred.
    Draws a TabularPayload; the number is the first column of the first row. An
    optional second column holds detail under the description. A detail title
    gives it a separate section with a smaller number.

    The number's size is set through a stylesheet, since the app's stylesheet
    sets every widget's font size and so overrides QWidget.setFont."""

    PAYLOAD_TYPE = TabularPayload

    def __init__(
        self,
        title: str,
        help_text: str,
        empty_text: str,
        description: str = "",
        detail_title: str = "",
        subtitle: str = "",
        number_point_size: int | None = None,
    ):
        content = QWidget()
        self.label = QLabel(content)
        self.description = QLabel(description, content)
        self.description.setVisible(bool(description))
        self.detail = QLabel(content)
        self.detail.hide()
        self.detail_title = detail_title
        self._point_size = 0
        self._number_point_size = number_point_size

        # Object names are targeted by style.qss
        self.label.setObjectName("accentNumber")
        self.description.setObjectName("accentDescription")
        self.detail.setObjectName("accentDescription")
        for label in (self.label, self.description, self.detail):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
        # The panel's width sizes the number, not the other way round; otherwise a
        # long number would force its panel wider
        self.label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

        layout = QVBoxLayout(content)
        # Stretches on both sides centre the content vertically
        layout.addStretch()
        layout.addWidget(self.label)
        layout.addWidget(self.description)
        layout.addWidget(self.detail)
        layout.addStretch()

        super().__init__(content, title, help_text, empty_text, subtitle)

        # Refits responsive numbers; configured sizes stay consistent across cards.
        self.label.installEventFilter(self)

    def _draw(self, payload: TabularPayload) -> None:
        """Accents the first column of the first row.

        Raises an error if the payload is empty."""
        try:
            row = payload.rows[0]
        except IndexError as exc:
            raise ValueError(
                f"Empty payload sent to AccentView titled '{self._title.text()}'"
            ) from exc
        self.label.setText(row[0])
        detail = str(row[1]) if len(row) > 1 else ""
        self.detail.setText(
            f"<hr/>{escape(self.detail_title)}<br/>"
            f'<b style="font-size:18pt;color:{THEME["ink"]}">{escape(detail)}</b>'
            if detail and self.detail_title
            else detail
        )
        self.detail.setVisible(bool(detail))
        self._fit_number()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Refits the number when its label resizes. Overrides
        QObject.eventFilter."""
        if watched is self.label and event.type() == QEvent.Type.Resize:
            self._fit_number()
        return super().eventFilter(watched, event)

    def _fit_number(self) -> None:
        """Use the configured size, or fit responsive numbers onto one line."""
        point_size = self._number_point_size
        if point_size is None:
            font = QFont(self.label.font())
            font.setBold(True)
            text = self.label.text()

            point_size = MIN_NUMBER_POINT_SIZE
            for size in range(MAX_NUMBER_POINT_SIZE, MIN_NUMBER_POINT_SIZE, -1):
                font.setPointSize(size)
                if QFontMetrics(font).horizontalAdvance(text) <= self.label.width():
                    point_size = size
                    break

        # Restyling resizes the label, so only do it on a change
        if point_size != self._point_size:
            self._point_size = point_size
            self.label.setStyleSheet(f"font-size: {point_size}pt;")
