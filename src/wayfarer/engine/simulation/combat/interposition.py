"""Source B375 ordinary weapon interposition through existing attack reducers."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_distance, move_basic
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex, distance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def prepare_interposition(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    if command.sacrificial_for is None:
        return encounter
    pending = encounter.pending_defense
    if (
        pending is None
        or pending.defender_id != command.sacrificial_for
        or command.actor_id in (pending.attacker_id, pending.defender_id)
        or command.defense != "dodge"
        or command.second_defense is not None
        or command.basic_retreat
        or command.retreat is not None
        or command.catch_thrown
    ):
        raise ValidationError(
            "Sacrificial Dodge requires another actor's pending weapon attack and no retreat"
        )
    if (
        pending.target_item_id
        or pending.spell_cast_id
        or pending.shield_rush
        or pending.spray_targets
        or pending.suppression_zone_id
        or pending.area_aim_point
        or pending.hit_location not in (None, "torso")
        or pending.shots != 1
    ):
        raise ValidationError("This attack requires a specialized interposition consumer")
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode) and (
        weapon.firearm
        or weapon.sprayer
        or weapon.linked_follow_up
        or weapon.multiple_projectiles
        or weapon.ammunition_id
    ):
        raise ValidationError("This projectile requires a specialized interposition consumer")
    protector = next((p for p in encounter.participants if p.actor_id == command.actor_id), None)
    friend = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    if (
        protector is None
        or protector.retreat_used
        or protector.grappled
        or protector.pinned
        or protector.maneuver_state.defense_forbidden
    ):
        raise ValidationError("Protecting actor cannot take the interposition step")
    step = max(1, (protector.movement_allowance + 9) // 10)
    if encounter.spatial_kind == "basic":
        yards = basic_distance(encounter, protector.actor_id, friend.actor_id)
        if yards > step:
            raise ValidationError("Friend is beyond the protecting actor's step")
        if yards:
            encounter = move_basic(
                encounter,
                actor_id=protector.actor_id,
                reference_actor_id=friend.actor_id,
                direction="approach",
                yards=int(yards),
                command_id=command.id,
                revision=state.revision,
            )
        protector = next(p for p in encounter.participants if p.actor_id == protector.actor_id)
    elif isinstance(protector.position, Hex) and isinstance(friend.position, Hex):
        if distance(protector.position, friend.position) > step:
            raise ValidationError("Friend is beyond the protecting actor's step")
        protector = protector.model_copy(update={"position": friend.position})
    elif isinstance(protector.position, GridPoint) and isinstance(friend.position, GridPoint):
        if (
            abs(protector.position.x - friend.position.x)
            + abs(protector.position.y - friend.position.y)
            > step
        ):
            raise ValidationError("Friend is beyond the protecting actor's step")
        protector = protector.model_copy(update={"position": friend.position})
    else:
        raise ValidationError("Interposition requires matching spatial coordinates")
    protector = protector.model_copy(update={"retreat_used": True, "retreat_attacker_id": None})
    encounter = CombatEngine._replace(encounter, protector)
    ranged = tuple(
        scene.model_copy(update={"defender_id": protector.actor_id})
        for scene in encounter.ranged_situations
        if scene.attacker_id == pending.attacker_id and scene.defender_id == friend.actor_id
    )
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={"defender_id": protector.actor_id, "protected_defender_id": friend.actor_id}
            ),
            "ranged_situations": encounter.ranged_situations + ranged,
        }
    )
