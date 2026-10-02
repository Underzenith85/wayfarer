"""Private canonical B253 water quantities; no public spell schema expansion."""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "water-state:"


class WaterBody(Record):
    """An authored, placed nonliving source or receiving container.

    Significance is a director world fact: B253 provides no gallon threshold.
    Capacity and custody refer to the actual world object, not a player claim.
    Whole gallons are the supported material boundary; fractional flows await
    their own source/energy and inventory integration.
    """

    object_id: Id
    location_id: Id
    position: tuple[int, int] = (0, 0)
    gallons: int = Field(ge=0)
    pure_gallons: int = Field(ge=0)
    nature: str = Field(min_length=1)
    significant: bool = False
    capacity_gallons: int | None = Field(default=None, ge=0)
    form: Literal["liquid", "ice", "steam"] = "liquid"
    # Destroy supports bounded isolated authored portions, not a whole deep lake.
    depth_yards: int = Field(default=1, ge=1)
    surrounding_water: bool = False

    @model_validator(mode="after")
    def quantities(self) -> WaterBody:
        if self.pure_gallons > self.gallons:
            raise ValueError("Pure water cannot exceed total water")
        if self.capacity_gallons is not None and self.gallons > self.capacity_gallons:
            raise ValueError("Water exceeds receiving container capacity")
        return self


class WaterEvent(Record):
    command_id: Id
    body: WaterBody


def latest(state: ResourceState) -> dict[str, WaterBody]:
    found: dict[str, WaterBody] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            body = WaterEvent.model_validate_json(event.kind).body
            found[body.object_id] = body
    return found


def save(state: ResourceState, body: WaterBody, command_id: str) -> ResourceState:
    body = WaterBody.model_validate(body.model_dump())
    payload = WaterEvent(command_id=command_id, body=body).model_dump_json()
    event_id = PREFIX + hashlib.sha256((command_id + ":" + body.object_id).encode()).hexdigest()
    previous = next((event for event in state.events if event.id == event_id), None)
    if previous is not None:
        if previous.kind != payload:
            raise ConflictError("Water command already records another material consequence")
        return state
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id, at=state.game_time, target_id=body.object_id, kind=payload
                ),
            )
        }
    )


def validate_body(world: World, body: WaterBody) -> None:
    entity = next((e for e in world.entities if e.id == body.object_id), None)
    location = next((e for e in world.entities if e.id == body.location_id), None)
    if (
        entity is None
        or entity.kind is not EntityKind.OBJECT
        or entity.location_id != body.location_id
        or location is None
        or location.kind is not EntityKind.LOCATION
    ):
        raise ValidationError("Water requires a placed nonliving world object")


def declare(world: World, state: ResourceState, body: WaterBody, command_id: str) -> ResourceState:
    validate_body(world, body)
    if body.object_id in latest(state):
        raise ConflictError("Water body already declared; material changes require an operation")
    return save(state, body, command_id)
