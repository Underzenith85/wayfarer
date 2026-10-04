"""Private immutable actual-punch contact records for hand Melee generation one."""

import hashlib
from typing import Literal

from pydantic import Field, field_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.location import Hand, HumanLocation
from wayfarer.engine.simulation.magic.hand_melee_spell_state import PREFIX, append, identifier
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError
from wayfarer.models import Id, Record


class HandContact(Record):
    generation: Literal[1] = 1
    pending_id: Id
    pending_digest: str
    command_id: Id
    encounter_id: Id
    attacker_id: Id
    defender_id: Id
    hand: Hand
    cast_id: Id
    charge_event_id: Id
    charge_digest: str
    build_revision: Id
    body_digest: str
    mana_event_id: Id
    mana_event_digest: str
    energy: int = Field(strict=True, ge=1, le=3)

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("Hand contact generation must be exact integer 1")
        return 1


class HandContactResult(Record):
    generation: Literal[1] = 1
    pending_id: Id
    contact_digest: str
    cast_id: Id
    attacker_id: Id
    defender_id: Id
    hand: Hand
    status: Literal["held", "spent"]
    outcome: Literal["held", "discharged", "no-effect"]
    triggered: bool
    ordinary_hit: bool
    actual_defense: str
    defense_check: CheckTrace | None
    defense_implement_id: str | None
    location: HumanLocation | None
    hp_before: int
    hp_after: int
    dice: tuple[int, ...] = ()
    injury: int = 0
    injury_checks: tuple[CheckTrace, ...] = ()
    injury_check_reasons: tuple[str, ...] = ()

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("Hand contact result generation must be exact integer 1")
        return 1


def contact_digest(contact: HandContact) -> str:
    return hashlib.sha256(contact.model_dump_json().encode()).hexdigest()


def read_contact(resources: ResourceState, pending_id: str) -> HandContact | None:
    event = next((e for e in resources.events if e.id == identifier("contact", pending_id)), None)
    if event is None:
        return None
    contact = HandContact.model_validate_json(event.kind)
    if contact.pending_id != pending_id or event.target_id != contact.attacker_id:
        raise ConflictError("Hand contact immutable event identity changed")
    return contact


def attach_contact(
    resources: ResourceState, pending_id: str, contact: HandContact
) -> ResourceState:
    if pending_id != contact.pending_id:
        raise ConflictError("Hand contact pending identity mismatch")
    return append(resources, "contact", pending_id, contact.attacker_id, contact)


def contact_results(resources: ResourceState) -> tuple[HandContactResult, ...]:
    return tuple(
        HandContactResult.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "result:")
    )


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "pending_id": r.pending_id,
            "cast_id": r.cast_id,
            "attacker_id": r.attacker_id,
            "defender_id": r.defender_id,
            "hand": r.hand,
            "status": r.status,
            "outcome": r.outcome,
            "triggered": r.triggered,
            "location": r.location,
        }
        for r in contact_results(resources)
        if r.attacker_id in actor_ids
    )
