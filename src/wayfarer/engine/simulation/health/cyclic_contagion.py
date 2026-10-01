"""Cyclic infectious exposure via authored Illness relationships (B103-104, B442-443)."""

import hashlib
from decimal import Decimal

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.cyclic import CyclicAttack, CyclicExposure
from wayfarer.engine.rules.types.disease import CONTACT_MODIFIERS, ContactExposure
from wayfarer.engine.simulation.health.cyclic import save as save_cyclic
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError


class ExposeCyclic(Command):
    source_attack_id: str
    relationship_id: str


def _subject(state: ResourceState, actor_id: str) -> None:
    hp = next((p for p in state.pools if p.id == "hp:" + actor_id), None)
    if (
        hp is None
        or hp.injury is None
        or hp.injury.profile_id != "gurps-basic-set-4e-2004"
        or hp.injury.dead
    ):
        raise ValidationError("Cyclic contagion requires a living canonical Basic Set subject")


def expose(
    state: ResourceState,
    world: World,
    command: ExposeCyclic,
    relationship: ContactExposure,
    *,
    ht: int,
    system: bool = False,
) -> ResourceState:
    if not system:
        raise ValidationError("Cyclic contagion requires trusted authored exposure")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Cyclic exposure command ID reused")
        return state
    if state.revision != command.expected_revision:
        raise ConflictError("Cyclic exposure revision changed")
    _subject(state, command.actor_id)
    source = next((a for a in state.cyclic_attacks if a.id == command.source_attack_id), None)
    if (
        source is None
        or not source.active
        or source.contagious == "none"
        or source.contagion_vector is None
    ):
        raise ValidationError("Cyclic carrier is not infectious")
    if (
        relationship.id != command.relationship_id
        or relationship.actor_id != command.actor_id
        or relationship.carrier_id != source.actor_id
        or relationship.disease_id != source.attack_id
        or relationship.vector != source.contagion_vector
        or relationship.occurred_at != state.game_time
        or relationship.actor_id == relationship.carrier_id
    ):
        raise ValidationError("Cyclic contact does not match the trusted infectious relationship")
    if any(
        a.active and a.actor_id == command.actor_id and a.attack_id == source.attack_id
        for a in state.cyclic_attacks
    ):
        raise ValidationError("Cyclic subject is already affected by this infection")
    entities = {e.id: e for e in world.entities}
    if (
        relationship.actor_id not in entities
        or source.actor_id not in entities
        or entities[relationship.actor_id].location_id is None
        or entities[relationship.actor_id].location_id != entities[source.actor_id].location_id
    ):
        raise ValidationError("Cyclic exposure requires its authored world contact context")
    if any(e.relationship.id == relationship.id for e in state.cyclic_exposures):
        raise ConflictError("Cyclic contact relationship was already recorded")
    due = (state.game_time // 86400 + 1) * 86400
    exposure = CyclicExposure(
        id="cyclic-exposure:" + hashlib.sha256(relationship.id.encode()).hexdigest(),
        source=source,
        relationship=relationship,
        ht=ht,
        due=due,
    )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "cyclic_exposures": state.cyclic_exposures + (exposure,),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=exposure.id,
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind="cyclic-exposure-recorded",
                ),
            ),
        }
    )


def settle(state: ResourceState, exposure: CyclicExposure, rng: RandomSource) -> ResourceState:
    if exposure.stage != "exposure" or exposure.due != state.game_time:
        raise ConflictError("Cyclic contagion check is not due")
    relationship = exposure.relationship
    # B443 requires the least advantageous contact modifier. Contacts involving
    # the same source/subject/day are considered together; each retains its receipt.
    contacts = tuple(
        e
        for e in state.cyclic_exposures
        if e.source.attack_id == exposure.source.attack_id
        and e.relationship.actor_id == relationship.actor_id
        and e.due == exposure.due
    )
    contact_modifier = min(
        CONTACT_MODIFIERS[e.relationship.contact] + e.relationship.protection_bonus
        for e in contacts
    )
    immune = next(
        (
            e
            for e in state.cyclic_exposures
            if e.immune
            and e.relationship.actor_id == relationship.actor_id
            and e.source.attack_id == exposure.source.attack_id
        ),
        None,
    )
    check = (
        immune.check
        if immune
        else success_roll(
            "gurps-basic-set-4e-2004",
            exposure.ht + (exposure.source.resistance_modifier or 0) + contact_modifier,
            rng=rng,
        )
    )
    infection_id = (
        "cyclic-infection:"
        + hashlib.sha256(
            f"{exposure.source.id}:{relationship.actor_id}:{exposure.due}".encode()
        ).hexdigest()
    )
    assert check is not None
    infected = not check.outcome.succeeded
    if infected:
        source = exposure.source
        attack = CyclicAttack.model_validate(
            {
                **source.model_dump(),
                "id": infection_id,
                "actor_id": relationship.actor_id,
                "ht": exposure.ht,
                "resistance": 0,
                "armor_divisor": Decimal(1),
                "vulnerability_multiplier": 1,
                "due": exposure.due + source.incubation_seconds,
                "remaining": source.cycle + source.remaining,
                "cycle": 0,
                "hp_debt": 0,
                "fp_debt": 0,
                "active": True,
            }
        )
        state = save_cyclic(state, attack)
    updated = tuple(
        e.model_copy(
            update={
                "stage": "infected" if infected else "resisted",
                "check": check,
                "infection_id": infection_id if infected else None,
                "immune": check.total <= 4,
            }
        )
        if e in contacts
        else e
        for e in state.cyclic_exposures
    )
    return state.model_copy(
        update={
            "cyclic_exposures": updated,
            "events": state.events
            + (
                ResourceEvent(
                    id="cyclic-contagion:" + exposure.id,
                    at=state.game_time,
                    target_id=relationship.actor_id,
                    kind=updated[
                        next(i for i, e in enumerate(updated) if e.id == exposure.id)
                    ].model_dump_json(),
                ),
            ),
        }
    )
