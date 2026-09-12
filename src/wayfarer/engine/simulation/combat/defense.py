"""The defense a paused attack is waiting on."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.tables.combat import maneuver_move_allowance
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import (
    CombatResult,
    DefenseChoice,
    Encounter,
)
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.engine import CombatEngine


def choose_defense(
    engine: CombatEngine, encounter: Encounter, *, actor_id: str, selected: Defense
) -> tuple[Encounter, CombatResult]:
    pending = encounter.pending_defense
    if pending is None:
        raise ConflictError("Encounter is not waiting for a defense")
    if actor_id != pending.defender_id or selected not in pending.allowed:
        raise ValidationError("Defense is not available to this actor")
    defender = next(p for p in encounter.participants if p.actor_id == actor_id)
    if (
        engine.rules.gurps_equipment is None
        and not defender.reaction_available
        and selected != "none"
    ):
        raise ValidationError("Defender has no reaction available")
    defender = defender.model_copy(
        update={
            "reaction_available": False if selected != "none" else defender.reaction_available,
            "maneuver_state": defender.maneuver_state.model_copy(update={"defended": True})
            if selected != "none"
            else defender.maneuver_state,
        }
    )
    choice = DefenseChoice(pending=pending, selected=selected, chosen_by=actor_id)
    encounter = engine._replace(encounter, defender).model_copy(
        update={
            "pending_defense": None,
            "defense_history": encounter.defense_history + (choice,),
        }
    )
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    suppression_queue = tuple(
        attack
        for attack in pending.suppression_attacks
        if any(
            zone.id == attack.zone_id and zone.remaining_hits > 0
            for zone in encounter.suppression_zones
        )
    )
    if (
        engine.rules.gurps_equipment is not None
        and pending.suppression_zone_id is not None
        and suppression_queue
        and not encounter.blocked_reason
    ):
        suppression_following, *suppression_remaining = suppression_queue
        encounter = encounter.model_copy(
            update={
                "pending_defense": pending.model_copy(
                    update={
                        "id": "suppression:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                        "attacker_id": suppression_following.attacker_id,
                        "weapon_id": suppression_following.weapon_id,
                        "mode_id": suppression_following.mode_id,
                        "shots": suppression_following.shots,
                        "spray_targets": (),
                        "spray_recoil_penalty": 0,
                        "traversal_shots": 0,
                        "suppression_zone_id": suppression_following.zone_id,
                        "suppression_attacks": tuple(suppression_remaining),
                        "suppression_remaining_hits": suppression_following.remaining_hits,
                        "suppression_aim_bonus": suppression_following.aim_bonus,
                        "suppression_skill_cap": suppression_following.skill_cap,
                    }
                )
            }
        )
    elif (
        engine.rules.gurps_equipment is not None
        and pending.spray_targets
        and not encounter.blocked_reason
    ):
        spray_following, *spray_remaining = pending.spray_targets
        encounter = encounter.model_copy(
            update={
                "pending_defense": pending.model_copy(
                    update={
                        "id": "spray:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                        "defender_id": spray_following.target_id,
                        "shots": spray_following.shots,
                        "hit_location": spray_following.hit_location,
                        "target_item_id": None,
                        "spray_targets": tuple(spray_remaining),
                        "spray_recoil_penalty": spray_following.recoil_penalty,
                        "traversal_shots": spray_following.traversal_shots,
                        "post_attack_destination": None,
                        "post_attack_square_facing": None,
                        "post_attack_hex_path": (),
                        "post_attack_facing": None,
                        "post_attack_posture": None,
                    }
                )
            }
        )
    elif (
        engine.rules.gurps_equipment is not None
        and attacker.maneuver_state.attacks_remaining
        and not encounter.blocked_reason
    ):
        commitment = attacker.maneuver_state
        second_item = commitment.second_attack_item_id or pending.weapon_id
        second_target = commitment.second_attack_target_id or pending.defender_id
        if second_item not in attacker.ready_item_ids:
            commitment = commitment.model_copy(
                update={"attacks_remaining": 0}
            ).consume_attack_setup()
            attacker = attacker.model_copy(update={"maneuver_state": commitment})
            encounter = engine._replace(encounter, attacker)
            encounter = engine._advance(encounter)
            return (
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.defense_recorded",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    available=engine.available(encounter, encounter.current_actor_id),
                ),
            )
        attacker = attacker.model_copy(
            update={
                "maneuver_state": commitment.model_copy(
                    update={
                        "attacks_remaining": 0,
                        "attack_bonus": commitment.second_attack_penalty,
                    }
                )
            }
        )
        encounter = engine._replace(encounter, attacker).model_copy(
            update={
                "pending_defense": pending.model_copy(
                    update={
                        "id": "second:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                        "weapon_id": second_item,
                        "defender_id": second_target,
                        "mode_id": commitment.second_attack_mode_id or pending.mode_id,
                        "post_attack_destination": None,
                        "post_attack_square_facing": None,
                        "post_attack_hex_path": (),
                        "post_attack_facing": None,
                        "post_attack_posture": None,
                    }
                )
            }
        )
    else:
        if (
            pending.post_attack_destination is not None
            or pending.post_attack_square_facing is not None
        ):
            if not isinstance(attacker.position, GridPoint):
                raise ValidationError("Square step requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != attacker.actor_id and isinstance(p.position, GridPoint)
            }
            limit = maneuver_move_allowance("attack", attacker.movement_allowance, attacker.posture)
            destination = pending.post_attack_destination or attacker.position
            if destination in occupied:
                raise ValidationError("Post-attack step cannot end in an occupied position")
            blockers = {
                p.position
                for p in encounter.participants
                if p.actor_id != attacker.actor_id
                and isinstance(p.position, GridPoint)
                and encounter.blocks_passage(attacker.actor_id, p.actor_id)
            }
            if not engine._reachable(
                engine.battlefields[encounter.battlefield_id],
                attacker.position,
                destination,
                limit,
                blockers,
            ):
                raise ValidationError("Post-attack step is no longer available")
            attacker = attacker.model_copy(
                update={
                    "position": destination,
                    "facing": pending.post_attack_square_facing or attacker.facing,
                }
            )
            encounter = engine._replace(encounter, attacker)
        if pending.post_attack_posture is not None:
            if {attacker.posture, pending.post_attack_posture} != {"standing", "kneeling"}:
                raise ValidationError("Post-attack posture step is invalid")
            attacker = attacker.model_copy(update={"posture": pending.post_attack_posture})
            encounter = engine._replace(encounter, attacker)
        if pending.post_attack_crouch:
            attacker = attacker.model_copy(update={"posture": "crouching"})
        attacker = attacker.model_copy(
            update={"maneuver_state": attacker.maneuver_state.consume_attack_setup()}
        )
        encounter = engine._replace(encounter, attacker)
        encounter = engine._advance(encounter)
    return (
        encounter,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.defense_recorded",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=engine.available(encounter, encounter.current_actor_id),
        ),
    )
