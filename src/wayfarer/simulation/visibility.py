"""Authoritative encounter visibility shared by mechanics and projections."""

from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Encounter
from wayfarer.simulation.hex_geometry import HexBattlefield
from wayfarer.simulation.tactical import sight


def visible_actors(
    state: PlayState, encounter: Encounter, actor_id: str, *, board: HexBattlefield | None = None
) -> frozenset[str]:
    own = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if own is None:
        return frozenset()
    entities = {e.id: e for e in state.world.perspective(actor_id).entities}
    own_entity = entities.get(actor_id)
    return frozenset(
        p.actor_id
        for p in encounter.participants
        if p.actor_id == actor_id
        or (
            p.actor_id in entities
            and own_entity is not None
            and entities[p.actor_id].location_id == own_entity.location_id
            and sight(encounter, own, p, board=board)
        )
    )
