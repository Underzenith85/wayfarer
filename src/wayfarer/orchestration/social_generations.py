"""Recorded private social semantics, including automatic checkpoint occurrences."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer import validation
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
CURRENT_VERSION = 4
_current: ContextVar[bool | None] = ContextVar(KEY, default=None)
_extremes: ContextVar[bool | None] = ContextVar("social_regular_contest_extremes", default=None)
_questions: ContextVar[bool | None] = ContextVar("interrogation_quick_contest", default=None)
_self_control: ContextVar[bool | None] = ContextVar("condition_self_control", default=None)


def _version(record: CommandInput) -> int:
    if record.text is None:
        return 0
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    payload = object_input(original_input(record.text))
    return validation.integer(payload[KEY]) if _metadata(payload) and payload is not None else 0


def _generation(record: CommandInput) -> bool:
    return _version(record) > 0


def extreme_scores_from_payload(text: str) -> bool:
    """Validated private version 2 includes B349 in automatic social occurrences."""
    payload = object_input(original_input(text))
    return _metadata(payload) and payload is not None and validation.integer(payload[KEY]) >= 2


def self_control_from_payload(text: str) -> bool:
    payload = object_input(original_input(text))
    return _metadata(payload) and payload is not None and validation.integer(payload[KEY]) >= 3


def questions_from_payload(text: str) -> bool:
    payload = object_input(original_input(text))
    return _metadata(payload) and payload is not None and validation.integer(payload[KEY]) >= 4


def interrogation_quick_contest_enabled() -> bool:
    selected = _questions.get()
    if selected is not None:
        return selected
    prior = recorded_command.get()
    return prior is None or _version(CommandInput(prior.payload_hash, prior.command_input)) >= 4


def condition_self_control_enabled() -> bool:
    selected = _self_control.get()
    if selected is not None:
        return selected
    prior = recorded_command.get()
    return prior is None or _version(CommandInput(prior.payload_hash, prior.command_input)) >= 3


def regular_contest_extremes_enabled() -> bool:
    selected = _extremes.get()
    if selected is not None:
        return selected
    prior = recorded_command.get()
    return prior is None or _version(CommandInput(prior.payload_hash, prior.command_input)) >= 2


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
def social_generation(
    correct: bool,
    *,
    extreme_scores: bool = False,
    self_control: bool = False,
    source_questions: bool = False,
) -> Iterator[None]:
    token = _current.set(correct)
    extreme_token = _extremes.set(extreme_scores)
    control_token = _self_control.set(self_control)
    question_token = _questions.set(source_questions)
    try:
        yield
    finally:
        _questions.reset(question_token)
        _self_control.reset(control_token)
        _extremes.reset(extreme_token)
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    version = CURRENT_VERSION if saved is None else _version(saved)
    correct = version > 0
    payload = object_input(text)
    # Typed service inputs never carry private metadata from an external caller.
    if _metadata(payload):
        raise ValidationError("Reaction generation is recorded by the command boundary")
    if correct:
        if payload is None:
            payload = {KEY: version, WRAPPED: text}
        else:
            payload[KEY], payload[ORIGINAL] = version, text
        text = canonical(payload)
    return text, correct
