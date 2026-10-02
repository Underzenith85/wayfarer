"""Cyclic infectious exposure via authored Illness relationships (B103-104, B442-443)."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, Modifier, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.cyclic import (
    CyclicAttack,
    CyclicExposure,
    ZeroDamageCyclicAttack,
    ZeroDamageCyclicExposure,
)
from wayfarer.engine.rules.types.disease import CONTACT_MODIFIERS, ContactExposure
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.health.cyclic import save as save_cyclic
from wayfarer.engine.simulation.health.cyclic_host_state import bind_occurrence, binding
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.health.cyclic_types import CyclicContextResolver


class ExposeCyclic(Command):
    source_attack_id: str
    relationship_id: str


def _subject(state: ResourceState, actor_id: str) -> InjuryStatus:
    hp = next((p for p in state.pools if p.id == "hp:" + actor_id), None)
    if (
        hp is None
        or hp.injury is None
        or hp.injury.profile_id != "gurps-basic-set-4e-2004"
        or hp.injury.dead
    ):
        raise ValidationError("Cyclic contagion requires a living canonical Basic Set subject")
    return hp.injury


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
    subject = _subject(state, command.actor_id)
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
    exposure_type = ZeroDamageCyclicExposure if source.basic_damage == 0 else CyclicExposure
    exposure = exposure_type.model_validate(
        {
            "id": "cyclic-exposure:" + hashlib.sha256(relationship.id.encode()).hexdigest(),
            "source": source,
            "relationship": relationship,
            "ht": ht,
            "due": due,
            "stage": "resisted"
            if binding(state, source.id) is not None
            and source.damage_type == "tox"
            and subject.machine
            else "exposure",
        }
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


def settle(
    state: ResourceState,
    exposure: CyclicExposure,
    rng: RandomSource,
    *,
    context: CyclicContextResolver | None = None,
) -> ResourceState:
    if exposure.stage != "exposure" or exposure.due != state.game_time:
        raise ConflictError("Cyclic contagion check is not due")
    relationship = exposure.relationship
    host = binding(state, exposure.source.id)
    current_ht = exposure.ht
    body_immune = False
    modifiers: tuple[Modifier, ...] = ()
    if host is not None:
        if context is None:
            raise ConflictError("Host-bound Cyclic exposure requires current target context")
        current = context(state, exposure.source, relationship.actor_id)
        current_ht = current.ht
        modifiers = current.resistance_modifiers
        body_immune = current.immune_to_damage
    # B443 requires the least advantageous contact modifier. Contacts involving
    # the same source/subject/day are considered together; each retains its receipt.
    contacts = tuple(
        e
        for e in state.cyclic_exposures
        if e.source.attack_id == exposure.source.attack_id
        and e.relationship.actor_id == relationship.actor_id
        and e.due == exposure.due
        and (host is None or e.stage == "exposure")
    )
    if body_immune:
        # B61-62 ordinary fatigue/toxic damage cannot affect machines. This is
        # current physiology, not B443 permanent natural immunity to a disease.
        return _finish_contacts(
            state, exposure, contacts, infection_id=None, check=None, immune=False
        )
    if host is not None:
        existing = next(
            (
                a
                for a in state.cyclic_attacks
                if a.active
                and a.actor_id == relationship.actor_id
                and a.attack_id == exposure.source.attack_id
            ),
            None,
        )
        if existing is not None:
            # A prior day's contact may become due after a different contact
            # already established this illness. Keep its existing clock/debt.
            return _finish_contacts(
                state, exposure, contacts, infection_id=existing.id, check=None, immune=False
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
            current_ht + (exposure.source.resistance_modifier or 0) + contact_modifier,
            modifiers,
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
    first_damage = exposure.due + exposure.source.incubation_seconds
    if host is not None:
        # B443 delay starts at actual contact, not at the later discovery roll.
        # Earlier deadlines can only enter through the host's explicit GM
        # deferral choice, recorded in that exposure's private observation.
        first_damage = max(
            exposure.due,
            min(e.relationship.occurred_at + e.source.incubation_seconds for e in contacts),
        )
    if infected:
        source = exposure.source
        attack_type = ZeroDamageCyclicAttack if source.basic_damage == 0 else CyclicAttack
        attack = attack_type.model_validate(
            {
                **source.model_dump(),
                "id": infection_id,
                "actor_id": relationship.actor_id,
                "ht": current_ht,
                "resistance_modifier": (source.resistance_modifier or 0)
                if host is not None
                else source.resistance_modifier,
                "resistance": 0,
                "armor_divisor": Decimal(1),
                "vulnerability_multiplier": 1,
                "due": first_damage,
                "remaining": source.cycle + source.remaining,
                "cycle": 0,
                "hp_debt": 0,
                "fp_debt": 0,
                "active": True,
            }
        )
        state = save_cyclic(state, attack)
        if host is not None:
            state = bind_occurrence(
                state,
                attack.id,
                source_id=host.source_id,
                source_revision=host.source_revision,
                policy=host.policy,
                bypass_dr=True,
                disease=True,
            )
    first_attempt = not any(
        e.stage != "exposure"
        and e.check is not None
        and e.source.attack_id == exposure.source.attack_id
        and e.relationship.actor_id == relationship.actor_id
        for e in state.cyclic_exposures
    )
    return _finish_contacts(
        state,
        exposure,
        contacts,
        infection_id=infection_id if infected else None,
        check=check,
        immune=check.total <= 4 and (host is None or first_attempt or immune is not None),
    )


def _finish_contacts(
    state: ResourceState,
    exposure: CyclicExposure,
    contacts: tuple[CyclicExposure, ...],
    *,
    infection_id: str | None,
    check: CheckTrace | None,
    immune: bool,
) -> ResourceState:
    relationship = exposure.relationship
    updated = tuple(
        e.model_copy(
            update={
                "stage": "infected" if infection_id is not None else "resisted",
                "check": check,
                "infection_id": infection_id,
                "immune": immune,
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
