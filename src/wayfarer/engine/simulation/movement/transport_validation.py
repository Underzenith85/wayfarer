"""Explicit scenario-activation validation for a transport on the resource ledger."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


def validate_transport(engine: ResourceEngine, state: ResourceState, t: Transport) -> None:
    """Explicit scenario activation validator. No inferred migration from catalog listings."""
    if t.locomotion == "ground-mount" and t.body_id not in engine.actors:
        raise ValidationError("Mount must be a world actor")
    creature = next((entry for entry in state.creatures if entry.actor_id == t.body_id), None)
    if t.locomotion == "ground-mount" and creature is not None:
        if creature.mount is None or not creature.mount.riding:
            raise ValidationError("Ground mount lacks creature riding capability")
        ground = creature.statistics.move("ground")
        if (t.acceleration, t.top_speed) != (
            ground.ordinary_move,
            ground.enhanced_move or ground.ordinary_move,
        ):
            raise ValidationError("Ground mount movement disagrees with creature facts")
    passengers = t.occupants + t.overboard + tuple(e.actor_id for e in t.pending_ejections)
    if not set(passengers) <= engine.actors:
        raise ValidationError("Unknown transport occupant")
    for actor in passengers + ((t.body_id,) if t.locomotion == "ground-mount" else ()):
        pool = next((p for p in state.pools if p.id == "hp:" + actor), None)
        if pool is None or pool.injury is None or pool.injury.profile_id != t.profile_id:
            raise ValidationError("Transport actors require matching explicit injury profiles")
    if t.locomotion != "ground-mount":
        item = next((i for i in state.items if i.id == t.body_id), None)
        spec = engine.specs.get(item.definition_id) if item else None
        if item is None or item.condition is None or spec is None or spec.durability is None:
            raise ValidationError("Vehicle requires an initialized authoritative durability item")
        if spec.durability.profile_id != t.profile_id or item.owner_id != t.operator_id:
            raise ValidationError("Vehicle profile or operator custody mismatch")
