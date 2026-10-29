"""Independent native activation provenance for current B348 continuation."""

import hashlib

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.abilities import internal_id
from wayfarer.engine.simulation.ability_state import PREFIX as ABILITY_PREFIX
from wayfarer.engine.simulation.ability_state import effects
from wayfarer.engine.simulation.ability_types import AbilityCommand, AbilityEffect, AbilityEvent
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "ability-resistance-origin:"


class ResistanceActivation(Record):
    generation: int = Field(ge=1, le=1)
    command_id: Id
    effect: AbilityEffect


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _native_origins(resources: ResourceState) -> dict[tuple[str, str], str]:
    origins: dict[tuple[str, str], str] = {}
    for event in resources.events:
        if not event.id.startswith(ABILITY_PREFIX):
            continue
        native = AbilityEvent.read_native_json(event.kind)
        effect = native.effect
        if effect is None:
            continue
        key = (native.actor_id, native.ability_id)
        if not effect.active or not effect.concentrating:
            origins.pop(key, None)
        elif native.outcome.outcome == "concentrating" and key not in origins:
            origins[key] = event.id
    return origins


def _pending(resources: ResourceState) -> tuple[ResistanceActivation, ...]:
    active = {(e.actor_id, e.ability_id): e for e in effects(resources)}
    native_origins = _native_origins(resources)
    result = []
    for event in resources.events:
        if not event.id.startswith(PREFIX):
            continue
        origin = ResistanceActivation.model_validate_json(event.kind)
        natives = tuple(e for e in resources.events if e.id == internal_id(origin.command_id))
        producer = natives[0] if len(natives) == 1 else None
        native = AbilityEvent.read_native_json(producer.kind) if producer is not None else None
        if (
            sum(e.id == event.id for e in resources.events) != 1
            or event.kind != origin.model_dump_json()
            or event.id != identifier(origin.command_id)
            or event.at != origin.effect.started_at
            or event.target_id != origin.effect.actor_id
            or native is None
            or producer is None
            or producer.at != origin.effect.started_at
            or producer.target_id != origin.effect.actor_id
            or native.effect != origin.effect
            or producer.kind != native.model_dump_json()
            or native.outcome.outcome != "concentrating"
            or (native.actor_id, native.ability_id, native.target_id)
            != (origin.effect.actor_id, origin.effect.ability_id, origin.effect.target_id)
            or origin.effect.kind not in {"mind-reading", "burning-malediction"}
            or not origin.effect.concentrating
            or origin.effect.ready_at != origin.effect.started_at + 1
        ):
            raise ValidationError("Invalid resistance activation producer")
        key = (origin.effect.actor_id, origin.effect.ability_id)
        if native_origins.get(key) != internal_id(origin.command_id):
            continue
        current = active.get(key)
        if current is None or not current.concentrating:
            continue
        if current.model_copy(update={"distracted": origin.effect.distracted}) != origin.effect:
            raise ValidationError("Resistance concentration changed its captured identity")
        result.append(origin)
    return tuple(result)


def pending(resources: ResourceState) -> tuple[ResistanceActivation, ...]:
    try:
        return _pending(resources)
    except SchemaError as exc:
        raise ValidationError("Invalid resistance activation producer schema") from exc


def current(resources: ResourceState, command: AbilityCommand) -> bool:
    return any(
        (p.effect.actor_id, p.effect.ability_id, p.effect.channel_id)
        == (command.actor_id, command.ability_id, command.channel_id)
        for p in pending(resources)
    )


def record(resources: ResourceState, command: AbilityCommand) -> ResourceState:
    event = next(e for e in resources.events if e.id == internal_id(command.id))
    native = AbilityEvent.read_native_json(event.kind)
    if native.effect is None or native.outcome.outcome != "concentrating":
        return resources
    origin = ResistanceActivation(generation=1, command_id=command.id, effect=native.effect)
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier(command.id),
                    kind=origin.model_dump_json(),
                    at=resources.game_time,
                    target_id=command.actor_id,
                ),
            )
        }
    )
