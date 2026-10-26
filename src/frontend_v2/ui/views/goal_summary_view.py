"""Structured goal context with a responsive calculation and optional detail."""

from html import escape

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from frontend_v2.ui.theme import THEME
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import TabularPayload


class GoalSummaryView(PanelView):
    """Draw supplied goal facts without fetching or calculating results."""

    PAYLOAD_TYPE = TabularPayload

    def __init__(self, title: str, help_text: str, empty_text: str):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.labels = {}
        for key, style in (
            ("goal", "goalHeading"),
            ("explanation", "textValue"),
            ("context", "panelSubtitle"),
        ):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setObjectName(style)
            self.labels[key] = label
            layout.addWidget(label)
        self.calculation = QWidget()
        self.calculation_layout = QGridLayout(self.calculation)
        self.calculation_layout.setContentsMargins(0, 8, 0, 8)
        self.components = []
        self.captions = {}
        for key, heading in (
            ("financial", "Recorded financial cost"),
            ("carbon", "+ Modelled carbon cost"),
            ("terminal", "− Remaining-energy estimate"),
            ("result", "= Adjusted Balanced result"),
        ):
            component = QWidget()
            column = QVBoxLayout(component)
            column.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(heading)
            caption.setObjectName("panelSubtitle")
            caption.setWordWrap(True)
            self.captions[key] = caption
            amount = QLabel()
            amount.setTextFormat(Qt.TextFormat.PlainText)
            amount.setObjectName("goalAmount")
            amount.setWordWrap(True)
            self.labels[key] = amount
            column.addWidget(caption)
            column.addWidget(amount)
            if key == "carbon":
                rate = QLabel()
                rate.setTextFormat(Qt.TextFormat.PlainText)
                rate.setObjectName("panelSubtitle")
                rate.setWordWrap(True)
                self.labels["carbon_rate"] = rate
                column.addWidget(rate)
            self.components.append(component)
        layout.addWidget(self.calculation)
        for key in ("note", "details"):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setObjectName("panelSubtitle")
            self.labels[key] = label
        self.labels["details"].setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.labels["note"])
        self.more = QToolButton()
        self.more.setObjectName("configDisclosure")
        self.more.setText("About these results")
        self.more.setCheckable(True)
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more.setArrowType(Qt.ArrowType.RightArrow)
        self.more.toggled.connect(self.labels["details"].setVisible)
        self.more.toggled.connect(
            lambda expanded: self.more.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
            )
        )
        self.labels["details"].hide()
        layout.addWidget(self.more, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.labels["details"])
        self.carbon_source = QLabel(
            f'<a style="color: {THEME["accent"]}" '
            'href="https://www.aer.gov.au/system/files/2024-05/'
            "AER%20-%20Valuing%20emissions%20reduction%20-%20Final%20guidance"
            '%20and%20explanatory%20statement%20-%20May%202024.pdf#page=7">'
            "Source: Australian Energy Regulator · May 2024, Table 1 "
            "(2023 AUD)</a>"
        )
        self.carbon_source.setTextFormat(Qt.TextFormat.RichText)
        self.carbon_source.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.carbon_source.setOpenExternalLinks(True)
        self.carbon_source.setWordWrap(True)
        self.carbon_source.setObjectName("panelSubtitle")
        self.carbon_source.hide()
        layout.addWidget(self.carbon_source)
        self.more.toggled.connect(
            lambda expanded: self.carbon_source.setVisible(
                expanded and self._show_carbon
            )
        )
        self._show_carbon = True
        self._calculation_layout_state = None
        super().__init__(content, title, help_text, empty_text)
        self._columns = 0

    def _draw(self, payload: TabularPayload) -> None:
        if not payload.rows:
            self.show_empty()
            return
        values = dict(zip(payload.headers, payload.rows[0], strict=True))
        for key, label in self.labels.items():
            if key != "details":
                label.setText(values[key])
        sections = []
        for section in values["details"].split("\n\n"):
            heading, _, explanation = section.partition("\n")
            sections.append(f"<b>{escape(heading)}</b><br>{escape(explanation)}")
        self.labels["details"].setText("<br><br>".join(sections))
        self.labels["context"].setToolTip(f"Run {values['run_id']}")
        emissions = values["goal"] == "Emissions"
        self.captions["financial"].setText(
            "Recorded grid emissions" if emissions else "Recorded financial cost"
        )
        self.captions["result"].setText(
            "= Adjusted Balanced result"
            if values["goal"] == "Balanced"
            else "= Adjusted grid emissions"
            if emissions
            else "= Adjusted financial cost"
        )
        self._show_carbon = bool(values["carbon"])
        self.components[1].setVisible(self._show_carbon)
        self.carbon_source.setVisible(self.more.isChecked() and self._show_carbon)
        self.labels["carbon_rate"].setVisible(bool(values["carbon_rate"]))
        self.calculation.setVisible(bool(values["result"]))
        self.labels["note"].setVisible(bool(values["note"]))
        self._layout_calculation()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._layout_calculation()

    def _layout_calculation(self) -> None:
        visible = [
            component
            for index, component in enumerate(self.components)
            if index != 1 or self._show_carbon
        ]
        columns = max(1, min(len(visible), self.width() // 250))
        state = (columns, self._show_carbon)
        if state == self._calculation_layout_state:
            return
        self._calculation_layout_state = state
        self._columns = columns
        for component in self.components:
            self.calculation_layout.removeWidget(component)
        for index, component in enumerate(visible):
            self.calculation_layout.addWidget(
                component,
                index // columns,
                index % columns,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
        for index in range(len(self.components)):
            self.calculation_layout.setColumnStretch(index, int(index < columns))
