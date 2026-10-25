"""Private input generation for B421 projections, including old exact retries."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer import validation
from wayfarer.engine.simulation.health.condition_generations import condition_generation
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import KEY as KEY
from wayfarer.persistence.command_inputs import (
    ORIGINAL_INPUT,
    generation,
    generation_version,
    metadata,
    object_input,
    stamp,
)
from wayfarer.persistence.command_inputs import WRAPPED_INPUT as WRAPPED_INPUT
from wayfarer.persistence.command_inputs import replay_payload as replay_payload
from wayfarer.persistence.events import CommandInput

_current: ContextVar[bool | None] = ContextVar(KEY, default=None)


def correct_symptom_attributes() -> bool:
    current = _current.get()
    if current is not None:
        return current
    # Some plans construct their RulesContext before entering submit().
    prior = recorded_command.get()
    return (
        True if prior is None else generation(CommandInput(prior.payload_hash, prior.command_input))
    )


def condition_attributes_from_payload(text: str) -> bool:
    payload = object_input(text)
    return metadata(payload) and payload is not None and validation.integer(payload[KEY]) >= 2


def drowsiness_from_payload(text: str) -> bool:
    payload = object_input(text)
    return metadata(payload) and payload is not None and validation.integer(payload[KEY]) >= 3


@contextmanager
def symptom_generation(
    correct: bool, *, condition_attributes: bool = False, deduplicate_drowsiness: bool = False
) -> Iterator[None]:
    token = _current.set(correct)
    try:
        with condition_generation(condition_attributes, deduplicate=deduplicate_drowsiness):
            yield
    finally:
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    """Retain the recorded encoding while the store owns duplicate identity."""
    replay = recorded_command.get()
    prior = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    version = 3 if prior is None else generation_version(prior)
    correct = version > 0
    if correct:
        old = object_input(prior.text) if prior is not None and prior.text is not None else None
        text = stamp(
            text,
            retain_original=old is None or ORIGINAL_INPUT in old or WRAPPED_INPUT in old,
            version=version,
        )
    return text, correct
