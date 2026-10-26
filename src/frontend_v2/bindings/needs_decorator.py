import functools

SELECTOR_FUNCTIONS = {}


def needs(keys: set):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.needs = keys
        SELECTOR_FUNCTIONS[func.__name__] = wrapper
        return wrapper

    return decorator
