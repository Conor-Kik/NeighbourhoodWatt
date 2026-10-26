"""panel_controller.py
Connects one PanelView to its optional ReadBinding and WriteBinding."""

from PyQt6.QtCore import QObject

from frontend_v2.bindings.bindings import ReadBinding, WriteBinding
from frontend_v2.ui.views.panel_view import PanelView
from frontend_v2.utils.buffer import Buffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status


class PanelController(QObject):
    """Decides what one panel shows; its PanelView decides how.

    Requirements:
        Optional ReadBinding and WriteBinding.
        Invalidated when its needs change; cleared once refreshed.
        Shows empty state when any needed buffer has no rows.
        Passes write results to its view.
        Never fetch data, call a DAO, or reference other PanelControllers."""

    def __init__(
        self,
        id: str,
        view: PanelView,
        read: ReadBinding | None = None,
        write: WriteBinding | None = None,
    ):
        super().__init__()
        self.id = id
        self.view = view

        self.read = read
        self.write = write
        self.needs: set[str] = set()

        self.is_invalidated = False

        if read is not None:
            self.needs = set(read.needs)
        if write is not None:
            write.result.connect(self._on_write_result)
            view.submitted.connect(self.submit)

    def invalidate(self) -> None:
        """Mark for refresh; Grid refreshes once visible."""
        self.is_invalidated = True

    def refresh(self, payload: dict[str, Buffer]) -> None:
        """Translate payload via ReadBinding and show it, or show empty state."""
        self.is_invalidated = False

        if self.read is None:
            return

        if any(
            self._is_empty(value)
            for key, value in payload.items()
            if key not in self.read.optional
        ):
            self.view.show_empty()
            return

        self.view.show_payload(self.read.translate(payload))

    def submit(self, values: dict) -> None:
        """Submit what the view's form holds via the write binding. Result
        arrives in _on_write_result."""
        if self.write is None:
            raise ValueError(f"PanelController '{self.id}' has no WriteBinding")
        self.write.submit(values)

    @staticmethod
    def _is_empty(value) -> bool:
        """True if value is a buffer holding no rows.

        A UI-key value holds no rows to be empty, so only a sized value can be
        empty. A None UI value is a value a panel draws, not missing data: the
        replay clock publishes the optimiser's step as None until the optimiser
        reports one."""
        return hasattr(value, "__len__") and len(value) == 0

    def _on_write_result(self, result: Result) -> None:
        """Handles write result from WriteBinding."""
        if result.status == Status.FAILURE:
            self.view.show_error(result.message)
        else:
            self.view.show_success(result.message)
