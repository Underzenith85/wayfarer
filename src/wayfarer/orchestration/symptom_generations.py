"""Private input generation for B421 projections, including old exact retries."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "symptom_attribute_generation"
WRAPPED_INPUT = "symptom_command_input"
_current: ContextVar[bool | None] = ContextVar(KEY, default=None)


def _object(text: str) -> dict[str, object] | None:
    """Old service inputs include opaque principal prefixes and JSON arrays."""
    try:
        decoded = validation.decode(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(decoded) if isinstance(decoded, dict) else None


def _metadata(payload: dict[str, object] | None) -> bool:
    if payload is None or KEY not in payload:
        return False
    generation = payload[KEY]
    if type(generation) is not int or generation != 1:
        raise ValidationError("Unsupported recorded Symptoms attribute generation")
    if WRAPPED_INPUT in payload and (
        set(payload) != {KEY, WRAPPED_INPUT} or not isinstance(payload[WRAPPED_INPUT], str)
    ):
        raise ValidationError("Invalid recorded Symptoms input wrapper")
    return True


def replay_payload(text: str) -> object:
    """Remove validated private metadata before a family's strict command adapter.

    This is a decoded view only. command_text() and the receipt hash continue to
    describe the exact stored bytes; opaque families remain unsupported by the
    generic replay dispatcher.
    """
    payload = _object(text)
    if _metadata(payload):
        assert payload is not None
        if WRAPPED_INPUT not in payload:
            return {key: value for key, value in payload.items() if key != KEY}
        text = validation.string(payload[WRAPPED_INPUT])
    try:
        return validation.decode(text)
    except json.JSONDecodeError:
        return text


def _generation(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    return _metadata(_object(record.text))


def correct_symptom_attributes() -> bool:
    current = _current.get()
    if current is not None:
        return current
    # Some plans construct their RulesContext before entering submit().
    prior = recorded_command.get()
    return (
        True
        if prior is None
        else _generation(CommandInput(prior.payload_hash, prior.command_input))
    )


@contextmanager
def symptom_generation(correct: bool) -> Iterator[None]:
    token = _current.set(correct)
    try:
        yield
    finally:
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    """Use the old bytes only for identical input; changed retries still conflict."""
    replay = recorded_command.get()
    prior = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    correct = prior is None or _generation(prior)
    payload = _object(text)
    _metadata(payload)
    if correct:
        if payload is None:
            payload = {WRAPPED_INPUT: text}
        payload[KEY] = 1
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if prior is not None and prior.text is not None:
        original = prior.text
        if original == text or (payload is not None and _object(original) == payload):
            text = original
    return text, correct
