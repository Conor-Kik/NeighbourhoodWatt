"""utils/result.py
Dataclass that messages back result-metadata."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from frontend_v2.utils.status import Status

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """Wrap data within status and message. Unit tests are recommended within
    creating new statuses."""

    status: Status
    data: T | None = None
    message: str = "OK"
