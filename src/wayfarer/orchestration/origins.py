"""Task-local origin forwarding through service dispatch, reset at the boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.persistence.events import CommandOrigin

current_origin: ContextVar[CommandOrigin | None] = ContextVar("command_origin", default=None)


@contextmanager
def origin_scope(origin: CommandOrigin | None) -> Iterator[None]:
    token = current_origin.set(origin)
    try:
        yield
    finally:
        current_origin.reset(token)
