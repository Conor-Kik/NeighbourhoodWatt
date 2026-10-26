"""bindings/bindings.py
Turns field values into specific payload for GridObject.
Translator functions translate data into a payload dataclass (utils/payload.py) for
transferring between the database to widgets (via Dao and DataManager).
"""

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from frontend_v2.engine.data_manager import WRITE_OPERATION, DataManager
from frontend_v2.utils.buffer import Buffer
from frontend_v2.utils.result import Result
from frontend_v2.utils.status import Status


class ReadBinding:
    """Translates required data fields for read flow, linking DataManager to widgets."""

    def __init__(self, select: Callable, *, optional: frozenset[str] = frozenset()):
        self.select = select
        self.needs = getattr(select, "needs", None)
        self.optional = optional

        if self.needs is None:
            raise ValueError(
                "ReadBinding given a select function with no @needs when it's required"
            )
        if not optional <= self.needs:
            raise ValueError("Optional read fields must be declared in @needs")

    def translate(self, data: dict[str, Buffer]) -> dict:
        return self.select(data)


class WriteBinding(QObject):
    """Write flow for one FrontendDao setter: each submitted row is saved with it.

    Raises:
        DataManager's signal emits a non-dictionary Result.
    """

    result = pyqtSignal(Result)  # Emits once per submit

    def __init__(self, data_manager: DataManager, setter: str):
        super().__init__()
        self.data_manager = data_manager
        self.setter = setter
        # Only this binding's writes are reported back, not every form's
        self._pending: set[int] = set()
        self.data_manager.result.connect(self._handle_result)

    def submit(self, row: dict) -> None:
        """Save row with the setter. Result arrives on the result signal.

        Fields:
            row: Dictionary of field name to value (what the setter accepts)."""
        try:
            self._pending.add(self.data_manager.set({self.setter: [row]}))
        except ValueError as exc:
            self.result.emit(Result(Status.FAILURE, message=str(exc)))

    def _handle_result(self, result: Result) -> None:
        if not isinstance(result.data, dict):
            raise ValueError("WriteBinding given a result with no dictionary")

        if result.data.get("operation") != WRITE_OPERATION:
            return
        request_id = result.data.get("request_id")
        if request_id not in self._pending:
            return

        self._pending.discard(request_id)
        if result.status == Status.SUCCESS:
            result = Result(Status.SUCCESS, data=result.data, message="Saved")
        self.result.emit(result)


class UiWriteBinding(QObject):
    """Save a frontend preference directly through DataManager's UI state."""

    result = pyqtSignal(Result)

    def __init__(self, data_manager: DataManager):
        super().__init__()
        self.data_manager = data_manager

    def submit(self, row: dict) -> None:
        try:
            self.data_manager.set_ui(row["key"], row["value"])
        except ValueError as exc:
            self.result.emit(Result(Status.FAILURE, message=str(exc)))
        else:
            self.result.emit(Result(Status.SUCCESS, message="Saved"))
