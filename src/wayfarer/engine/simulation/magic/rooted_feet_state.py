"""Private B244 rooting, immutable original rolls and real-turn escape receipts."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record

PREFIX = "rooted-feet:"
PREFIXES = (PREFIX,)


class RootedFeetSubject(Record):
    id: Id
    caster_id: Id
    target_id: Id
    touching: Literal[True]
    visible: Literal[True]
    standing_living_human: Literal[True]
    hostile_resistance: Literal[True]


class ObserveRootedFeetSubject(Command):
    kind: Literal["rooted_feet_subject"] = "rooted_feet_subject"
    subject: RootedFeetSubject


class CastRootedFeet(Command):
    kind: Literal["rooted_feet_cast"] = "rooted_feet_cast"
    cast_id: Id
    subject_id: Id


class TryRootedFeetEscape(Command):
    kind: Literal["rooted_feet_escape"] = "rooted_feet_escape"
    effect_id: Id
    encounter_id: Id


RootedFeetCommand = Annotated[
    ObserveRootedFeetSubject | CastRootedFeet | TryRootedFeetEscape, Field(discriminator="kind")
]
ADAPTER: TypeAdapter[RootedFeetCommand] = TypeAdapter(RootedFeetCommand)


class RootedFeetObservation(Record):
    command_id: Id
    subject: RootedFeetSubject
    witness: str


class RootedFeetEffect(Record):
    id: Id
    command_id: Id
    caster_id: Id
    target_id: Id
    original_check: CheckTrace
    initial_resistance: CheckTrace | None
    started_at: int
    expires_at: int
    status: Literal["active", "resisted", "failed", "escaped", "expired"]


class RootedFeetEscape(Record):
    command_id: Id
    effect_id: Id
    actor_id: Id
    encounter_id: Id
    round: int
    turn_index: int
    check: CheckTrace
    escaped: bool


class RootedFeetReceipt(Record):
    command_id: Id
    outcome: Literal["accepted", "active", "resisted", "failed", "escaped", "retained"]
    energy_spent: int = 0


def identifier(label: str, command_id: str) -> str:
    return PREFIX + label + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def append(
    resources: ResourceState, label: str, command_id: str, actor_id: str, value: Record
) -> ResourceState:
    event = ResourceEvent(
        id=identifier(label, command_id),
        at=resources.game_time,
        target_id=actor_id,
        kind=value.model_dump_json(),
    )
    return resources.model_copy(update={"events": resources.events + (event,)})


def observations(resources: ResourceState) -> tuple[RootedFeetObservation, ...]:
    return tuple(
        RootedFeetObservation.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "subject:")
    )


def effects(resources: ResourceState) -> dict[str, RootedFeetEffect]:
    result: dict[str, RootedFeetEffect] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX + "effect:"):
            effect = RootedFeetEffect.model_validate_json(event.kind)
            result[effect.id] = effect
    return result


def escapes(resources: ResourceState) -> tuple[RootedFeetEscape, ...]:
    return tuple(
        RootedFeetEscape.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "escape:")
    )


def active_effect(resources: ResourceState, actor_id: str) -> RootedFeetEffect | None:
    return next(
        (
            e
            for e in effects(resources).values()
            if e.target_id == actor_id
            and e.status == "active"
            and resources.game_time < e.expires_at
        ),
        None,
    )


def require_locomotion(resources: ResourceState, actor_id: str) -> None:
    if active_effect(resources, actor_id) is not None:
        raise ConflictError("Rooted Feet prevents locomotion")


def active_effects(resources: ResourceState) -> tuple[RootedFeetEffect, ...]:
    return tuple(
        e
        for e in effects(resources).values()
        if e.status == "active" and resources.game_time < e.expires_at
    )


def active_caster_ids(resources: ResourceState) -> frozenset[str]:
    return frozenset(e.caster_id for e in active_effects(resources))


def active_actor_ids(resources: ResourceState) -> frozenset[str]:
    return frozenset(e.target_id for e in active_effects(resources))


def melee_weapon_penalty(resources: ResourceState, actor_id: str) -> int:
    return -2 if active_effect(resources, actor_id) is not None else 0


def rooted_dodge(resources: ResourceState, actor_id: str, score: int) -> int:
    return score // 2 if active_effect(resources, actor_id) is not None else score


def expire(resources: ResourceState, now: int) -> ResourceState:
    for effect in effects(resources).values():
        if effect.status == "active" and now >= effect.expires_at:
            resources = append(
                resources,
                "effect",
                "expiry:" + effect.id,
                effect.target_id,
                effect.model_copy(update={"status": "expired"}),
            )
    return resources


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "effect_id": e.id,
            "actor_id": e.target_id,
            "expires_at": e.expires_at,
            "rooted": active_effect(resources, e.target_id) is not None,
        }
        for e in effects(resources).values()
        if e.target_id in actor_ids or e.caster_id in actor_ids
    )
