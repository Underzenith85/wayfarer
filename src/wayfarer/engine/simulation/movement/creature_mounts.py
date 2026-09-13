"""Adapter from persistent creature facts to the existing ground-mount transport."""

from typing import Literal

from wayfarer.engine.rules.types.creature import Creature
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.errors import ValidationError


def mount_transport(
    creature: Creature,
    *,
    transport_id: str,
    rider_id: str,
    q: int = 0,
    r: int = 0,
    facing: Literal[0, 1, 2, 3, 4, 5] = 0,
) -> Transport:
    capabilities = creature.mount
    if capabilities is None or not capabilities.riding:
        raise ValidationError("Creature is not an authored riding mount")
    ground = creature.statistics.move("ground")
    footprint = tuple(range(-(creature.statistics.hexes // 2), creature.statistics.hexes // 2 + 1))
    if len(footprint) > creature.statistics.hexes:
        footprint = footprint[:-1]
    return Transport(
        id=transport_id,
        mechanics_version=2,
        locomotion="ground-mount",
        body_id=creature.actor_id,
        operator_id=rider_id,
        occupants=(rider_id,),
        acceleration=ground.ordinary_move,
        top_speed=ground.enhanced_move or ground.ordinary_move,
        q=q,
        r=r,
        facing=facing,
        footprint=footprint,
    )
