"""Typed, event-backed execution for authored mental and spirit trait channels."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.mental_spirit import mental_spirit_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import (
    Command,
    Pool,
    ResourceEvent,
    ResourceState,
    Scheduled,
)
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "mental-spirit-use:"
MentalKind = Literal[
    "influence", "probe", "read", "possession", "terror", "neutralize", "foresight", "spirit"
]

KINDS: Mapping[str, frozenset[MentalKind]] = {
    "advantage:dominance": frozenset({"influence"}),
    "advantage:mind-control": frozenset({"influence"}),
    "advantage:mind-probe": frozenset({"probe"}),
    "advantage:mind-reading": frozenset({"read"}),
    "advantage:neutralize": frozenset({"neutralize"}),
    "advantage:possession": frozenset({"possession"}),
    "advantage:precognition": frozenset({"foresight"}),
    "advantage:oracle": frozenset({"foresight"}),
    "advantage:psychometry": frozenset({"probe"}),
    "advantage:racial-memory": frozenset({"probe"}),
    "advantage:terror": frozenset({"terror"}),
    "advantage:true-faith": frozenset({"terror"}),
    "advantage:channeling": frozenset({"spirit"}),
    "advantage:medium": frozenset({"spirit"}),
    "advantage:spirit-empathy": frozenset({"spirit"}),
}


class MentalChannel(Record):
    id: str
    definition_id: str
    actor_id: str
    target_id: str
    location_id: str
    kind: MentalKind
    fact_ids: tuple[str, ...] = ()
    actor_score: int = Field(default=10, ge=1)
    resistance_score: int | None = Field(default=None, ge=1)
    actor_roll: int = Field(default=10, ge=3, le=18)
    resistance_roll: int | None = Field(default=None, ge=3, le=18)
    duration_seconds: int = Field(default=1, ge=1)
    fatigue_cost: int = Field(default=0, ge=0)
    interruptible: bool = True
    blocked: bool = False
    power_family: str = "psi"


class MentalCommand(Command):
    definition_id: str
    channel_id: str
    kind: Literal["activate", "interrupt"]


class MentalOutcome(Record):
    outcome: Literal["successful", "resisted", "blocked", "interrupted"]
    actor_id: str
    target_id: str
    definition_id: str
    channel_id: str
    revealed_fact_ids: tuple[str, ...] = ()
    effect_id: str | None = None
    expires_at: int | None = Field(default=None, ge=0)


class MentalEvent(Record):
    command_id: str
    command_kind: Literal["activate", "interrupt"]
    outcome: MentalOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _effect_id(channel_id: str) -> str:
    return PREFIX + "effect:" + hashlib.sha256(channel_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[MentalEvent, ...]:
    return tuple(
        MentalEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _spent_fatigue(resources: ResourceState, actor_id: str, amount: int) -> tuple[Pool, ...]:
    if amount == 0:
        return resources.pools
    pool_id = "fp:" + actor_id
    pool = next((value for value in resources.pools if value.id == pool_id), None)
    if pool is None or pool.current < amount:
        raise ValidationError("Mental/spirit use requires available fatigue")
    return tuple(
        value.model_copy(update={"current": value.current - amount})
        if value.id == pool_id
        else value
        for value in resources.pools
    )


def _resisted(channel: MentalChannel) -> bool:
    if channel.resistance_score is None:
        if channel.resistance_roll is not None:
            raise ValidationError("Resistance roll requires a resistance score")
        return False
    if channel.resistance_roll is None:
        raise ValidationError("Resistance score requires a resistance roll")
    actor_margin = channel.actor_score - channel.actor_roll
    resistance_margin = channel.resistance_score - channel.resistance_roll
    return actor_margin <= resistance_margin


def apply_mental_use(
    resources: ResourceState,
    world: World,
    command: MentalCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    channels: tuple[MentalChannel, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, World, MentalOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Mental/spirit execution requires actor authority")
    prior = next((event for event in history(resources) if event.command_id == command.id), None)
    if prior is not None:
        if (
            prior.command_kind == command.kind
            and prior.outcome.channel_id == command.channel_id
            and prior.outcome.definition_id == command.definition_id
        ):
            return resources, world, prior.outcome
        raise ConflictError("Mental/spirit command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Mental/spirit revision changed")
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.actor_id != command.actor_id
        or channel.definition_id != command.definition_id
        or channel.kind not in KINDS.get(command.definition_id, frozenset())
    ):
        raise ValidationError("Authored mental/spirit channel is unavailable")
    traits = mental_spirit_traits(build, definitions)
    if not traits.has(command.definition_id):
        raise ValidationError("Mental/spirit trait is not in the approved build")
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
        raise ValidationError("Mental/spirit channel context changed")

    effect_id = _effect_id(channel.id)
    updated_world = world
    pools = resources.pools
    active = resources.active_effect_ids
    scheduled = resources.scheduled
    if command.kind == "interrupt":
        activation = next(
            (
                event.outcome
                for event in reversed(history(resources))
                if event.outcome.effect_id == effect_id and event.outcome.outcome == "successful"
            ),
            None,
        )
        if (
            activation is None
            or effect_id not in active
            or not channel.interruptible
            or activation.expires_at is None
            or resources.game_time >= activation.expires_at
        ):
            raise ConflictError("Mental/spirit effect cannot be interrupted")
        outcome = MentalOutcome(
            outcome="interrupted",
            actor_id=command.actor_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            channel_id=channel.id,
            effect_id=effect_id,
            expires_at=resources.game_time,
        )
        active = tuple(value for value in active if value != effect_id)
        scheduled = tuple(value for value in scheduled if value.target_id != effect_id)
    else:
        pools = _spent_fatigue(resources, command.actor_id, channel.fatigue_cost)
        result: Literal["successful", "resisted", "blocked"]
        if channel.blocked:
            result = "blocked"
        elif _resisted(channel):
            result = "resisted"
        else:
            result = "successful"
        persistent = channel.kind in {"influence", "possession", "neutralize"}
        expires_at = (
            resources.game_time + channel.duration_seconds if result == "successful" else None
        )
        revealed = (
            channel.fact_ids
            if result == "successful"
            and channel.kind
            in {
                "probe",
                "read",
                "foresight",
                "spirit",
            }
            else ()
        )
        outcome = MentalOutcome(
            outcome=result,
            actor_id=command.actor_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            channel_id=channel.id,
            revealed_fact_ids=revealed,
            effect_id=effect_id if result == "successful" and persistent else None,
            expires_at=expires_at,
        )
        if outcome.effect_id is not None:
            assert expires_at is not None
            active = tuple(sorted(set(active) | {outcome.effect_id}))
            scheduled = scheduled + (
                Scheduled(
                    id=PREFIX + "expiry:" + hashlib.sha256(channel.id.encode()).hexdigest(),
                    due=expires_at,
                    kind="expire",
                    target_id=outcome.effect_id,
                ),
            )
        for fact_id in revealed:
            updated_world = updated_world.learn(command.actor_id, fact_id)

    event = MentalEvent(command_id=command.id, command_kind=command.kind, outcome=outcome)
    updated = resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "pools": pools,
            "active_effect_ids": active,
            "scheduled": scheduled,
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=resources.game_time,
                    target_id=channel.target_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    )
    return updated, updated_world, outcome
