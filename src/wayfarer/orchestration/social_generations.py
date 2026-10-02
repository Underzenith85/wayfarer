"""Recorded private social semantics, including automatic checkpoint occurrences."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import canonical, object_input, original_input
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "reaction_semantics_generation"
ORIGINAL = "reaction_original_input"
WRAPPED = "reaction_command_input"
_current: ContextVar[bool | None] = ContextVar(KEY, default=None)


def _metadata(payload: dict[str, object] | None) -> bool:
    if payload is None:
        return False
    if KEY not in payload:
        if ORIGINAL in payload or WRAPPED in payload:
            raise ValidationError("Reaction generation metadata requires its version")
        return False
    if type(payload[KEY]) is not int or payload[KEY] != 1:
        raise ValidationError("Unsupported recorded reaction generation")
    if WRAPPED in payload:
        if set(payload) != {KEY, WRAPPED} or not isinstance(payload[WRAPPED], str):
            raise ValidationError("Invalid recorded reaction input wrapper")
        if object_input(validation.string(payload[WRAPPED])) is not None:
            raise ValidationError("Reaction wrapper cannot hide an object input")
    else:
        raw = payload.get(ORIGINAL)
        source = object_input(raw) if isinstance(raw, str) else None
        intended = {key: value for key, value in payload.items() if key not in {KEY, ORIGINAL}}
        if (
            source is None
            or {KEY, ORIGINAL, WRAPPED}.intersection(source)
            or canonical(source) != canonical(intended)
        ):
            raise ValidationError("Invalid recorded reaction original input")
    return True


def _unwrap(text: str) -> str:
    payload = object_input(text)
    if not _metadata(payload):
        return text
    assert payload is not None
    return validation.string(payload[WRAPPED] if WRAPPED in payload else payload[ORIGINAL])


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


def replay_payload(text: str) -> object:
    """Unwrap validated Symptoms and social metadata in their recorded order."""
    raw = _unwrap(original_input(text))
    try:
        return validation.decode(raw)
    except json.JSONDecodeError:
        return raw
