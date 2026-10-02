"""Recorded private social semantics, including automatic checkpoint occurrences."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import (
    REACTION_KEY,
    REACTION_ORIGINAL,
    REACTION_WRAPPED,
    canonical,
    object_input,
    original_input,
)
from wayfarer.persistence.command_inputs import reaction_metadata as _metadata
from wayfarer.persistence.command_inputs import replay_payload as replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = REACTION_KEY
ORIGINAL = REACTION_ORIGINAL
WRAPPED = REACTION_WRAPPED
_current: ContextVar[bool | None] = ContextVar(KEY, default=None)


def _generation(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    return _metadata(object_input(original_input(record.text)))


def correct_social_reactions() -> bool:
    current = _current.get()
    if current is not None:
        return current
    prior = recorded_command.get()
    return (
        True
        if prior is None
        else _generation(CommandInput(prior.payload_hash, prior.command_input))
    )


@contextmanager
def social_generation(correct: bool) -> Iterator[None]:
    token = _current.set(correct)
    try:
        yield
    finally:
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    correct = saved is None or _generation(saved)
    payload = object_input(text)
    # Typed service inputs never carry private metadata from an external caller.
    if _metadata(payload):
        raise ValidationError("Reaction generation is recorded by the command boundary")
    if correct:
        if payload is None:
            payload = {KEY: 1, WRAPPED: text}
        else:
            payload[KEY], payload[ORIGINAL] = 1, text
        text = canonical(payload)
    return text, correct
