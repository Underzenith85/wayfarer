"""Spraying fire and suppression zones (B409)."""

from __future__ import annotations

from math import sqrt
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.suppression import ActiveSuppressionZone, PendingSprayTarget
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def _spray_vector(encounter: Encounter, actor_id: str, target_id: str) -> tuple[float, float]:
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    target = next(p for p in encounter.participants if p.actor_id == target_id)
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.hex_geometry import Hex

        if not isinstance(actor.position, Hex) or not isinstance(target.position, Hex):
            raise ValidationError("Spraying fire requires one mapped coordinate system")
        return (
            (target.position.q + target.position.r / 2) - (actor.position.q + actor.position.r / 2),
            (target.position.r - actor.position.r) * sqrt(3) / 2,
        )
    from wayfarer.engine.simulation.combat.battlefield import GridPoint

    if not isinstance(actor.position, GridPoint) or not isinstance(target.position, GridPoint):
        raise ValidationError("Spraying fire requires exact mapped positions")
    return float(target.position.x - actor.position.x), float(target.position.y - actor.position.y)


def prepare_spraying_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
) -> Encounter:
    """Validate and persist a B409 ordered multi-target sweep before any dice."""
    if not command.spray_targets:
        return encounter
    if encounter.spatial_kind == "basic":
        raise ValidationError("Spraying fire requires exact mapped target directions")
    if command.target_item_id is not None or command.second_target_id is not None:
        raise ValidationError("Spraying fire targets combatants through one declared sweep")
    pending = encounter.pending_defense
    if pending is None or pending.mode_id is None:
        raise ValidationError("Spraying fire requires a pending ranged attack")
    from wayfarer.engine.simulation.combat.melee.modes import mode

    selected = mode(runtime, state, command.actor_id, command.item_id or "", pending.mode_id)
    if not isinstance(selected, RangedMode):
        raise ValidationError("Spraying fire requires a ranged weapon")
    if selected.rate_of_fire < 5 or selected.thrown or selected.sprayer is not None:
        raise ValidationError("Spraying fire requires an ordinary weapon with RoF 5+")
    declarations = (
        (pending.defender_id, pending.shots, pending.hit_location),
        *(
            (target.target_id, target.shots, target.hit_location)
            for target in command.spray_targets
        ),
    )
    target_ids = tuple(target_id for target_id, _, _ in declarations)
    if len(set(target_ids)) != len(target_ids) or command.actor_id in target_ids:
        raise ValidationError("Spraying fire requires distinct targets other than the attacker")
    vectors = tuple(
        _spray_vector(encounter, command.actor_id, target_id) for target_id in target_ids
    )
    for index, left in enumerate(vectors):
        for right in vectors[index + 1 :]:
            dot = left[0] * right[0] + left[1] * right[1]
            cross = left[0] * right[1] - left[1] * right[0]
            if dot <= 0 or 3 * cross * cross > dot * dot:
                raise ValidationError("Spraying-fire targets must fit within one 30-degree angle")
    turns = tuple(
        left[0] * right[1] - left[1] * right[0]
        for left, right in zip(vectors, vectors[1:], strict=False)
    )
    if any(turn > 0 for turn in turns) and any(turn < 0 for turn in turns):
        raise ValidationError("Spraying-fire targets must be ordered from one side to the other")
    participants = {p.actor_id: p for p in encounter.participants}
    traversal: list[int] = []
    from wayfarer.engine.simulation.combat.objects.locations import validate_target

    for index, (target_id, shots, hit_location) in enumerate(declarations):
        target = participants.get(target_id)
        if target is None:
            raise ValidationError("Spraying-fire target is not in the encounter")
        scene = situation(runtime, encounter, command.actor_id, target_id, selected)
        if scene.distance > float(selected.maximum_range):
            raise ValidationError("Spraying-fire target exceeds maximum weapon range")
        validate_target(
            runtime, state, encounter, command.actor_id, target_id, selected, hit_location
        )
        if shots > selected.rate_of_fire or shots < selected.minimum_shots_per_attack:
            raise ValidationError("Each spraying-fire target requires a legal burst")
        if index:
            previous = participants[declarations[index - 1][0]]
            distance = CombatEngine.distance(previous.position, target.position)
            traversal.append(max(0, distance - 1) * (2 if selected.rate_of_fire > 16 else 1))
    total = sum(shots for _, shots, _ in declarations) + sum(traversal)
    if total > selected.rate_of_fire:
        raise ValidationError("Spraying-fire bursts and traversal exceed weapon RoF")
    load = next(
        (
            entry
            for entry in state.resources.ammunition_loads
            if entry.weapon_id == pending.weapon_id
        ),
        None,
    )
    if load is None or load.mode_id != selected.id or load.rounds < total:
        raise ValidationError("Spraying fire requires all declared ammunition before rolling")
    queued = tuple(
        PendingSprayTarget(
            target_id=target_id,
            shots=shots,
            hit_location=hit_location,
            recoil_penalty=index,
            traversal_shots=traversal[index - 1],
        )
        for index, (target_id, shots, hit_location) in enumerate(declarations[1:], start=1)
    )
    return encounter.model_copy(
        update={"pending_defense": pending.model_copy(update={"spray_targets": queued})}
    )


def prepare_suppression_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
    board: HexBattlefield | None,
) -> tuple[PlayState, Encounter]:
    """Validate, pay for, and persist B409-410 suppression zones."""
    if not command.suppression_zones:
        return state, encounter
    if encounter.spatial_kind != "hex" or board is None:
        raise ValidationError("Suppression fire requires an exact hex path and battlefield")
    from wayfarer.engine.simulation.combat.melee.modes import mode

    selected = mode(runtime, state, command.actor_id, command.item_id or "", command.mode_id)
    if (
        not isinstance(selected, RangedMode)
        or selected.rate_of_fire < 5
        or selected.thrown
        or selected.sprayer is not None
    ):
        raise ValidationError("Suppression fire requires an ordinary weapon with RoF 5+")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if not isinstance(actor.position, Hex):
        raise ValidationError("Suppression fire requires the firer's exact hex")
    declarations = command.suppression_zones
    if len(declarations) > 1 and selected.rate_of_fire < 10:
        raise ValidationError("Multiple suppression zones require RoF 10+")
    if len({zone.center for zone in declarations}) != len(declarations):
        raise ValidationError("Suppression zones require distinct centers")
    for index, zone in enumerate(declarations):
        cell = board.cell(zone.center)
        if cell.blocked:
            raise ValidationError("Suppression-zone center cannot be blocked terrain")
        if CombatEngine.distance(actor.position, zone.center) > float(selected.maximum_range):
            raise ValidationError("Suppression zone exceeds maximum weapon range")
        if zone.shots < selected.minimum_shots_per_attack:
            raise ValidationError("Suppression zone requires a legal burst")
        if len(declarations) > 1 and zone.shots < 5:
            raise ValidationError("Each of multiple suppression zones requires at least five shots")
        if index and CombatEngine.distance(declarations[index - 1].center, zone.center) > 2:
            raise ValidationError("Multiple suppression zones must be adjacent")
    total = sum(zone.shots for zone in declarations)
    if total > selected.rate_of_fire:
        raise ValidationError("Suppression-zone shots exceed weapon RoF")
    load = next(
        (entry for entry in state.resources.ammunition_loads if entry.weapon_id == command.item_id),
        None,
    )
    if load is None or load.mode_id != selected.id or load.rounds < total:
        raise ValidationError("Suppression fire requires all declared ammunition")
    aim = actor.maneuver_state
    aim_bonus = (
        aim.aim_bonus if (aim.aim_item_id, aim.aim_mode_id) == (command.item_id, selected.id) else 0
    )
    zones = tuple(
        ActiveSuppressionZone(
            id=f"suppression:{command.id}:{index}",
            attacker_id=command.actor_id,
            weapon_id=command.item_id or "",
            mode_id=selected.id,
            origin=actor.position,
            center=zone.center,
            shots=zone.shots,
            remaining_hits=zone.shots,
            aim_bonus=aim_bonus,
            skill_cap=8 if selected.mount is not None else 6,
        )
        for index, zone in enumerate(declarations)
    )
    from wayfarer.engine.simulation.combat.firearm_transitions import validate_attack
    from wayfarer.engine.simulation.combat.firearms import spend_rounds

    validate_attack(state.resources, command.item_id or "", selected, total)
    resources = spend_rounds(state.resources, command.item_id or "", total)
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), encounter.model_copy(
        update={"suppression_zones": encounter.suppression_zones + zones}
    )
