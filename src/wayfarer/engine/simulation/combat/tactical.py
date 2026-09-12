"""Explicit hex adapter for the existing encounter, not a second combat engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.tables.combat import maneuver_permission
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.combat.combat_height import HeightEffect, melee_height
from wayfarer.engine.simulation.hex_geometry import (
    Hex,
    HexBattlefield,
    HexFacing,
    Occupant,
    Pose,
    SightPoint,
    arc,
    in_reach,
    line_of_sight,
    movement,
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
        Occupant(actor_id=p.actor_id, position=pose(p).position) for p in encounter.participants
    )


def movement_blockers(encounter: Encounter, actor_id: str) -> tuple[Occupant, ...]:
    return tuple(
        Occupant(actor_id=p.actor_id, position=pose(p).position)
        for p in encounter.participants
        if p.actor_id != actor_id and encounter.blocks_passage(actor_id, p.actor_id)
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
        if board.cell(pose(actor).position).blocked:
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
    if reaches is not None:
        height_effect(encounter, actor, target, reach=max(reaches), location=location, board=board)
    if not sight(encounter, actor, target, board=board) or arc(
        pose(actor), pose(target).position
    ) not in (
        "front",
        "close",
    ):
        raise ValidationError("Target is unavailable")
    if reaches is not None and not in_reach(
        board, pose(actor), pose(target).position, reaches=reaches
    ):
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


def defense_adjustment(encounter: Encounter, actor: Combatant, target: Combatant) -> int:
    if encounter.spatial_kind != "hex":
        return 0
    direction = arc(pose(target), pose(actor).position)
    if direction == "rear":
        raise ValidationError("No active defense against a rear attack")
    return -2 if direction in ("left", "right") else 0


def move_hex(
    encounter: Encounter,
    actor: Combatant,
    maneuver: Maneuver,
    path: tuple[Hex, ...],
    facing: HexFacing | None,
    defense_option: DefenseOption | None,
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
        pose(participant).position
        for participant in encounter.participants
        if participant.actor_id != actor.actor_id
    }
    if path and path[-1] in occupied_destinations:
        raise ValidationError("Movement cannot end in an occupied position")
    result = movement(
        board,
        pose(actor),
        path,
        move=allowance,
        step=step,
        occupants=movement_blockers(encounter, actor.actor_id),
        actor_id=actor.actor_id,
        final_facing=facing,
        final_turn_policy="one"
        if maneuver == "all_out_attack"
        else "any"
        if maneuver == "all_out_defense" and defense_option == "dodge"
        else "move",
    )
    return actor.model_copy(
        update={"position": result.destination.position, "hex_facing": result.destination.facing}
    )
