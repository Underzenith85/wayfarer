"""Producer-bound, one-second B69 continuation during B481 off-shift time."""

import hashlib

from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.abilities import internal_id
from wayfarer.engine.simulation.ability_state import PREFIX as ABILITY_PREFIX
from wayfarer.engine.simulation.ability_state import effects
from wayfarer.engine.simulation.ability_types import AbilityCommand, AbilityEffect, AbilityEvent
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "ability-offshift:"


class OffshiftConcentration(Record):
    generation: int
    command_id: Id
    effect: AbilityEffect


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _origins(resources: ResourceState) -> dict[tuple[str, str], str]:
    origins: dict[tuple[str, str], str] = {}
    for event in resources.events:
        if not event.id.startswith(ABILITY_PREFIX):
            continue
        native = AbilityEvent.model_validate_json(event.kind)
        if native.effect is None:
            continue
        key = (native.actor_id, native.ability_id)
        if not native.effect.active or not native.effect.concentrating:
            origins.pop(key, None)
        elif key not in origins:
            origins[key] = event.id
    return origins


def _pending(resources: ResourceState) -> tuple[OffshiftConcentration, ...]:
    """Validate retained producer facts; never enroll an unmarked old effect."""
    current = {(e.actor_id, e.ability_id): e for e in effects(resources)}
    origins = _origins(resources)
    result = []
    for event in resources.events:
        if not event.id.startswith(PREFIX):
            continue
        policy = OffshiftConcentration.model_validate_json(event.kind)
        producers = tuple(e for e in resources.events if e.id == internal_id(policy.command_id))
        producer = producers[0] if len(producers) == 1 else None
        native = AbilityEvent.model_validate_json(producer.kind) if producer is not None else None
        if (
            policy.generation != 1
            or sum(e.id == event.id for e in resources.events) != 1
            or event.target_id != policy.effect.actor_id
            or event.at != policy.effect.started_at
            or identifier(policy.command_id) != event.id
            or native is None
            or native.effect != policy.effect
            or producer is None
            or producer.at != policy.effect.started_at
            or producer.target_id != policy.effect.actor_id
            or (native.actor_id, native.ability_id, native.target_id)
            != (policy.effect.actor_id, policy.effect.ability_id, policy.effect.target_id)
            or native.outcome.outcome != "concentrating"
            or policy.effect.kind != "mind-reading"
            or not policy.effect.concentrating
            or policy.effect.ready_at != policy.effect.started_at + 1
        ):
            raise ValidationError("Invalid off-shift ability producer")
        key = (policy.effect.actor_id, policy.effect.ability_id)
        if origins.get(key) != internal_id(policy.command_id):
            continue
        active = current.get(key)
        if active is None or not active.concentrating:
            continue
        if active.model_copy(update={"distracted": policy.effect.distracted}) != policy.effect:
            raise ValidationError(
                "Off-shift concentration changed its captured interval or identity"
            )
        result.append(policy)
    return tuple(result)


def pending(resources: ResourceState) -> tuple[OffshiftConcentration, ...]:
    """Reject malformed private/native records through the command error contract."""
    try:
        return _pending(resources)
    except SchemaError as exc:
        raise ValidationError("Invalid off-shift ability producer schema") from exc


def matching(resources: ResourceState, command: AbilityCommand) -> OffshiftConcentration | None:
    return next(
        (
            p
            for p in pending(resources)
            if (p.effect.actor_id, p.effect.ability_id, p.effect.channel_id)
            == (command.actor_id, command.ability_id, command.channel_id)
        ),
        None,
    )


def record(resources: ResourceState, command: AbilityCommand) -> ResourceState:
    event = next(e for e in resources.events if e.id == internal_id(command.id))
    native = AbilityEvent.model_validate_json(event.kind)
    if native.effect is None or native.outcome.outcome != "concentrating":
        return resources
    policy = OffshiftConcentration(generation=1, command_id=command.id, effect=native.effect)
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier(command.id),
                    kind=policy.model_dump_json(),
                    at=resources.game_time,
                    target_id=command.actor_id,
                ),
            )
        }
    )
