"""Explicit hex adapter for the existing encounter, not a second combat engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.tables.combat import maneuver_permission
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.combat.combat_height import HeightEffect, melee_height
from wayfarer.engine.simulation.combat.spatial import HexActorPlacement
from wayfarer.engine.simulation.hex_geometry import (
    Hex,
    HexBattlefield,
    HexFacing,
    Occupant,
    Pose,
    SightPoint,
    arc,
    distance,
    in_reach,
    line_of_sight,
    movement,
    pop_up_movement,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
    from wayfarer.engine.simulation.combat.maneuvers import DefenseOption
    from wayfarer.engine.simulation.combat.vocabulary import Maneuver
    from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog


class TacticalTrace(Record):
    command_id: str
    actor_id: str
    code: str
    totals: tuple[int, ...] = ()
    targets: tuple[int, ...] = ()
    injury: int = 0
    source: str = "GURPS Basic Set: Campaigns 4e, fourth printing (April 2008), B367-377, B384-392"


def pose(actor: Combatant) -> Pose:
    if not isinstance(actor.position, Hex) or actor.hex_facing is None:
        raise ValidationError("Hex encounter requires explicitly migrated actor poses")
    return Pose(
        position=actor.position,
        facing=actor.hex_facing,
        posture="lying" if actor.posture == "prone" else actor.posture,
    )


def occupants(encounter: Encounter) -> tuple[Occupant, ...]:
    return tuple(
        Occupant(actor_id=p.actor_id, position=position)
        for p in encounter.participants
        for position in encounter.occupied_hexes(p.actor_id)
    )


def movement_blockers(encounter: Encounter, actor_id: str) -> tuple[Occupant, ...]:
    return tuple(
        Occupant(
            actor_id=p.actor_id,
            position=position,
            relation="enemy" if encounter.blocks_passage(actor_id, p.actor_id) else "ally",
        )
        for p in encounter.participants
        if p.actor_id != actor_id
        for position in encounter.occupied_hexes(p.actor_id)
    )


def validate_hex_encounter(
    encounter: Encounter, catalog: EquipmentCatalog | None, *, board: HexBattlefield | None
) -> None:
    if encounter.spatial_kind == "hex" and board is None:
        raise ValidationError("Hex encounter requires its configured template")
    assert board is not None
    if (
        catalog is None
        or catalog.profile_id != board.profile_id
        or board.id != encounter.battlefield_id
    ):
        raise ValidationError("Hex battlefield must match the exact saved combat profile and map")
    for actor in encounter.participants:
        for position in encounter.occupied_hexes(actor.actor_id):
            if board.cell(position).blocked:
                raise ValidationError("Combatant occupies blocked terrain")


def sight(
    encounter: Encounter,
    actor: Combatant,
    target: Combatant,
    *,
    board: HexBattlefield | None = None,
) -> bool:
    if encounter.spatial_kind == "hex" and board is None:
        raise ValidationError("Hex encounter requires its configured template")
    if board is None:
        return True
    return line_of_sight(
        board,
        SightPoint(position=pose(actor).position, height=1 if actor.posture != "prone" else 0),
        SightPoint(position=pose(target).position, height=1 if target.posture != "prone" else 0),
    )


def attack_geometry(
    encounter: Encounter,
    actor: Combatant,
    target: Combatant,
    reaches: frozenset[int] | None = None,
    *,
    location: HitLocation | None = None,
    board: HexBattlefield | None = None,
) -> None:
    if encounter.spatial_kind == "hex" and board is None:
        raise ValidationError("Hex encounter requires its configured template")
    if board is None:
        return
    target_hexes = encounter.occupied_hexes(target.actor_id)
    target_position = min(target_hexes, key=lambda point: distance(pose(actor).position, point))
    if reaches is not None:
        height_effect(encounter, actor, target, reach=max(reaches), location=location, board=board)
    if not sight(encounter, actor, target, board=board) or arc(
        pose(actor), target_position
    ) not in (
        "front",
        "close",
    ):
        raise ValidationError("Target is unavailable")
    if reaches is not None and not in_reach(board, pose(actor), target_position, reaches=reaches):
        raise ValidationError("Target is outside selected weapon reach")


def height_effect(
    encounter: Encounter,
    actor: Combatant,
    target: Combatant,
    *,
    reach: int,
    location: HitLocation | None,
    board: HexBattlefield | None = None,
) -> HeightEffect:
    if encounter.spatial_kind == "hex" and board is None:
        raise ValidationError("Hex encounter requires its configured template")
    if board is None:
        return HeightEffect()
    start = board.cell(pose(actor).position).ground
    end = board.cell(pose(target).position).ground
    return melee_height(start, end, reach=reach, location=location)


TacticalApproach = Literal["front", "side", "rear", "runaround", "pop-up"]


def attack_approach(
    defender: Pose,
    attacker: Pose,
    *,
    origin: Pose | None = None,
) -> TacticalApproach:
    """Classify the defender's awareness from the committed attack path (B390-391)."""
    direction = arc(defender, attacker.position)
    if direction == "rear":
        if origin is not None and arc(defender, origin.position) == "front":
            return "runaround"
        return "rear"
    return "side" if direction in ("left", "right") else "front"


def defense_adjustment(
    encounter: Encounter,
    actor: Combatant,
    target: Combatant,
    *,
    approach: TacticalApproach | None = None,
) -> int:
    if encounter.spatial_kind != "hex":
        return 0
    classified = approach or attack_approach(pose(target), pose(actor))
    if classified == "rear":
        raise ValidationError("No active defense against a rear attack")
    return -2 if classified in ("side", "runaround") else 0


def move_hex(
    encounter: Encounter,
    actor: Combatant,
    maneuver: Maneuver,
    path: tuple[Hex, ...],
    facing: HexFacing | None,
    defense_option: DefenseOption | None,
    enter_close_combat: bool = False,
    *,
    board: HexBattlefield | None,
) -> Combatant:
    if encounter.spatial_kind == "hex" and board is None:
        raise ValidationError("Hex encounter requires its configured template")
    assert board is not None
    if not path and facing is None:
        if maneuver == "move":
            raise ValidationError("Move requires a path or facing")
        return actor
    if (
        actor.grappled
        or actor.pinned
        or any(g.holder_id == actor.actor_id for g in encounter.grips)
    ):
        raise ValidationError("Release or escape the grapple before moving")
    permission = maneuver_permission(maneuver)
    if permission.movement in ("none", "triggered"):
        raise ValidationError("Maneuver does not permit movement")
    allowance = actor.movement_allowance
    step = permission.movement == "step"
    if maneuver == "all_out_attack" or (
        maneuver == "all_out_defense" and defense_option == "dodge"
    ):
        allowance //= 2
        step = False
    if maneuver == "all_out_attack":
        current = pose(actor)
        for point in path:
            if arc(current, point) != "front":
                raise ValidationError("All-Out Attack movement must be forward")
            current = current.model_copy(update={"position": point})
    occupied_destinations = {
        position
        for participant in encounter.participants
        if participant.actor_id != actor.actor_id
        for position in encounter.occupied_hexes(participant.actor_id)
    }
    if path and path[-1] in occupied_destinations and not enter_close_combat:
        raise ValidationError("Movement cannot end in an occupied position")
    result = movement(
        board,
        pose(actor),
        path,
        move=allowance,
        step=step,
        occupants=movement_blockers(encounter, actor.actor_id),
        actor_id=actor.actor_id,
        enter_close_combat=enter_close_combat,
        final_facing=facing,
        final_turn_policy="one"
        if maneuver == "all_out_attack"
        else "any"
        if maneuver == "all_out_defense" and defense_option == "dodge"
        else "move",
    )
    _validate_multi_hex_destination(
        encounter,
        actor.actor_id,
        result.destination,
        board,
        enter_close_combat=enter_close_combat,
    )
    return actor.model_copy(
        update={"position": result.destination.position, "hex_facing": result.destination.facing}
    )


def pop_up_hex(
    encounter: Encounter,
    actor: Combatant,
    path: tuple[Hex, ...],
    facing: HexFacing | None,
    *,
    board: HexBattlefield | None,
) -> tuple[Combatant, Pose]:
    """Validate and apply only the final pose of an atomic pop-up movement."""
    if encounter.spatial_kind != "hex" or board is None:
        raise ValidationError("Pop-up attack requires the matching hex battlefield")
    result = pop_up_movement(
        board,
        pose(actor),
        path,
        move=actor.movement_allowance,
        occupants=movement_blockers(encounter, actor.actor_id),
        actor_id=actor.actor_id,
        exposure_facing=facing,
    )
    return (
        actor.model_copy(
            update={
                "position": result.destination.position,
                "hex_facing": result.destination.facing,
            }
        ),
        result.exposure,
    )


def _rotate_offset(q: int, r: int, turns: int) -> tuple[int, int]:
    for _ in range(turns % 6):
        q, r = -r, q + r
    return q, r


def transformed_footprint(
    encounter: Encounter, actor_id: str, destination: Pose
) -> tuple[Hex, ...]:
    """Rotate and translate a body footprint around its head as one identity (B392)."""
    placement = encounter.placement(actor_id)
    if not isinstance(placement, HexActorPlacement):
        raise ValidationError("Multi-hex movement requires a hex placement")
    turns = (destination.facing - placement.facing) % 6
    result = []
    for point in placement.occupied:
        q, r = _rotate_offset(point.q - placement.position.q, point.r - placement.position.r, turns)
        result.append(Hex(q=destination.position.q + q, r=destination.position.r + r))
    return tuple(result)


def _validate_multi_hex_destination(
    encounter: Encounter,
    actor_id: str,
    destination: Pose,
    board: HexBattlefield,
    *,
    enter_close_combat: bool,
) -> None:
    occupied = transformed_footprint(encounter, actor_id, destination)
    if enter_close_combat and len(occupied) > 1:
        raise ValidationError("Multi-hex close-combat entry requires explicit body placement")
    blockers = {
        point
        for participant in encounter.participants
        if participant.actor_id != actor_id
        and encounter.blocks_passage(actor_id, participant.actor_id)
        for point in encounter.occupied_hexes(participant.actor_id)
    }
    for point in occupied:
        if board.cell(point).blocked or (point in blockers and not enter_close_combat):
            raise ValidationError("Multi-hex movement destination is blocked")
