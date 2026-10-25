"""Capture B173 default knowledge policy independently for current producers."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import (
    default_knowledge_version,
    stamp_default_knowledge,
)
from wayfarer.persistence.events import CommandInput, payload_digest

ACTIVE = True
CURRENT_VERSION = 2

_current: ContextVar[int | None] = ContextVar("default_knowledge_generation", default=None)


def current_version() -> int:
    current = _current.get()
    if current is not None:
        return current
    prior = recorded_command.get()
    return (
        (CURRENT_VERSION if ACTIVE else 0)
        if prior is None
        else default_knowledge_version(CommandInput(prior.payload_hash, prior.command_input))
    )


def correct_default_knowledge() -> bool:
    return current_version() > 0


def require_default_knowledge() -> bool:
    return current_version() >= 2


def required_from_payload(text: str) -> bool:
    return default_knowledge_version(CommandInput(payload_digest({"input": text}), text)) >= 2


@contextmanager
def default_knowledge_scope(correct: bool, *, required: bool = False) -> Iterator[None]:
    token = _current.set(2 if required else int(correct))
    try:
        yield
    finally:
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    replay = recorded_command.get()
    prior = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    version = (
        (CURRENT_VERSION if ACTIVE else 0) if prior is None else default_knowledge_version(prior)
    )
    return (stamp_default_knowledge(text, version=version) if version else text), version > 0
