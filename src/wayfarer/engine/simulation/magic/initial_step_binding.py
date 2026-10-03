"""Trusted initial-Step source binding defers sight until the actual casting pose."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[bool] = ContextVar("initial_casting_step_binding", default=False)


def preparing() -> bool:
    return _current.get()


@contextmanager
def initial_binding(*, enabled: bool) -> Iterator[None]:
    token = _current.set(enabled)
    try:
        yield
    finally:
        _current.reset(token)
