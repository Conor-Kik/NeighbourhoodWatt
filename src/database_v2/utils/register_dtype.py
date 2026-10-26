from collections.abc import Callable
from functools import wraps
from typing import Any

import numpy as np


def register_dtype(dtype: list[tuple], pk: str | tuple[str, ...] | None = None):
    """Decorator that attaches numpy dtype metadata and the primary key to methods.

    pk names the fields that identify one row, and defaults to the leading dtype
    field. Declare a composite pk when a single leading field is not unique.

    Raises:
        ValueError: pk repeats a field, or names one the dtype does not have.
    """
    parsed_dtype = np.dtype(dtype)
    field_names = parsed_dtype.names or ()
    pk_fields = (pk,) if isinstance(pk, str) else tuple(pk or ())
    if pk_fields != tuple(dict.fromkeys(pk_fields)):
        raise ValueError(f"Duplicate pk fields given to register_dtype: {pk_fields}")
    unknown = [name for name in pk_fields if name not in field_names]
    if unknown:
        raise ValueError(f"Unknown pk fields given to register_dtype: {unknown}")
    if not pk_fields:
        pk_fields = field_names[:1]

    def decorator(func: Callable) -> Any:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)

            if isinstance(result, list) and result and isinstance(result[0], dict):
                return [tuple(row.get(name) for name in field_names) for row in result]
            return result

        setattr(wrapper, "dtype", parsed_dtype)  # noqa: B010
        setattr(wrapper, "pk", pk_fields)  # noqa: B010
        return wrapper

    return decorator
