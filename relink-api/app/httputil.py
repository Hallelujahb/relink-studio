from flask import request


class BadParam(ValueError):
    """A query parameter that could not be used. Turned into a 400 by the app."""


def int_arg(name, default, lo=None, hi=None):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadParam(f"'{name}' must be an integer.")
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value
