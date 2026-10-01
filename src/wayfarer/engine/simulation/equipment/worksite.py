"""Physical custody checks for work on world-grounded objects and supplies."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.resources import Item


def available_here(state: PlayState, actor_id: str, item: Item) -> bool:
    """Owned equipment is usable only at the actor's actual work location.

    A contained tool inherits its root's world/encounter placement. Custody
    bookkeeping alone never lets a distant ground object be worked remotely.
    """
    actor = next((e for e in state.world.entities if e.id == actor_id), None)
    if actor is None or actor.location_id is None:
        return False
    parents = {entry.id: entry for entry in state.resources.items}
    visited: set[str] = set()
    while True:
        if item.id in visited or item.owner_id != actor_id or item.ground is not None:
            return False
        visited.add(item.id)
        if item.world_ground_location_id is not None:
            return item.world_ground_location_id == actor.location_id
        if item.container_id is None:
            return True
        parent = parents.get(item.container_id)
        if parent is None:
            return False
        item = parent
