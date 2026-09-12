"""Typed, event-backed Jumper, Snatcher, and Warp execution."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.world_travel import WorldTravelTraits, world_travel_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import Command, Pool, ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "world-travel-use:"
TravelKind = Literal["spatial", "time", "world", "snatch"]


class TravelRoute(Record):
    id: str
    definition_id: str
    actor_id: str
    origin_id: str
    destination_id: str
    kind: TravelKind
    available_at: int = Field(default=0, ge=0)
    iq: int = Field(default=10, ge=1)
    roll: int = Field(default=10, ge=3, le=18)
    difficulty_modifier: int = Field(default=0, ge=-100, le=100)
    preparation_bonus: int = Field(default=0, ge=0, le=10)
    fatigue_cost: int = Field(default=0, ge=0)
    object_id: str | None = None
    object_weight: int = Field(default=0, ge=0)
    failure_destination_id: str | None = None


class TravelCommand(Command):
    definition_id: str
    route_id: str


class TravelOutcome(Record):
    outcome: Literal["arrived", "retrieved", "failed", "misjumped"]
    actor_id: str
    definition_id: str
    destination_id: str | None = None
    object_id: str | None = None
    effective_target: int
    roll: int


class TravelEvent(Record):
    command_id: str
    route_id: str
    definition_id: str
    outcome: TravelOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[TravelEvent, ...]:
    return tuple(
        TravelEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _spend(resources: ResourceState, actor_id: str, amount: int) -> tuple[Pool, ...]:
    if amount == 0:
        return resources.pools
    pool_id = "fp:" + actor_id
    pool = next((value for value in resources.pools if value.id == pool_id), None)
    if pool is None or pool.current < amount:
        raise ValidationError("World travel requires available fatigue")
    return tuple(
        value.model_copy(update={"current": value.current - amount})
        if value.id == pool_id
        else value
        for value in resources.pools
    )


def _validate_route(route: TravelRoute, traits: WorldTravelTraits) -> None:
    if route.definition_id == "advantage:jumper":
        if route.kind != traits.jumper_kind() or route.object_id is not None:
            raise ValidationError("Jumper route differs from the approved kind")
    elif route.definition_id == "advantage:warp":
        if route.kind != "spatial" or route.object_id is not None:
            raise ValidationError("Warp requires a spatial actor route")
    elif route.definition_id == "advantage:snatcher":
        if (
            route.kind != "snatch"
            or route.object_id is None
            or route.object_weight < 1
            or route.object_weight > traits.snatcher_weight()
        ):
            raise ValidationError("Snatcher route exceeds its approved object binding")
    else:
        raise ValidationError("Unknown world-travel trait")


def _move_actor(world: World, actor_id: str, destination_id: str) -> World:
    updated = replace(
        world,
        entities=tuple(
            replace(entity, location_id=destination_id) if entity.id == actor_id else entity
            for entity in world.entities
        ),
    )
    updated.validate()
    return updated


def _retrieve(world: World, actor_id: str, object_id: str, destination_id: str) -> World:
    updated = replace(
        world,
        entities=tuple(
            replace(entity, location_id=destination_id, owner_id=actor_id)
            if entity.id == object_id
            else entity
            for entity in world.entities
        ),
    )
    updated.validate()
    return updated


def apply_world_travel(
    resources: ResourceState,
    world: World,
    command: TravelCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    routes: tuple[TravelRoute, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, World, TravelOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("World travel requires actor authority")
    previous = next((event for event in history(resources) if event.command_id == command.id), None)
    if previous is not None:
        if (
            previous.route_id == command.route_id
            and previous.definition_id == command.definition_id
        ):
            return resources, world, previous.outcome
        raise ConflictError("World-travel command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("World-travel revision changed")
    route = next((value for value in routes if value.id == command.route_id), None)
    if (
        route is None
        or route.definition_id != command.definition_id
        or route.actor_id != command.actor_id
    ):
        raise ValidationError("Authored world-travel route is unavailable")
    if resources.game_time < route.available_at:
        raise ConflictError("World-travel preparation is not complete")
    traits = world_travel_traits(build, definitions)
    if not traits.has(command.definition_id):
        raise ValidationError("World-travel trait is not in the approved build")
    _validate_route(route, traits)
    entities = {entity.id: entity for entity in world.entities}
    actor = entities.get(command.actor_id)
    destination = entities.get(route.destination_id)
    failure_destination = (
        None if route.failure_destination_id is None else entities.get(route.failure_destination_id)
    )
    if (
        actor is None
        or actor.location_id != route.origin_id
        or destination is None
        or destination.kind is not EntityKind.LOCATION
        or route.origin_id not in entities
        or (
            route.failure_destination_id is not None
            and (failure_destination is None or failure_destination.kind is not EntityKind.LOCATION)
        )
    ):
        raise ValidationError("World-travel route context changed")
    if route.object_id is not None:
        item = entities.get(route.object_id)
        if item is None or item.kind is not EntityKind.OBJECT or item.owner_id is not None:
            raise ValidationError("Snatcher object context changed")

    pools = _spend(resources, command.actor_id, route.fatigue_cost)
    target = route.iq + route.difficulty_modifier + route.preparation_bonus
    if command.definition_id == "advantage:warp":
        target += traits.warp_reliability()
    critical = route.roll >= 18 or route.roll == 17 and target <= 15
    succeeded = route.roll <= target and not critical
    updated_world = world
    result: Literal["arrived", "retrieved", "failed", "misjumped"]
    actual_destination: str | None = None
    if critical and route.failure_destination_id is not None:
        result = "misjumped"
        actual_destination = route.failure_destination_id
        updated_world = _move_actor(world, command.actor_id, actual_destination)
    elif not succeeded:
        result = "failed"
    elif command.definition_id == "advantage:snatcher":
        assert route.object_id is not None
        result = "retrieved"
        actual_destination = route.origin_id
        updated_world = _retrieve(world, command.actor_id, route.object_id, route.origin_id)
    else:
        result = "arrived"
        actual_destination = route.destination_id
        updated_world = _move_actor(world, command.actor_id, route.destination_id)
    outcome = TravelOutcome(
        outcome=result,
        actor_id=command.actor_id,
        definition_id=command.definition_id,
        destination_id=actual_destination,
        object_id=route.object_id if result == "retrieved" else None,
        effective_target=target,
        roll=route.roll,
    )
    event = TravelEvent(
        command_id=command.id,
        route_id=route.id,
        definition_id=command.definition_id,
        outcome=outcome,
    )
    state = resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "pools": pools,
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    )
    return state, updated_world, outcome
