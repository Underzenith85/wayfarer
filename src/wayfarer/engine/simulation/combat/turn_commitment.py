"""Build the maneuver commitment recorded for one combat turn."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    Encounter,
    basic_distance,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    DefenseOption,
    ManeuverState,
    WaitTrigger,
)
from wayfarer.engine.simulation.combat.vocabulary import Maneuver
from wayfarer.engine.simulation.magic.spells import active_spells
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.engine import CombatEngine


def _carried_state(participant: Combatant, maneuver: Maneuver) -> ManeuverState:
    old = participant.maneuver_state
    if participant.last_maneuver != "evaluate":
        old = old.model_copy(update={"evaluate_target_id": None, "evaluate_bonus": 0})
    if participant.last_maneuver != "feint":
        old = old.model_copy(update={"feint_target_id": None, "feint_penalty": 0})
    if maneuver not in ATTACK_MANEUVERS and maneuver != "feint":
        return ManeuverState()
    return ManeuverState(
        aim_item_id=old.aim_item_id,
        aim_target_id=old.aim_target_id,
        aim_mode_id=old.aim_mode_id,
        aim_seconds=old.aim_seconds if participant.last_maneuver == "aim" else 0,
        aim_accuracy=old.aim_accuracy,
        aim_braced=old.aim_braced,
        aim_sight_bonus=old.aim_sight_bonus,
        evaluate_target_id=old.evaluate_target_id,
        evaluate_bonus=old.evaluate_bonus,
        feint_target_id=old.feint_target_id,
        feint_penalty=old.feint_penalty,
        stop_thrust_damage_bonus=old.stop_thrust_damage_bonus,
    )


def _all_out_attack(
    participant: Combatant,
    commitment: ManeuverState,
    *,
    item_id: str | None,
    target_id: str | None,
    attack_option: AttackOption | None,
    second_item_id: str | None,
    second_target_id: str | None,
    second_mode_id: str | None,
) -> ManeuverState:
    if attack_option is None:
        raise ValidationError("Choose an All-Out Attack option")
    commitment = commitment.model_copy(
        update={
            "defense_forbidden": True,
            "attack_bonus": 4 if attack_option == "determined" else 0,
            "strong": attack_option == "strong",
            "attacks_remaining": int(attack_option == "double"),
            "second_attack_item_id": second_item_id,
            "second_attack_target_id": second_target_id,
            "second_attack_mode_id": second_mode_id,
        }
    )
    if attack_option != "double":
        if any(v is not None for v in (second_item_id, second_target_id, second_mode_id)):
            raise ValidationError("Second attack choices require All-Out Attack (Double)")
        return commitment
    if second_target_id not in (None, target_id):
        raise ValidationError("All-Out Attack (Double) attacks the same foe")
    if second_item_id is None:
        return commitment
    hands = {item: hand for item, hand in participant.hand_bindings}
    if (
        second_item_id == item_id
        or second_item_id not in participant.ready_item_ids
        or item_id not in hands
        or second_item_id not in hands
        or hands[item_id] == hands[second_item_id]
    ):
        raise ValidationError("Double requires two distinct ready one-hand weapons")
    return commitment.model_copy(
        update={
            "second_attack_target_id": target_id,
            "attack_bonus": -4 if hands[item_id] == "left-hand" else 0,
            "second_attack_penalty": -4 if hands[second_item_id] == "left-hand" else 0,
        }
    )


def _wait(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    resources: ResourceState,
    commitment: ManeuverState,
    *,
    actor_id: str,
    basic: bool,
    wait_trigger: WaitTrigger | None,
) -> ManeuverState:
    if (
        wait_trigger is None
        or wait_trigger.actor_id == actor_id
        or (wait_trigger.actor_id is not None and wait_trigger.actor_id not in encounter.turn_order)
    ):
        raise ValidationError("Wait requires an observable other combatant trigger")
    held_missile = any(
        effect.actor_id == actor_id
        and effect.spell_id == "fireball"
        and effect.execute_effects
        and wait_trigger.item_id == "spell:" + hashlib.sha256(effect.cast_id.encode()).hexdigest()
        for effect in active_spells(resources)
    )
    if held_missile and (wait_trigger.reaction != "attack" or wait_trigger.mode_id is not None):
        raise ValidationError("Held missile Wait supports its declared release only")
    if (
        wait_trigger.unarmed is None
        and wait_trigger.item_id not in participant.ready_item_ids
        and wait_trigger.reaction != "ready"
        and not held_missile
    ):
        raise ValidationError("Wait attack requires a ready weapon")
    if (
        wait_trigger.reaction != "ready"
        and wait_trigger.reaction_target_id not in encounter.turn_order
    ):
        raise ValidationError("Wait attack requires a declared target")
    if (wait_trigger.reaction == "all_out_attack") != (wait_trigger.attack_option is not None):
        raise ValidationError("Wait All-Out Attack requires its option in advance")
    _validate_wait_zone(engine, encounter, wait_trigger)
    if wait_trigger.stop_thrust:
        if basic:
            raise ValidationError("Basic stop thrust requires explicit GM adjudication")
        if (
            wait_trigger.actor_id is None
            or wait_trigger.reaction_target_id != wait_trigger.actor_id
        ):
            raise ValidationError("Stop thrust requires one declared charging foe")
    return commitment.model_copy(update={"wait": wait_trigger})


def _validate_wait_zone(
    engine: CombatEngine, encounter: Encounter, wait_trigger: WaitTrigger
) -> None:
    if not wait_trigger.zone:
        return
    if encounter.spatial_kind != "hex":
        raise ValidationError("Wait zones require an explicit hex battlefield")
    cells = {(cell.position.q, cell.position.r) for cell in engine.require_hex(encounter).cells}
    if not set(wait_trigger.zone) <= cells:
        raise ValidationError("Wait zone is outside the battlefield")


def _observation(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    commitment: ManeuverState,
    *,
    actor_id: str,
    maneuver: Maneuver,
    target_id: str | None,
    item_id: str | None,
    basic: bool,
) -> ManeuverState:
    target = next((p for p in encounter.participants if p.actor_id == target_id), None)
    if target is None or target.actor_id == actor_id:
        raise ValidationError("Maneuver requires another combatant target")
    reach = participant.reach + (participant.movement_allowance if maneuver == "evaluate" else 0)
    distance = (
        basic_distance(encounter, participant.actor_id, target.actor_id)
        if basic
        else engine.distance(participant.position, target.position)
    )
    if maneuver != "aim" and distance > reach:
        raise ValidationError("Maneuver target is outside melee reach")
    old = participant.maneuver_state
    if maneuver == "evaluate":
        return commitment.model_copy(
            update={
                "evaluate_target_id": target_id,
                "evaluate_bonus": (
                    min(3, old.evaluate_bonus + 1) if old.evaluate_target_id == target_id else 1
                ),
            }
        )
    if maneuver == "aim":
        if item_id not in participant.ready_item_ids:
            raise ValidationError("Aim requires a ready ranged weapon")
        return commitment.model_copy(
            update={
                "aim_item_id": item_id,
                "aim_target_id": target_id,
                "aim_seconds": (
                    min(3, old.aim_seconds + 1)
                    if (old.aim_item_id, old.aim_target_id) == (item_id, target_id)
                    else 1
                ),
            }
        )
    return commitment


def prepare(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    resources: ResourceState,
    *,
    actor_id: str,
    maneuver: Maneuver,
    item_id: str | None,
    target_id: str | None,
    attack_option: AttackOption | None,
    defense_option: DefenseOption | None,
    wait_trigger: WaitTrigger | None,
    second_item_id: str | None,
    second_target_id: str | None,
    second_mode_id: str | None,
    basic: bool,
) -> Combatant:
    """Return the participant with the maneuver's lasting commitments recorded."""
    if any(v is not None for v in (second_item_id, second_target_id, second_mode_id)) and not (
        maneuver == "all_out_attack" and attack_option == "double"
    ):
        raise ValidationError("A second attack requires All-Out Attack (Double)")
    commitment = _carried_state(participant, maneuver)
    if maneuver == "all_out_attack":
        commitment = _all_out_attack(
            participant,
            commitment,
            item_id=item_id,
            target_id=target_id,
            attack_option=attack_option,
            second_item_id=second_item_id,
            second_target_id=second_target_id,
            second_mode_id=second_mode_id,
        )
    elif maneuver == "move_and_attack":
        commitment = commitment.model_copy(
            update={"attack_bonus": -4, "attack_cap": 9, "parry_forbidden": True}
        )
    elif maneuver == "concentrate":
        old = participant.maneuver_state
        commitment = commitment.model_copy(
            update={
                "concentrating": True,
                "concentration_seconds": old.concentration_seconds + 1 if old.concentrating else 1,
            }
        )
    elif maneuver == "all_out_defense":
        if defense_option is None:
            raise ValidationError("Choose the enhanced active defense")
        commitment = commitment.model_copy(update={"enhanced_defense": defense_option})
    elif maneuver == "wait":
        commitment = _wait(
            engine,
            encounter,
            participant,
            resources,
            commitment,
            actor_id=actor_id,
            basic=basic,
            wait_trigger=wait_trigger,
        )
    elif maneuver in ("evaluate", "aim", "feint"):
        commitment = _observation(
            engine,
            encounter,
            participant,
            commitment,
            actor_id=actor_id,
            maneuver=maneuver,
            target_id=target_id,
            item_id=item_id,
            basic=basic,
        )
    return participant.model_copy(update={"maneuver_state": commitment})
