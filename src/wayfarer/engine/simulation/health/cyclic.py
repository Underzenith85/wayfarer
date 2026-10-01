"""Execute and terminate canonical Cyclic damage, Characters third printing B103-104."""

import hashlib
from decimal import Decimal

from wayfarer.engine.rules.checks import RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.cyclic import CyclicAttack, CyclicOccurrence
from wayfarer.engine.rules.types.hazard import RecoveryRestriction
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.hit_locations import effective_dr
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.symptoms import register as register_symptoms
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError


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


def settle(state: ResourceState, attack: CyclicAttack, rng: RandomSource) -> ResourceState:
    if not attack.active or attack.due != state.game_time:
        raise ConflictError("Cyclic occurrence is not due")
    occurrence = attack.id + ":" + str(attack.cycle + 1)
    check = (
        None
        if attack.resistance_modifier is None
        else success_roll(
            "gurps-basic-set-4e-2004", attack.ht + attack.resistance_modifier, rng=rng
        )
    )
    resisted = check is not None and check.outcome.succeeded
    dice = () if resisted else draw_dice(rng, attack.damage_dice)
    damage = sum(dice)
    hp_lost = fp_lost = 0
    if not resisted:
        if attack.damage_type == "fat":
            amount = (
                max(
                    0,
                    damage
                    - effective_dr(
                        attack.resistance, attack.armor_divisor, location="torso", damage_type="fat"
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
                    armor_divisor=attack.armor_divisor,
                    vulnerability_multiplier=Decimal(attack.vulnerability_multiplier),
                ),
                ht=attack.ht,
                rng=rng,
                system=True,
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
