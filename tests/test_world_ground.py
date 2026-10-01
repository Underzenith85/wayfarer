"""Owned gear remains at its actual world location, including container contents."""

from dataclasses import replace

import pytest
from test_resources import engine, seed

from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand, apply_world_ground
from wayfarer.engine.simulation.resources import Equip
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError


def test_world_ground_container_load_ownership_location_and_retries() -> None:
    world = World(
        entities=(
            Entity("a", EntityKind.ACTOR, "A", location_id="camp"),
            Entity("b", EntityKind.ACTOR, "B", location_id="camp"),
            Entity("camp", EntityKind.LOCATION, "Camp"),
            Entity("road", EntityKind.LOCATION, "Road"),
        )
    )
    reducer = engine().for_world(world)
    state = seed()
    state = state.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"container_id": "bag"}) if i.id == "arrows" else i
                for i in state.items
            )
        }
    )
    reducer.validate(state)
    assert reducer.carried_weight(state, "a") == 17
    command = WorldGroundCommand(
        id="drop", actor_id="a", expected_revision=0, kind="drop", item_id="bag"
    )
    with pytest.raises(ValidationError, match="authority"):
        apply_world_ground(state, world, reducer, command, authorized_actor_id="b", system=True)
    dropped, result = apply_world_ground(
        state, world, reducer, command, authorized_actor_id="a", system=True
    )
    assert result.item_ids == ("arrows", "bag")
    assert reducer.carried_weight(dropped, "a") == 5
    assert all(i.owner_id == "a" for i in dropped.items)
    assert apply_world_ground(
        dropped, world, reducer, command, authorized_actor_id="a", system=True
    ) == (dropped, result)
    with pytest.raises(ConflictError, match="already used"):
        apply_world_ground(
            dropped,
            world,
            reducer,
            command.model_copy(update={"kind": "retrieve"}),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="authoritative retrieval"):
        reducer.apply(
            dropped, Equip(id="equip", actor_id="a", expected_revision=1, item_id="arrows")
        )
    retrieve = command.model_copy(
        update={"id": "retrieve", "kind": "retrieve", "expected_revision": 1}
    )
    away = replace(
        world,
        entities=tuple(
            replace(e, location_id="road") if e.id == "a" else e for e in world.entities
        ),
    )
    with pytest.raises(ValidationError, match="actual world location"):
        apply_world_ground(dropped, away, reducer, retrieve, authorized_actor_id="a", system=True)
    restored, _ = apply_world_ground(
        dropped, world, reducer, retrieve, authorized_actor_id="a", system=True
    )
    assert reducer.carried_weight(restored, "a") == 17
    assert all(
        not i.equipped and not i.ready and i.world_ground_location_id is None
        for i in restored.items
    )
    assert "world_ground_location_id" not in seed().items[0].model_dump()
