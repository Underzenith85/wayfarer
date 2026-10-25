"""Private B173 origin policy for the B248 Minor/Major Healing benefit."""

import hashlib
from typing import Protocol

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.magic.spell_state import PREFIX as SPELL_PREFIX
from wayfarer.engine.simulation.magic.spell_state import event_id, latest, parse_event
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "healing-physician-origin:"
SPELLS = frozenset({"minor-healing", "major-healing"})


class HealingCommand(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def actor_id(self) -> str: ...
    @property
    def cast_id(self) -> str: ...
    @property
    def spell_id(self) -> str: ...
    @property
    def kind(self) -> str: ...


class PhysicianOrigin(Record):
    generation: int = Field(strict=True, ge=1, le=1)
    command_id: Id
    cast_id: Id
    actor_id: Id
    target_id: Id
    spell_id: str
    build_revision: Id
    started_at: int = Field(strict=True, ge=0)


def correct(state: ResourceState, command: HealingCommand, *, fresh: bool = False) -> bool:
    if command.spell_id not in SPELLS:
        return False
    if command.kind == "start":
        return fresh
    effect = latest(state).get(command.cast_id)
    matches = []
    for event in state.events:
        if not event.id.startswith(PREFIX):
            continue
        try:
            value = PhysicianOrigin.model_validate_json(event.kind)
        except SchemaError as exc:
            raise ValidationError("Malformed healing Physician origin") from exc
        if (
            value.spell_id not in SPELLS
            or event.id != PREFIX + hashlib.sha256(value.cast_id.encode()).hexdigest()
            or event.target_id != value.actor_id
            or event.at != value.started_at
            or event.at > state.game_time
        ):
            raise ValidationError("Healing Physician origin has invalid provenance")
        if value.cast_id == command.cast_id:
            matches.append(value)
    if not matches:
        return False
    if len(matches) != 1:
        raise ValidationError("Duplicate healing Physician origin")
    value = matches[0]
    producer_event = next(
        (
            event
            for event in state.events
            if event.id.startswith(SPELL_PREFIX)
            and parse_event(event).effect.cast_id == value.cast_id
        ),
        None,
    )
    if (
        effect is None
        or value.actor_id != command.actor_id
        or value.spell_id != command.spell_id
        or value.actor_id != effect.actor_id
        or value.target_id != effect.target_id
        or value.spell_id != effect.spell_id
        or value.build_revision != effect.build_revision
        or value.started_at != effect.started_at
        or not any(r.command_id == value.command_id for r in state.receipts)
        or producer_event is None
        or producer_event.id != event_id(value.command_id, value.spell_id)
        or producer_event.target_id != value.actor_id
        or producer_event.at != value.started_at
    ):
        raise ValidationError("Healing Physician origin does not match its accepted cast")
    return True


def remember(state: ResourceState, command: HealingCommand, *, enabled: bool) -> ResourceState:
    if not enabled or command.spell_id not in SPELLS:
        return state
    effect = latest(state).get(command.cast_id)
    if command.kind != "start" or effect is None:
        raise ValidationError("Healing Physician origin requires its successful start")
    value = PhysicianOrigin(
        generation=1,
        command_id=command.id,
        cast_id=command.cast_id,
        actor_id=effect.actor_id,
        target_id=effect.target_id,
        spell_id=effect.spell_id,
        build_revision=effect.build_revision,
        started_at=effect.started_at,
    )
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command.cast_id.encode()).hexdigest(),
                    at=effect.started_at,
                    target_id=effect.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )
