"""Isolated generation-three B244 Wither charge, contact and effect records."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record

PREFIX = "wither-spell:"


class WitherLimbCast(Record):
    actor_id: Id
    cast_id: Id
    command_id: Id
    status: Literal["casting", "held", "failed", "cancelled", "spent", "dissipated"]
    carrier: StaffCarrier
    carrier_digest: str
    build_revision: Id
    skill: int
    energy: int = Field(default=5, strict=True, ge=5, le=5)
    started_at: int
    ready_at: int
    hp_at_start: int
    credited_seconds: int = Field(default=0, ge=0, le=1)
    distracted: bool = False
    completed_at: int | None = None
    check: CheckTrace | None = None
    paid_fp: int = 0


class WitherLimbContact(Record):
    pending_id: Id
    command_id: Id
    encounter_id: Id
    attacker_id: Id
    defender_id: Id
    cast_id: Id
    charge_event_id: Id
    charge_digest: str
    carrier_item_id: Id | None
    mode_id: str | None
    energy: int = Field(default=5, strict=True, ge=5, le=5)


class WitherLimbContactResult(Record):
    attacker_id: Id
    hp_before: int
    hp_after: int
    pending_id: Id
    cast_id: Id
    defender_id: Id
    triggered: bool
    status: Literal["held", "spent"]
    dice: tuple[int, ...] = ()
    injury: int = 0
    injury_checks: tuple[CheckTrace, ...] = ()
    injury_check_reasons: tuple[str, ...] = ()
    outcome: Literal["held", "no-effect", "contact-failed", "resisted", "withered"]
    location: str | None = None
    contact_check: CheckTrace | None = None
    resistance_check: CheckTrace | None = None
    contest_winner: str | None = None
    lasting_id: str | None = None
    recovery_at: int | None = None
    dropped_item_ids: tuple[str, ...] = ()
    grip_checks: tuple[CheckTrace, ...] = ()


class WitherLimbReceipt(Record):
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


def casts(resources: ResourceState) -> dict[str, WitherLimbCast]:
    found: dict[str, WitherLimbCast] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX + "cast:"):
            value = WitherLimbCast.model_validate_json(event.kind)
            found[value.cast_id] = value
    return found


def cast_event(resources: ResourceState, cast_id: str) -> ResourceEvent:
    return next(
        e
        for e in reversed(resources.events)
        if e.id.startswith(PREFIX + "cast:")
        and WitherLimbCast.model_validate_json(e.kind).cast_id == cast_id
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


def attach_contact(
    resources: ResourceState, pending_id: str, contact: WitherLimbContact
) -> ResourceState:
    if pending_id != contact.pending_id:
        raise ConflictError("Melee spell contact pending identity mismatch")
    return append(resources, "contact", pending_id, contact.attacker_id, contact)


def read_contact(resources: ResourceState, pending_id: str) -> WitherLimbContact | None:
    event = next((e for e in resources.events if e.id == identifier("contact", pending_id)), None)
    return WitherLimbContact.model_validate_json(event.kind) if event else None


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    charges: tuple[dict[str, object], ...] = tuple(
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
    results: tuple[dict[str, object], ...] = tuple(
        {
            "pending_id": r.pending_id,
            "cast_id": r.cast_id,
            "attacker_id": r.attacker_id,
            "defender_id": r.defender_id,
            "status": r.status,
            "outcome": r.outcome,
            "triggered": r.triggered,
            "location": r.location,
            "recovery_at": r.recovery_at,
            "dropped_item_ids": r.dropped_item_ids,
        }
        for r in contact_results(resources)
        if r.attacker_id in actor_ids
    )
    return charges + results


def needs_clock_checkpoints(resources: ResourceState) -> bool:
    return bool(pending_actor_ids(resources))


def contact_results(resources: ResourceState) -> tuple[WitherLimbContactResult, ...]:
    return tuple(
        WitherLimbContactResult.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "result:")
    )
