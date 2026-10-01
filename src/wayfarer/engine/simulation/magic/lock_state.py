"""Private object facts and access consequences for B251/B253 lock spells."""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.equipment.salvage_state import tasks as salvage_tasks
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEffect, active_spells
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "lock-state:"


class LockFixture(Record):
    object_id: Id
    location_id: Id
    kind: Literal["door", "lock"]
    mechanical_lock: bool = True
    difficulty_modifier: int = Field(default=0, ge=-20, le=20)
    size_modifier: int = Field(default=0, ge=-10, le=30)
    # An optional canonical durable object, never an authored destruction flag.
    item_id: Id | None = None
    passage: tuple[Id, Id] | None = None

    @model_validator(mode="after")
    def door_passage(self) -> LockFixture:
        if self.passage is not None and (
            self.kind != "door"
            or len(set(self.passage)) != 2
            or self.location_id not in self.passage
        ):
            raise ValueError("A door passage requires two different adjacent locations")
        return self


class LockState(Record):
    fixture: LockFixture
    locked: bool
    closed: bool

    @model_validator(mode="after")
    def mechanical_state(self) -> LockState:
        if self.locked and not self.fixture.mechanical_lock:
            raise ValueError("A mechanical lock is required for a locked mechanism")
        if self.locked and not self.closed:
            raise ValueError("An open lock or door cannot be mechanically locked")
        return self


class LockEvent(Record):
    command_id: Id
    state: LockState


def latest(state: ResourceState) -> dict[str, LockState]:
    found: dict[str, LockState] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            value = LockEvent.model_validate_json(event.kind).state
            found[value.fixture.object_id] = value
    return found


def save(state: ResourceState, value: LockState, command_id: str) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=value.fixture.object_id,
                    kind=LockEvent(command_id=command_id, state=value).model_dump_json(),
                ),
            )
        }
    )


def validate_fixture(
    world: World, state: ResourceState, fixture: LockFixture, *, declaring: bool = False
) -> None:
    entities = {e.id: e for e in world.entities}
    target, location = entities.get(fixture.object_id), entities.get(fixture.location_id)
    if (
        target is None
        or target.kind is not EntityKind.OBJECT
        or target.location_id != fixture.location_id
        or location is None
        or location.kind is not EntityKind.LOCATION
    ):
        raise ValidationError("A lock fixture requires a placed world object")
    if fixture.passage is not None:
        if any(
            location_id not in entities or entities[location_id].kind is not EntityKind.LOCATION
            for location_id in fixture.passage
        ) or not any(
            {c.source_id, c.destination_id} == set(fixture.passage) for c in world.connections
        ):
            raise ValidationError("A door passage requires an existing world connection")
    if fixture.item_id is not None:
        if any(
            v.fixture.item_id == fixture.item_id and v.fixture.object_id != fixture.object_id
            for v in latest(state).values()
        ):
            raise ValidationError("A durable item cannot back two distinct lock objects")
        item = next((i for i in state.items if i.id == fixture.item_id), None)
        if item is None:
            existing = latest(state).get(fixture.object_id)
            if (
                not declaring
                and existing is not None
                and existing.fixture == fixture
                and _terminal_item(state, fixture.item_id)
            ):
                return
            raise ValidationError("A destructible lock requires a canonical durable item")
        if item.condition is None or item.quantity != 1:
            raise ValidationError("A destructible lock requires one canonical durable instance")
        if declaring and item.condition.destroyed:
            raise ValidationError("Declare a lock only on an intact durable instance")
        if item.condition.destroyed:
            return
        if fixture.kind == "door" and (
            item.world_ground_location_id != fixture.location_id
            or item.ground is not None
            or item.container_id is not None
            or item.equipped
            or item.ready
        ):
            raise ValidationError(
                "A fixed door requires its uncontained durable item on matching world ground"
            )
        if fixture.kind == "lock" and item.world_ground_location_id not in (
            None,
            fixture.location_id,
        ):
            raise ValidationError("Lock backing item is at another world location")
        if fixture.kind == "lock" and item.world_ground_location_id is None:
            owner = entities.get(item.owner_id)
            if (
                item.ground is not None
                or item.container_id is not None
                or owner is None
                or owner.location_id != fixture.location_id
            ):
                raise ValidationError("Lock backing item has no matching current placement")


def _terminal_item(state: ResourceState, item_id: str) -> bool:
    return any(r.item_id == item_id and r.condition.destroyed for r in state.object_results) or any(
        t.item_id == item_id and t.status == "completed" for t in salvage_tasks(state)
    )


def destroyed(state: ResourceState, value: LockState) -> bool:
    if value.fixture.item_id is None:
        return False
    item = next((i for i in state.items if i.id == value.fixture.item_id), None)
    if item is None and _terminal_item(state, value.fixture.item_id):
        return True
    if item is None or item.condition is None:
        raise ValidationError("The lock's canonical durable item is missing")
    return item.condition.destroyed


def magelocks(state: ResourceState, object_id: str) -> tuple[RuntimeSpellEffect, ...]:
    return tuple(
        effect
        for effect in active_spells(state)
        if effect.spell_id == "magelock" and effect.target_id == object_id
    )


def opening_allowed(state: ResourceState, value: LockState) -> bool:
    return destroyed(state, value) or (
        not value.locked and not magelocks(state, value.fixture.object_id)
    )


def passage_blocked(state: ResourceState, origin: str | None, destination: str) -> bool:
    return any(
        value.fixture.passage is not None
        and set(value.fixture.passage) == {origin, destination}
        and not destroyed(state, value)
        and (value.closed or not opening_allowed(state, value))
        for value in latest(state).values()
    )
