"""Isolated private B240/B245 selected-hand casting producer records."""

import hashlib
from typing import Literal

from pydantic import Field, TypeAdapter, field_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record

PREFIX = "hand-melee-spell:"


class CastHandDeathtouch(Command):
    kind: Literal["cast-hand-deathtouch"] = "cast-hand-deathtouch"
    operation: Literal["start", "concentrate", "complete", "cancel"]
    cast_id: Id
    energy: int = Field(strict=True, ge=1, le=3)
    carrier: HandCarrier


HandMeleeSpellCommand = CastHandDeathtouch
ADAPTER: TypeAdapter[HandMeleeSpellCommand] = TypeAdapter(HandMeleeSpellCommand)


class HandMeleeCast(Record):
    generation: Literal[1] = 1

    @field_validator("generation", mode="before")
    @classmethod
    def strict_generation(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("Hand Melee producer generation must be exact integer 1")
        return 1

    actor_id: Id
    cast_id: Id
    command_id: Id
    status: Literal["casting", "held", "failed", "cancelled", "spent", "dissipated"]
    carrier: HandCarrier
    carrier_digest: str
    mana_event_id: Id
    mana_event_digest: str
    build_revision: Id
    skill: int
    energy: int = Field(strict=True, ge=1, le=3)
    started_at: int
    ready_at: int
    hp_at_start: int
    credited_seconds: int = Field(default=0, ge=0, le=1)
    distracted: bool = False
    completed_at: int | None = None
    check: CheckTrace | None = None
    paid_fp: int = 0


class HandMeleeSpellReceipt(Record):
    command_id: Id
    cast_id: Id | None = None
    outcome: str
    game_time: int
    energy_spent: int = 0


def identifier(family: str, identity: str) -> str:
    return PREFIX + family + ":" + hashlib.sha256(identity.encode()).hexdigest()


def append(
    resources: ResourceState, family: str, identity: str, target: str, value: Record
) -> ResourceState:
    event = ResourceEvent(
        id=identifier(family, identity),
        at=resources.game_time,
        target_id=target,
        kind=value.model_dump_json(),
    )
    if any(e.id == event.id for e in resources.events):
        raise ConflictError("Melee spell record identity is immutable")
    return resources.model_copy(update={"events": resources.events + (event,)})


def casts(resources: ResourceState) -> dict[str, HandMeleeCast]:
    found: dict[str, HandMeleeCast] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX + "cast:"):
            value = HandMeleeCast.model_validate_json(event.kind)
            found[value.cast_id] = value
    return found


def cast_event(resources: ResourceState, cast_id: str) -> ResourceEvent:
    return next(
        e
        for e in reversed(resources.events)
        if e.id.startswith(PREFIX + "cast:")
        and HandMeleeCast.model_validate_json(e.kind).cast_id == cast_id
    )


def pending_actor_ids(resources: ResourceState) -> tuple[str, ...]:
    return tuple(c.actor_id for c in casts(resources).values() if c.status == "casting")


def held_actor_ids(resources: ResourceState) -> tuple[str, ...]:
    return tuple(c.actor_id for c in casts(resources).values() if c.status == "held")


def interrupt_casts(
    resources: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    for cast in casts(resources).values():
        if cast.actor_id == actor_id and cast.status == "casting":
            updated = cast.model_copy(
                update={"distracted": True} if distraction else {"status": "cancelled"}
            )
            resources = append(
                resources, "cast", command_id + ":" + cast.cast_id, actor_id, updated
            )
    return resources


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "cast_id": c.cast_id,
            "actor_id": c.actor_id,
            "status": c.status,
            "carrier": c.carrier.model_dump(mode="json"),
            "energy": c.energy,
            "paid_fp": c.paid_fp,
        }
        for c in casts(resources).values()
        if c.actor_id in actor_ids
    )


def needs_clock_checkpoints(resources: ResourceState) -> bool:
    return bool(pending_actor_ids(resources))
