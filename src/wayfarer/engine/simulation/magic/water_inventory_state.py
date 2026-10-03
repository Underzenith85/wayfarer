"""B253/B288 one-gallon liquid receiver on the actual canonical inventory Item."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.simulation.magic.water_state import latest as world_bodies
from wayfarer.engine.simulation.resources import (
    Command,
    EquipmentSpec,
    Item,
    ResourceEvent,
    ResourceState,
    is_carried,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile

PREFIX = "water-inventory:"
WINESKIN = "equipment:wineskin"


class InventoryWaterReceiver(Record):
    id: Id
    actor_id: Id
    item_id: Id
    physically_empty: Literal[True] = True
    physically_intact: Literal[True] = True
    physically_touching: Literal[True] = True


class DeclareInventoryWaterReceiver(Command):
    kind: Literal["declare-inventory-water-receiver"] = "declare-inventory-water-receiver"
    receiver: InventoryWaterReceiver


class ReceiverObservation(Record):
    command_id: Id
    receiver: InventoryWaterReceiver
    item: Item
    ancestors: tuple[Item, ...]
    equipment_digest: str
    spec: EquipmentSpec


class InventoryWaterMaterial(Record):
    command_id: Id
    receiver_id: Id
    item_id: Id
    definition_id: Literal["equipment:wineskin"] = "equipment:wineskin"
    gallons: Literal[1] = 1
    pure_gallons: Literal[1] = 1
    mass_millipounds: Literal[8000] = 8000
    spec: EquipmentSpec


def digest(entry: EquipmentProfile) -> str:
    return hashlib.sha256(entry.model_dump_json().encode()).hexdigest()


def event_id(phase: str, command_id: str) -> str:
    return PREFIX + phase + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def materials(state: ResourceState) -> tuple[InventoryWaterMaterial, ...]:
    return tuple(
        InventoryWaterMaterial.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "filled:")
    )


def contents_mass(state: ResourceState, item_id: str) -> int:
    return sum(m.mass_millipounds for m in materials(state) if m.item_id == item_id)


def validate_inventory_material(state: ResourceState, specs: Mapping[str, EquipmentSpec]) -> None:
    liquid = materials(state)
    if len({m.item_id for m in liquid}) != len(liquid):
        raise ValidationError("Inventory Water material must have one conserved live carrier")
    if not liquid:
        return
    items = {i.id: i for i in state.items}
    if any(m.item_id in world_bodies(state) for m in liquid):
        raise ValidationError("Inventory Water cannot alias a World water material carrier")
    for material in liquid:
        item = items.get(material.item_id)
        spec = specs.get(material.definition_id)
        if (
            item is None
            or item.definition_id != WINESKIN
            or item.quantity != 1
            or (item.condition is not None and item.condition.disabled)
        ):
            raise ValidationError(
                "Filled wineskin cannot be removed, expended, split or disabled without a material adapter"
            )
        if (
            spec != material.spec
            or spec is None
            or spec.unit_weight != 250
            or spec.container_capacity != 8000
        ):
            raise ValidationError(
                "Inventory Water requires its exact pinned one-gallon wineskin profile"
            )


def observation(state: ResourceState, receiver_id: str) -> ReceiverObservation:
    found = tuple(
        ReceiverObservation.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "observed:")
        and ReceiverObservation.model_validate_json(e.kind).receiver.id == receiver_id
    )
    if len(found) != 1:
        raise ValidationError("Inventory Water requires one authenticated immutable receiver")
    return found[0]


def current(
    state: ResourceState, receiver_id: str, item_id: str, gallons: int
) -> ReceiverObservation:
    value = observation(state, receiver_id)
    item = next((i for i in state.items if i.id == item_id), None)
    if value.receiver.item_id != item_id or gallons != 1:
        raise ValidationError("Inventory Water creates exactly one gallon in the selected wineskin")
    if item != value.item or item is None or not is_carried(state, item):
        raise ConflictError("Inventory Water receiver custody or physical state changed")
    parents: list[Item] = []
    parent_id = item.container_id
    while parent_id is not None:
        parent = next((i for i in state.items if i.id == parent_id), None)
        if parent is None or parent in parents:
            raise ValidationError("Inventory Water receiving path is invalid")
        parents.append(parent)
        parent_id = parent.container_id
    if tuple(parents) != value.ancestors:
        raise ConflictError("Inventory Water receiving container path changed")
    if contents_mass(state, item_id) or any(i.container_id == item_id for i in state.items):
        raise ConflictError("Inventory Water requires its observed empty receiving vessel")
    return value


def fill(
    state: ResourceState, receiver_id: str, item_id: str, actor_id: str, command_id: str
) -> ResourceState:
    value = current(state, receiver_id, item_id, 1)
    if value.receiver.actor_id != actor_id:
        raise ValidationError("Inventory Water material belongs to another caster")
    material = InventoryWaterMaterial(
        command_id=command_id, receiver_id=receiver_id, item_id=item_id, spec=value.spec
    )
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id("filled", command_id),
                    at=state.game_time,
                    target_id=item_id,
                    kind=material.model_dump_json(),
                ),
            )
        }
    )
