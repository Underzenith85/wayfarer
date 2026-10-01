"""Owned equipment put down at an authoritative world location."""

from __future__ import annotations

import hashlib
from typing import Literal

from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.salvage_state import tasks as salvage_tasks
from wayfarer.engine.simulation.magic.lock_state import destroyed as lock_destroyed
from wayfarer.engine.simulation.magic.lock_state import latest as lock_states
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "world-ground:"


class WorldGroundCommand(Command):
    kind: Literal["drop", "retrieve"]
    item_id: Id


class WorldGroundResult(Record):
    command: WorldGroundCommand
    location_id: Id
    item_ids: tuple[Id, ...]


def apply_world_ground(
    resources: ResourceState,
    world: World,
    engine: ResourceEngine,
    command: WorldGroundCommand,
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, WorldGroundResult]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("World equipment movement requires actor authority")
    identifier = PREFIX + hashlib.sha256(command.id.encode()).hexdigest()
    prior = next((e for e in resources.events if e.id == identifier), None)
    if prior is not None:
        result = WorldGroundResult.model_validate_json(prior.kind)
        if result.command != command:
            raise ConflictError("World equipment command ID was already used")
        return resources, result
    if resources.revision != command.expected_revision:
        raise ConflictError("World equipment revision changed")
    engine.for_world(world).validate(resources)
    actor = next((e for e in world.entities if e.id == command.actor_id), None)
    if actor is None or actor.kind is not EntityKind.ACTOR or actor.location_id is None:
        raise ValidationError("Dropping equipment requires an authoritative actor location")
    item = next((i for i in resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Equipment is not owned by the command actor")
    if item.container_id is not None or item.ground is not None:
        raise ValidationError("Retrieve the container or encounter-ground equipment first")
    if command.kind == "drop" and item.world_ground_location_id is not None:
        raise ValidationError("Equipment is already on world ground")
    if command.kind == "retrieve" and item.world_ground_location_id != actor.location_id:
        raise ValidationError("Retrieval requires the equipment's actual world location")
    ids = {item.id}
    while True:
        children = {i.id for i in resources.items if i.container_id in ids}
        if children <= ids:
            break
        ids |= children
    require_movable_gear(resources, frozenset(ids))
    items = tuple(
        i.model_copy(
            update={
                "world_ground_location_id": actor.location_id
                if command.kind == "drop" and i.id == item.id
                else None,
                "equipped": False,
                "ready": False,
            }
        )
        if i.id in ids
        else i
        for i in resources.items
    )
    result = WorldGroundResult(
        command=command, location_id=actor.location_id, item_ids=tuple(sorted(ids))
    )
    updated = resources.model_copy(
        update={
            "items": items,
            "revision": resources.revision + 1,
            "events": resources.events
            + (
                ResourceEvent(
                    id=identifier,
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    engine.for_world(world).validate(updated)
    return updated, result


def require_movable_gear(resources: ResourceState, identifiers: frozenset[str]) -> None:
    """Moving a root container cannot bypass the busy status of a contained item."""
    if any(
        v.fixture.kind == "door"
        and v.fixture.item_id in identifiers
        and not lock_destroyed(resources, v)
        for v in lock_states(resources).values()
    ):
        raise ConflictError("An intact fixed door cannot be retrieved as carried equipment")
    if any(
        t.status == "pending" and bool(identifiers & {t.item_id, t.tool_id})
        for t in tasks(resources)
    ) or any(
        t.status == "pending" and bool(identifiers & {t.item_id, t.tool_id})
        for t in salvage_tasks(resources)
    ):
        raise ConflictError("Equipment is committed to a pending repair")
