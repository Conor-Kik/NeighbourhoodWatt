"""utils/status.py
Define result statuses for utils/result.py; For status code readability."""

from enum import Enum, auto


class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()

    CREATED = auto()
    DELETED = auto()
    MODIFIED = auto()
