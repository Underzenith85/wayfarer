"""Typed, event-backed use of authored sensory and communication channels."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.sensory import sensory_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "sensory-use:"


class SensoryChannel(Record):
    id: str
    definition_id: str
    actor_id: str
    target_id: str
    location_id: str
    fact_ids: tuple[str, ...] = ()
    medium: str = "vision"
    blocked: bool = False
    resistant: bool = False


class SensoryCommand(Command):
    definition_id: str
    channel_id: str
    kind: Literal["observe", "communicate"]


class SensoryOutcome(Record):
    outcome: Literal["observed", "communicated", "blocked", "resisted"]
    actor_id: str
    target_id: str
    definition_id: str
    revealed_fact_ids: tuple[str, ...] = ()
    expires_at: int | None = Field(default=None, ge=0)


class SensoryEvent(Record):
    command_id: str
    channel_id: str
    kind: Literal["observe", "communicate"]
    outcome: SensoryOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[SensoryEvent, ...]:
    return tuple(
        SensoryEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def apply_sensory_use(
    resources: ResourceState,
    world: World,
    command: SensoryCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    channels: tuple[SensoryChannel, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, World, SensoryOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Sensory execution requires actor authority")
    prior = next((event for event in history(resources) if event.command_id == command.id), None)
    if prior is not None:
        if prior.kind == command.kind and prior.channel_id == command.channel_id:
            return resources, world, prior.outcome
        raise ConflictError("Sensory command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Sensory revision changed")
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.actor_id != command.actor_id
        or channel.definition_id != command.definition_id
    ):
        raise ValidationError("Authored sensory channel is unavailable")
    traits = sensory_traits(build, definitions)
    if not traits.has(command.definition_id):
        raise ValidationError("Sensory trait is not in the approved build")
    entities = {entity.id: entity for entity in world.entities}
    facts = {fact.id for fact in world.facts}
    if (
        command.actor_id not in entities
        or channel.target_id not in entities
        or channel.location_id not in entities
        or entities[command.actor_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
        or not set(channel.fact_ids) <= facts
    ):
        raise ValidationError("Sensory channel context changed")
    if command.kind == "communicate" and not traits.can_communicate(channel.medium):
        raise ValidationError("Trait cannot use the authored communication medium")
    result: Literal["observed", "communicated", "blocked", "resisted"]
    if channel.blocked:
        result = "blocked"
    elif channel.resistant:
        result = "resisted"
    else:
        result = "communicated" if command.kind == "communicate" else "observed"
    revealed = channel.fact_ids if result == "observed" else ()
    outcome = SensoryOutcome(
        outcome=result,
        actor_id=command.actor_id,
        target_id=channel.target_id,
        definition_id=command.definition_id,
        revealed_fact_ids=revealed,
        expires_at=resources.game_time + 1,
    )
    event = SensoryEvent(
        command_id=command.id,
        channel_id=command.channel_id,
        kind=command.kind,
        outcome=outcome,
    )
    updated_world = world
    if result == "observed":
        for fact_id in revealed:
            updated_world = updated_world.learn(command.actor_id, fact_id)
    return (
        resources.model_copy(
            update={
                "revision": resources.revision + 1,
                "events": resources.events
                + (
                    ResourceEvent(
                        id=_event_id(command.id),
                        at=resources.game_time,
                        target_id=command.actor_id,
                        kind=event.model_dump_json(),
                    ),
                ),
            }
        ),
        updated_world,
        outcome,
    )
