"""text_view.py
Panel that shows one line of text."""

from PyQt6.QtWidgets import QLabel

from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.payload import TabularPayload


class TextView(PanelView):
    """Shows text, such as which run a page describes. Draws a TabularPayload;
    the text is the first column of the first row."""

    PAYLOAD_TYPE = TabularPayload

    def __init__(self, title: str, help_text: str, empty_text: str):
        self.label = QLabel()
        self.label.setObjectName("textValue")
        self.label.setWordWrap(True)

        super().__init__(self.label, title, help_text, empty_text)

    def _draw(self, payload: TabularPayload) -> None:
        """Shows the first column of the first row.

        Raises an error if the payload is empty."""
        try:
            text = payload.rows[0][0]
        except IndexError as exc:
            raise ValueError(
                f"Empty payload sent to TextView titled '{self._title.text()}'"
            ) from exc
        self.label.setText(str(text))
