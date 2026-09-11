"""Private command inputs for offline replay; no provider or clock is consulted."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.persistence.events import CommandRecord

recorded_command: ContextVar[CommandRecord | None] = ContextVar("recorded_command", default=None)


@contextmanager
def replay_inputs(command: CommandRecord) -> Iterator[None]:
    token = recorded_command.set(command)
    try:
        yield
    finally:
        recorded_command.reset(token)
