"""Authoritative encounter visibility shared by mechanics and projections."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_visible
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import sight
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError


def visible_actors(
    state: PlayState, encounter: Encounter, actor_id: str, *, board: HexBattlefield | None = None
) -> frozenset[str]:
    own = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if own is None:
        return frozenset()
    entities = {e.id: e for e in state.world.perspective(actor_id).entities}
    own_entity = entities.get(actor_id)
    visible = {actor_id}
    for participant in encounter.participants:
        if (
            participant.actor_id == actor_id
            or participant.actor_id not in entities
            or own_entity is None
            or entities[participant.actor_id].location_id != own_entity.location_id
        ):
            continue
        try:
            observable = (
                basic_visible(encounter, actor_id, participant.actor_id)
                if isinstance(encounter.spatial, BasicSpatialContext)
                else sight(encounter, own, participant, board=board)
            )
        except ValidationError:
            observable = False
        if observable:
            visible.add(participant.actor_id)
    return frozenset(visible)
