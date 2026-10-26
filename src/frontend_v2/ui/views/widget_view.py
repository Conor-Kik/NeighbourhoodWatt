"""widget_view.py
Panel around one child widget that draws itself, with no payload."""

from PyQt6.QtWidgets import QWidget

from frontend_v2.ui.views.panel_view import PanelView


class WidgetView(PanelView):
    """Panel around one child widget that draws itself. Lets a hand-rolled widget,
    like the simulation launcher or the location editor, sit on a Grid like any
    other view. The child is a form, so its controls get the shared form
    colours."""

    NEEDS_NO_PAYLOAD = True

    def __init__(
        self,
        content: QWidget,
        title: str,
        help_text: str,
        min_width: int = 0,
        min_height: int = 0,
    ):
        # Object names are targeted by style.qss, which gives a form's controls
        # explicit colours
        content.setObjectName("form")
        # No payload, so there is no empty state to show
        super().__init__(
            content,
            title,
            help_text,
            "",
            min_width=min_width,
            min_height=min_height,
        )
