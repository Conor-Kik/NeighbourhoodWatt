import inspect


def enforce_get_set(cls):
    """Class decorator ensuring all methods start with "get_" or "set_".

    Args:
        cls: Class being validated.

    Returns:
        The validated class unchanged if methods pass validation.
    Raises:
        AttributeError: If a public method does not start with "get_" or
            "set_".
    """

    for name, value in cls.__dict__.items():
        # Skip private helpers and __init__
        if name.startswith("_"):
            continue

        # Skip @property
        if isinstance(value, property):
            continue

        if inspect.isfunction(value) or isinstance(value, (classmethod | staticmethod)):
            if not (name.startswith("get_") or name.startswith("set_")):
                raise AttributeError(
                    f"Method '{name}' in '{cls.__name__}' must start with 'get_'"
                    "or 'set_'."
                )

    return cls
