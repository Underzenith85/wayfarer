"""Execute and terminate canonical Cyclic damage, Characters third printing B103-104."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import Modifier, RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.cyclic import CyclicAttack, CyclicOccurrence
from wayfarer.engine.rules.types.cyclic_runtime import additional_symptoms
from wayfarer.engine.rules.types.hazard import RecoveryRestriction
from wayfarer.engine.simulation.equipment.corrosion_state import erode
from wayfarer.engine.simulation.health.cyclic_host_state import binding
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.hit_locations import effective_dr
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.symptoms import register as register_symptoms
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.health.cyclic_types import (
        CyclicContextResolver,
        CyclicTargetContext,
    )


class StopCyclic(Command):
    attack_id: str
    condition: str


def save(state: ResourceState, attack: CyclicAttack) -> ResourceState:
    restriction = RecoveryRestriction(
        id=attack.id,
        actor_id=attack.actor_id,
        active=attack.active,
        hp_debt=attack.hp_debt,
        fp_debt=attack.fp_debt,
        blocks_physician_healing=True,
        blocks_rest=True,
    )
    return state.model_copy(
        update={
            "cyclic_attacks": tuple(a for a in state.cyclic_attacks if a.id != attack.id)
            + (attack,),
            "illnesses": tuple(i for i in state.illnesses if i.id != attack.id) + (restriction,),
        }
    )


def settle(
    state: ResourceState,
    attack: CyclicAttack,
    rng: RandomSource,
    *,
    context: CyclicContextResolver | None = None,
) -> ResourceState:
    if not attack.active or attack.due != state.game_time:
        raise ConflictError("Cyclic occurrence is not due")
    immune = False
    current: CyclicTargetContext | None = None
    modifiers: tuple[Modifier, ...] = ()
    host = binding(state, attack.id)
    if host is not None:
        if context is None:
            raise ConflictError("Host-bound Cyclic deadline requires current target context")
        current = context(state, attack, attack.actor_id)
        immune = current.immune_to_damage
        modifiers = current.resistance_modifiers
        attack = attack.model_copy(
            update={
                "ht": current.ht,
                "resistance": current.resistance,
                "vulnerability_multiplier": current.vulnerability_multiplier,
            }
        )
    location = None if host is None or host.disease else host.location
    occurrence = attack.id + ":" + str(attack.cycle + 1)
    # B443: the failed exposure already decides infection; delayed onset is
    # its first damage. New host disease rolls again only on later cycles.
    # Legacy reducer records retain their original initial-cycle check.
    check = (
        None
        if immune
        or attack.resistance_modifier is None
        or (host is not None and host.disease and attack.cycle == 0)
        else success_roll(
            "gurps-basic-set-4e-2004", attack.ht + attack.resistance_modifier, modifiers, rng=rng
        )
    )
    resisted = check is not None and check.outcome.succeeded
    armor_only = _immune_armor(current)
    dice = () if resisted or (immune and not armor_only) else draw_dice(rng, attack.damage_dice)
    damage = sum(dice)
    hp_lost = fp_lost = 0
    state = _erode_immune_armor(state, current, occurrence, damage)
    if not resisted and not immune:
        if attack.damage_type == "fat":
            amount = (
                max(
                    0,
                    damage
                    - (
                        0
                        if host is not None and (host.bypass_dr or host.disease)
                        else effective_dr(
                            attack.resistance,
                            attack.armor_divisor,
                            location=location or "torso",
                            damage_type="fat",
                        )
                    ),
                )
                * attack.vulnerability_multiplier
            )
            state, result = apply_fatigue(
                state,
                FatigueCost(
                    id=occurrence,
                    actor_id=attack.actor_id,
                    expected_revision=state.revision,
                    amount=amount,
                    attack_damage=True,
                ),
                ht=attack.ht,
                rng=rng,
                system=True,
            )
            hp_lost, fp_lost = result.hp_lost, result.fp_lost
        else:
            state, injury = apply_injury(
                state,
                Wound(
                    id=occurrence,
                    actor_id=attack.actor_id,
                    expected_revision=state.revision,
                    basic_damage=damage,
                    resistance=attack.resistance,
                    damage_type=attack.damage_type,
                    location=location,
                    injury_source="internal" if host is not None and host.disease else "attack",
                    armor_divisor=attack.armor_divisor,
                    vulnerability_multiplier=Decimal(attack.vulnerability_multiplier),
                ),
                ht=attack.ht,
                rng=rng,
                system=True,
                held_item_ids=() if current is None else current.held_item_ids,
                held_item_locations=() if current is None else current.held_item_locations,
                shield_item_ids=() if current is None else current.shield_item_ids,
                dx=None if current is None else current.dx,
                ignore_dr=host is not None and (host.bypass_dr or host.disease),
                corrosion=None if current is None else current.corrosion,
            )
            hp_lost = injury.injury
    if attack.symptom_spec is not None and attack.symptom_source_id is not None and not resisted:
        state = register_symptoms(
            state,
            actor_id=attack.actor_id,
            source_id=attack.symptom_source_id,
            injury_id=occurrence,
            amount=fp_lost if attack.damage_type == "fat" else hp_lost,
            pool_id=("fp:" if attack.damage_type == "fat" else "hp:") + attack.actor_id,
            spec=attack.symptom_spec,
            additional_specs=additional_symptoms(attack),
            restriction_id=attack.id,
        )
    remaining = attack.remaining - 1
    attack = attack.model_copy(
        update={
            "remaining": remaining,
            "active": not resisted and remaining > 0,
            "due": attack.due + attack.interval,
            "cycle": attack.cycle + 1,
            "hp_debt": attack.hp_debt + hp_lost,
            "fp_debt": attack.fp_debt + fp_lost,
        }
    )
    state = save(state, attack)
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id="cyclic:" + occurrence,
                    at=state.game_time,
                    kind=CyclicOccurrence(
                        id=occurrence,
                        attack_id=attack.id,
                        actor_id=attack.actor_id,
                        at=state.game_time,
                        cycle=attack.cycle,
                        damage_dice=dice,
                        check=check,
                        hp_lost=hp_lost,
                        fp_lost=fp_lost,
                    ).model_dump_json(),
                    target_id=attack.actor_id,
                ),
            )
        }
    )


def _immune_armor(current: CyclicTargetContext | None) -> bool:
    return (
        current is not None
        and current.immune_to_damage
        and current.corrosion is not None
        and current.corrosion.item_id is not None
        and current.corrosion.resistance > 0
    )


def _erode_immune_armor(
    state: ResourceState, current: CyclicTargetContext | None, occurrence: str, damage: int
) -> ResourceState:
    if not _immune_armor(current):
        return state
    assert current is not None and current.corrosion is not None
    # A dead wearer gains no injury or recovery debt; its worn armor remains
    # exposed to this independently rolled external corrosion packet (B379).
    return erode(state, current.corrosion, occurrence, damage)


def stop(state: ResourceState, command: StopCyclic, *, system: bool = False) -> ResourceState:
    if not system:
        raise ValidationError("Cyclic stopping condition requires trusted adjudication")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    old = next((r for r in state.receipts if r.command_id == command.id), None)
    if old:
        if old.digest != digest:
            raise ConflictError("Cyclic command ID reused")
        return state
    if state.revision != command.expected_revision:
        raise ConflictError("Cyclic revision changed")
    attack = next(
        (
            a
            for a in state.cyclic_attacks
            if a.id == command.attack_id and a.actor_id == command.actor_id
        ),
        None,
    )
    if attack is None or not attack.active or attack.stop_condition != command.condition:
        raise ValidationError("Cyclic stopping condition does not match the active attack")
    state = save(state, attack.model_copy(update={"active": False}))
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id="cyclic-stop:" + command.id,
                    at=state.game_time,
                    target_id=attack.actor_id,
                    kind="cyclic-stopped",
                ),
            ),
        }
    )
