"""Profile-bound migration and defense geometry inside CombatService's transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.simulation.combat import CombatEngine, Encounter
from wayfarer.simulation.hex_geometry import RetreatContext, can_retreat
from wayfarer.simulation.tactical import defense_adjustment, occupants, pose, validate_hex_encounter

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState
    from wayfarer.simulation.combat_commands import ChooseDefense, MigrateEncounterHex
    from wayfarer.simulation.rules_context import RulesContext


def migrate(runtime: RulesContext, encounter: Encounter, command: MigrateEncounterHex) -> Encounter:
    if (
        encounter.spatial_kind == "hex"
        or encounter.status != "active"
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.wait_interrupt
        or encounter.grips
        or encounter.close_pairs
        or encounter.blocked_reason
    ):
        raise ConflictError(
            "Migration requires an unmigrated encounter with no pending interaction"
        )
    poses = {p.actor_id: p.pose for p in command.placements}
    if len(poses) != len(command.placements) or set(poses) != set(encounter.turn_order):
        raise ValidationError("Migration requires exactly one explicit pose for every participant")
    if any(p.posture not in ("standing", "kneeling", "lying") for p in poses.values()):
        raise ValidationError("Unsupported combat posture")
    for actor in encounter.participants:
        if poses[actor.actor_id].posture != (
            "lying" if actor.posture == "prone" else actor.posture
        ):
            raise ValidationError("Map migration cannot change posture")
    result = encounter.model_copy(
        update={
            "spatial_kind": "hex",
            "battlefield_id": command.battlefield.id,
            "participants": tuple(
                p.model_copy(
                    update={
                        "position": poses[p.actor_id].position,
                        "hex_facing": poses[p.actor_id].facing,
                    }
                )
                for p in encounter.participants
            ),
        }
    )
    rules = runtime.rules.combat
    validate_hex_encounter(
        result, rules.gurps_equipment if rules else None, board=runtime.hex_map(result)
    )
    return result


def prepare_defense(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    if encounter.spatial_kind != "hex":
        if command.retreat is not None:
            raise ValidationError("Retreat requires a migrated hex encounter")
        return encounter
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    attacker_id = pending.attacker_id if pending else unarmed.actor_id if unarmed else None
    defender_id = pending.defender_id if pending else unarmed.target_id if unarmed else None
    if defender_id != command.actor_id or attacker_id is None:
        raise ValidationError("Defense is unavailable")
    actor = next(p for p in encounter.participants if p.actor_id == attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == defender_id)
    bonus = defense_adjustment(encounter, actor, target) if command.defense != "none" else 0
    if command.retreat is not None:
        from wayfarer.simulation.gurps_equipment import RangedMode
        from wayfarer.simulation.mechanics.gurps_melee import mode

        if command.defense == "none" or command.second_defense is not None:
            raise ValidationError("Retreat requires one active defense")
        if pending and isinstance(
            mode(runtime, state, actor.actor_id, pending.weapon_id, pending.mode_id), RangedMode
        ):
            raise ValidationError("Retreat bonus is not available against ranged attacks")
        if unarmed and unarmed.action in ("grapple", "arm_lock"):
            raise ValidationError(
                "Retreat against control attacks requires following-grapple timing"
            )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
        context = RetreatContext(
            already_retreated=target.retreat_used,
            stunned=bool(hp.injury and hp.injury.stunned),
            grappled=target.grappled
            or target.pinned
            or any(g.holder_id == target.actor_id for g in encounter.grips),
            maneuver_allows_retreat=not target.maneuver_state.defense_forbidden,
        )
        if not can_retreat(
            runtime.require_hex(encounter),
            pose(target),
            pose(actor).position,
            command.retreat,
            context=context,
            occupants=occupants(encounter),
        ):
            raise ValidationError("Retreat is unavailable")
        bonus += 3 if command.defense == "dodge" else 1
    elif target.retreat_attacker_id == actor.actor_id and command.defense != "none":
        bonus += 3 if command.defense == "dodge" else 1
    if command.retreat is not None:
        target = target.model_copy(
            update={"retreat_used": True, "retreat_attacker_id": actor.actor_id}
        )
    return CombatEngine._replace(
        encounter, target.model_copy(update={"tactical_defense_bonus": bonus})
    )


def finish_defense(
    runtime: RulesContext, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    if encounter.spatial_kind != "hex":
        return encounter
    target = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    target = target.model_copy(update={"tactical_defense_bonus": 0})
    if command.retreat is not None:
        target = target.model_copy(update={"position": command.retreat})
    encounter = CombatEngine._replace(encounter, target)
    pending = encounter.defense_history[-1].pending if encounter.defense_history else None
    if pending and (pending.post_attack_hex_path or pending.post_attack_facing is not None):
        from wayfarer.simulation.tactical import move_hex

        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        attacker = move_hex(
            encounter,
            attacker,
            "attack",
            pending.post_attack_hex_path,
            pending.post_attack_facing,
            None,
            board=runtime.hex_map(encounter),
        )
        encounter = CombatEngine._replace(encounter, attacker)
    return encounter
