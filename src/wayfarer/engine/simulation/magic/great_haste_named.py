"""Private, explicitly known named actor targeting for B239 Regular spells."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.great_haste_state import CastGreatHaste
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    HostCommand,
    NamedInitialStepCastGreatHaste,
    NamedStepCastGreatHaste,
    StepCommand,
)
from wayfarer.engine.simulation.resources import Command
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "great-haste-named-origin:"


class NamedCastGreatHaste(Command):
    kind: Literal["named-great-haste"] = "named-great-haste"
    operation: Literal["start", "concentrate", "cancel"]
    channel_id: Id
    cast_id: Id
    known_fact_id: Id


class NamedOrigin(Record):
    actor_id: Id
    target_id: Id
    channel_id: Id
    cast_id: Id
    known_fact_id: Id
    target_name: str
    fact_predicate: str
    fact_value: str


NamedHostCommand = Annotated[HostCommand | NamedCastGreatHaste, Field(discriminator="kind")]
HOST_ADAPTER: TypeAdapter[NamedHostCommand] = TypeAdapter(NamedHostCommand)
_current: ContextVar[NamedCastGreatHaste | None] = ContextVar("great_haste_named", default=None)


def current() -> NamedCastGreatHaste | None:
    return _current.get()


@contextmanager
def named_cast(command: NamedCastGreatHaste) -> Iterator[None]:
    token = _current.set(command)
    try:
        yield
    finally:
        _current.reset(token)


def cast_command(command: NamedCastGreatHaste) -> CastGreatHaste:
    return CastGreatHaste(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=command.expected_revision,
        operation=command.operation,
        channel_id=command.channel_id,
        cast_id=command.cast_id,
    )


def origin(state: PlayState, cast_id: str) -> NamedOrigin | None:
    return next(
        (
            NamedOrigin.model_validate_json(event.kind)
            for event in reversed(state.resources.events)
            if event.id.startswith(PREFIX)
            and NamedOrigin.model_validate_json(event.kind).cast_id == cast_id
        ),
        None,
    )


def binding(state: PlayState, target_id: str, command: CastGreatHaste) -> NamedOrigin | None:
    selected = current()
    previous = origin(state, command.cast_id)
    if selected is None:
        if previous is not None and command.operation != "cancel":
            raise ConflictError("Named casting requires its authenticated private carrier")
        return None
    if command.operation == "cancel":
        return previous
    fact = next(
        (
            f
            for f in state.world.perspective(command.actor_id).facts
            if f.id == selected.known_fact_id
        ),
        None,
    )
    target = next((e for e in state.world.entities if e.id == target_id), None)
    if fact is None or fact.subject_id != target_id or target is None or not target.name.strip():
        raise ValidationError("Named Great Haste requires explicit known subject knowledge")
    if fact.predicate != "name" or fact.value != target.name:
        raise ValidationError(
            "Named Great Haste requires authoritative matching subject name knowledge"
        )
    accepted = NamedOrigin(
        actor_id=command.actor_id,
        target_id=target_id,
        channel_id=command.channel_id,
        cast_id=command.cast_id,
        known_fact_id=selected.known_fact_id,
        target_name=target.name,
        fact_predicate=fact.predicate,
        fact_value=fact.value,
    )
    if command.operation == "concentrate" and previous != accepted:
        raise ConflictError("Named Great Haste requires its immutable accepted subject knowledge")
    return accepted


@contextmanager
def named_step(command: StepCommand) -> Iterator[None]:
    if isinstance(command, (NamedStepCastGreatHaste, NamedInitialStepCastGreatHaste)):
        selected = NamedCastGreatHaste(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
            operation=command.operation,
            channel_id=command.channel_id,
            cast_id=command.cast_id,
            known_fact_id=command.known_fact_id,
        )
        with named_cast(selected):
            yield
    else:
        yield
