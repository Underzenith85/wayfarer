"""Command-scoped B421 condition semantics, selected from captured private input."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_attributes: ContextVar[bool] = ContextVar("condition_attribute_generation", default=True)
_drowsiness: ContextVar[bool] = ContextVar("deduplicate_drowsiness", default=True)


def current_attributes() -> bool:
    return _attributes.get()


def deduplicate_drowsiness() -> bool:
    return _drowsiness.get()


@contextmanager
def condition_generation(current: bool, *, deduplicate: bool = False) -> Iterator[None]:
    token = _attributes.set(current)
    drowsy_token = _drowsiness.set(deduplicate)
    try:
        yield
    finally:
        _drowsiness.reset(drowsy_token)
        _attributes.reset(token)
