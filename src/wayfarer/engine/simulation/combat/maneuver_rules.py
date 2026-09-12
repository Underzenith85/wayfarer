"""What each maneuver does to the actor taking it.

One rule per maneuver, each reading the same declaration and returning the
combatant it produces. Choosing the rule is a lookup, so a maneuver's legality
and its effect are read together instead of being spread down a ladder.

Attacks are not here: declaring one pauses the encounter for the defender's
choice, which changes the encounter rather than the attacker, so ``turns``
keeps it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import BasicMove
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.vocabulary import Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.resources import Equip, ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.engine import CombatEngine

ManeuverKind = str


@dataclass(frozen=True, slots=True)
class Declaration:
    """One validated turn declaration: every input a maneuver rule may read."""

    engine: CombatEngine
    encounter: Encounter
    participant: Combatant
    resources: ResourceState
    actor_id: str
    maneuver: Maneuver
    command_id: str
    battlefield: Battlefield | HexBattlefield | None
    basic: bool
    deferred_step: bool
    destination: GridPoint | None = None
    facing: Facing | None = None
    posture: Posture | None = None
    item_id: str | None = None
    target_id: str | None = None
    basic_move: BasicMove | None = None
    hex_path: tuple[Hex, ...] = ()


Outcome = tuple[Combatant, ResourceState]


def kind(
    maneuver: Maneuver,
    *,
    spatial_kind: str,
    basic: bool,
    suppression_fire: bool,
    attack_maneuvers: frozenset[str] | tuple[str, ...],
) -> ManeuverKind:
    """Name the rule this declaration selects, so the effect is one lookup."""
    if maneuver == "move":
        return "move_hex" if spatial_kind == "hex" else "move_basic" if basic else "move_square"
    if maneuver in ("ready", "change_posture"):
        return maneuver
    if maneuver in attack_maneuvers:
        return "suppression_attack" if suppression_fire else "attack"
    if maneuver in ("aim", "evaluate", "feint"):
        return "observe"
    return "other"


def _taken(declared: Declaration, **update: object) -> Outcome:
    return (
        declared.participant.model_copy(update={"last_maneuver": declared.maneuver, **update}),
        declared.resources,
    )


def move_hex(declared: Declaration) -> Outcome:
    if any(v is not None for v in (declared.posture, declared.item_id, declared.target_id)):
        raise ValidationError("Move accepts only a path and facing")
    return _taken(declared)


def move_basic(declared: Declaration) -> Outcome:
    if declared.basic_move is None or any(
        v is not None
        for v in (
            declared.destination,
            declared.facing,
            declared.posture,
            declared.item_id,
            declared.target_id,
        )
    ):
        raise ValidationError("Basic Move requires one authoritative relative movement")
    return _taken(declared)


def move_square(declared: Declaration) -> Outcome:
    engine, participant, destination = declared.engine, declared.participant, declared.destination
    if destination is None or any(
        v is not None for v in (declared.posture, declared.item_id, declared.target_id)
    ):
        raise ValidationError("Move requires only a destination and optional facing")
    limit = (
        engine.rules.prone_movement_allowance
        if participant.posture == "prone"
        else participant.movement_allowance
    )
    if not isinstance(participant.position, GridPoint):
        raise ValidationError("Square movement requires square coordinates")
    occupied = {
        p.position
        for p in declared.encounter.participants
        if p.actor_id != declared.actor_id and isinstance(p.position, GridPoint)
    }
    battlefield = declared.battlefield
    if (
        not isinstance(battlefield, Battlefield)
        or destination.x >= battlefield.width
        or destination.y >= battlefield.height
        or destination in battlefield.blocked
        or destination in occupied
        or engine.distance(participant.position, destination) > limit
        or not engine._reachable(battlefield, participant.position, destination, limit, occupied)
    ):
        raise ValidationError("Combat movement exceeds allowance or terrain constraints")
    return _taken(declared, position=destination, facing=declared.facing or participant.facing)


def ready(declared: Declaration) -> Outcome:
    # A bound actor may spend its Ready struggling with the binding
    # instead of readying an item (#354).
    participant, item_id = declared.participant, declared.item_id
    struggling = participant.entangled is not None
    if any(
        v is not None
        for v in (declared.destination, declared.facing, declared.posture, declared.target_id)
    ) or (item_id is None and not struggling):
        raise ValidationError("Ready requires exactly one item")
    resources = declared.resources
    if item_id is not None:
        resources = declared.engine.resources.apply(
            resources,
            Equip(
                id=f"{declared.command_id}:ready",
                actor_id=declared.actor_id,
                expected_revision=resources.revision,
                item_id=item_id,
                ready=True,
            ),
        )
    taken = participant.model_copy(
        update={
            "ready_item_ids": tuple(
                sorted(
                    item.id
                    for item in resources.items
                    if item.owner_id == declared.actor_id and item.equipped and item.ready
                )
            ),
            "last_maneuver": declared.maneuver,
        }
    )
    return taken, resources


def change_posture(declared: Declaration) -> Outcome:
    participant, posture = declared.participant, declared.posture
    if (
        declared.engine.rules.gurps_equipment is not None
        and participant.posture == "prone"
        and posture == "standing"
    ):
        raise ValidationError("Rise from prone to kneeling before standing")
    if posture is None or any(
        v is not None
        for v in (declared.destination, declared.facing, declared.item_id, declared.target_id)
    ):
        raise ValidationError("Posture maneuver requires exactly one posture")
    return _taken(declared, posture=posture)


def suppression_attack(declared: Declaration) -> Outcome:
    item_id = declared.item_id
    if (
        declared.maneuver != "all_out_attack"
        or item_id is None
        or declared.target_id is not None
        or item_id not in declared.participant.ready_item_ids
        or declared.deferred_step
        or any(v is not None for v in (declared.destination, declared.facing, declared.posture))
    ):
        raise ValidationError("Suppression fire requires an immobile All-Out Attack")
    return _taken(declared, last_attack_item_id=item_id)


def observe(declared: Declaration) -> Outcome:
    if (
        declared.facing is not None
        or declared.posture is not None
        or (declared.maneuver == "evaluate" and declared.item_id is not None)
    ):
        raise ValidationError("Unexpected observation maneuver parameters")
    return _taken(declared)


def other(declared: Declaration) -> Outcome:
    if any(
        v is not None
        for v in (
            declared.destination,
            declared.facing,
            declared.posture,
            declared.item_id,
            declared.target_id,
        )
    ):
        raise ValidationError("Maneuver has unexpected parameters")
    return _taken(declared)


RULES: Final[dict[ManeuverKind, Callable[[Declaration], Outcome]]] = {
    "move_hex": move_hex,
    "move_basic": move_basic,
    "move_square": move_square,
    "ready": ready,
    "change_posture": change_posture,
    "suppression_attack": suppression_attack,
    "observe": observe,
    "other": other,
}
