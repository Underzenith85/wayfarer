"""Source B375 ordinary weapon interposition through existing attack reducers."""

from collections import deque
from decimal import Decimal
from math import ceil

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    Encounter,
    basic_distance,
    basic_visible,
    move_basic,
)
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.engine.simulation.combat.tactical import move_hex, sight
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex, distance, neighbor
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _hex_step(
    runtime: RulesContext, encounter: Encounter, protector: Combatant, friend: Combatant
) -> Combatant:
    """Find an admitted step, rather than jumping through intervening terrain."""
    assert isinstance(protector.position, Hex) and isinstance(friend.position, Hex)
    board = runtime.require_hex(encounter)
    queue: deque[tuple[Hex, tuple[Hex, ...]]] = deque(((protector.position, ()),))
    seen = {protector.position}
    while queue:
        position, path = queue.popleft()
        if position == friend.position:
            return (
                protector
                if not path
                else move_hex(encounter, protector, "attack", path, None, None, True, board=board)
            )
        for direction in range(6):
            try:
                target = neighbor(position, direction)
                if target in seen:
                    continue
                following = path + (target,)
                move_hex(encounter, protector, "attack", following, None, None, True, board=board)
            except ValidationError, ValueError:
                continue
            seen.add(target)
            queue.append((target, following))
    raise ValidationError("No legal interposition step reaches the friend")


def _square_step(
    runtime: RulesContext, encounter: Encounter, protector: Combatant, friend: Combatant, step: int
) -> Combatant:
    assert isinstance(protector.position, GridPoint) and isinstance(friend.position, GridPoint)
    assert runtime.rules.combat is not None
    spatial = encounter.spatial
    assert isinstance(spatial, SquareSpatialContext)
    board = next(b for b in runtime.rules.combat.battlefields if b.id == spatial.battlefield_id)
    assert isinstance(board, Battlefield)
    blocked = set(board.blocked) | {
        p.position
        for p in encounter.participants
        if p.actor_id not in (protector.actor_id, friend.actor_id)
        and encounter.blocks_passage(protector.actor_id, p.actor_id)
    }
    queue: deque[tuple[GridPoint, int]] = deque(((protector.position, 0),))
    seen = {protector.position}
    while queue:
        position, spent = queue.popleft()
        if position == friend.position:
            return protector.model_copy(update={"position": friend.position})
        if spent == step:
            continue
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            x, y = position.x + dx, position.y + dy
            if not 0 <= x < board.width or not 0 <= y < board.height:
                continue
            target = GridPoint(x=x, y=y)
            if target in seen or target in blocked:
                continue
            seen.add(target)
            queue.append((target, spent + 1))
    raise ValidationError("No legal interposition step reaches the friend")


def _take_step(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense,
    protector: Combatant,
    friend: Combatant,
) -> tuple[Encounter, Combatant]:
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
                yards=ceil(yards),
                command_id=command.id,
                revision=state.revision,
            )
        protector = next(p for p in encounter.participants if p.actor_id == protector.actor_id)
    elif isinstance(protector.position, Hex) and isinstance(friend.position, Hex):
        if distance(protector.position, friend.position) > step:
            raise ValidationError("Friend is beyond the protecting actor's step")
        protector = _hex_step(runtime, encounter, protector, friend)
    elif isinstance(protector.position, GridPoint) and isinstance(friend.position, GridPoint):
        if (
            abs(protector.position.x - friend.position.x)
            + abs(protector.position.y - friend.position.y)
            > step
        ):
            raise ValidationError("Friend is beyond the protecting actor's step")
        protector = _square_step(runtime, encounter, protector, friend, step)
    else:
        raise ValidationError("Interposition requires matching spatial coordinates")
    return encounter, protector


def _tranquilizer_dart(weapon: RangedMode) -> bool:
    """B279 note 2: one penetrating dart carries this exact resisted drug."""
    payload = weapon.linked_follow_up
    return bool(
        payload is not None
        and payload.kind == "drug"
        and payload.requires_penetration
        and payload.resistance_penalty == -3
        and payload.condition == "unconsciousness"
        and payload.duration_minutes_per_margin == 1
        and weapon.damage.damage_type == "pi-"
        and weapon.damage.armor_divisor == Decimal("0.2")
    )


def prepare_interposition(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    if command.sacrificial_for is None:
        return encounter
    if (
        runtime.rules.combat is None
        or runtime.rules.combat.gurps_equipment is None
        or runtime.rules.combat.gurps_equipment.profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Sacrificial Dodge requires Basic Set combat")
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
        or pending.armor_chink
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
        (weapon.firearm is not None and weapon.firearm.action == "single-use")
        or weapon.sprayer
        or weapon.linked_follow_up is not None
        and not _tranquilizer_dart(weapon)
        or weapon.multiple_projectiles
    ):
        raise ValidationError("This projectile requires a specialized interposition consumer")
    protector = next((p for p in encounter.participants if p.actor_id == command.actor_id), None)
    friend = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    observable = (
        basic_visible(encounter, command.actor_id, pending.attacker_id)
        and basic_visible(encounter, command.actor_id, friend.actor_id)
        if encounter.spatial_kind == "basic"
        else protector is not None
        and sight(encounter, protector, attacker, board=runtime.hex_map(encounter))
        and sight(encounter, protector, friend, board=runtime.hex_map(encounter))
    )
    if not observable:
        raise ValidationError("Interposition requires an observable attack and friend")
    if (
        protector is None
        or protector.retreat_used
        or protector.grappled
        or protector.pinned
        or protector.maneuver_state.defense_forbidden
    ):
        raise ValidationError("Protecting actor cannot take the interposition step")
    encounter, protector = _take_step(runtime, state, encounter, command, protector, friend)
    if encounter.spatial_kind != "basic":
        pair = tuple(sorted((protector.actor_id, friend.actor_id)))
        encounter = encounter.model_copy(
            update={"close_pairs": tuple(sorted(set(encounter.close_pairs) | {pair}))}
        )
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
                update={
                    "defender_id": protector.actor_id,
                    "protected_defender_id": friend.actor_id,
                    "sacrificial_drop": command.dodge_and_drop,
                    "visibility_defense_penalty": 0,
                    "attention_defense_penalty": 0,
                    "tactical_approach": None,
                }
            ),
            "ranged_situations": tuple(
                s
                for s in encounter.ranged_situations
                if not (
                    s.attacker_id == pending.attacker_id and s.defender_id == protector.actor_id
                )
            )
            + ranged,
        }
    )
