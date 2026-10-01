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
_current: ContextVar[bool | None] = ContextVar(KEY, default=None)


def _generation(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    payload = validation.mapping(validation.decode(record.text))
    generation = payload.get(KEY)
    if KEY in payload and (type(generation) is not int or generation != 1):
        raise ValidationError("Unsupported recorded Symptoms attribute generation")
    return generation == 1


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
    payload = validation.mapping(validation.decode(text))
    if correct:
        payload[KEY] = 1
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if prior is not None and prior.text is not None:
        original = prior.text
        if validation.mapping(validation.decode(original)) == payload:
            text = original
    return text, correct
