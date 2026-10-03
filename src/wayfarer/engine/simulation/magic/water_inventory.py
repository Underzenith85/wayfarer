"""Authenticated B253 casting admission for the private inventory material carrier."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.water_inventory_state import (
    PREFIX,
    WINESKIN,
    ReceiverObservation,
    current,
    digest,
    event_id,
    fill,
    observation,
)
from wayfarer.engine.simulation.magic.water_inventory_state import (
    DeclareInventoryWaterReceiver as DeclareInventoryWaterReceiver,
)
from wayfarer.engine.simulation.magic.water_inventory_state import (
    InventoryWaterReceiver as InventoryWaterReceiver,
)
from wayfarer.engine.simulation.magic.water_inventory_state import (
    contents_mass as contents_mass,
)
from wayfarer.engine.simulation.magic.water_inventory_state import (
    materials as materials,
)
from wayfarer.engine.simulation.magic.water_inventory_state import (
    validate_inventory_material as validate_inventory_material,
)
from wayfarer.engine.simulation.resources import Item, ResourceEvent, ResourceState, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def require_receiver(
    runtime: RulesContext,
    state: PlayState,
    receiver_id: str,
    actor_id: str,
    item_id: str,
    gallons: int,
) -> None:
    # deferred: common actor catalog resolution is outside the resource mass adapter.
    from wayfarer.engine.simulation.actors import catalog

    value = current(state.resources, receiver_id, item_id, gallons)
    if value.receiver.actor_id != actor_id or value.item.owner_id != actor_id:
        raise ValidationError("Inventory Water receiver belongs to another actor")
    entry = next((e for e in catalog(runtime).entries if e.definition_id == WINESKIN), None)
    if (
        entry is None
        or digest(entry) != value.equipment_digest
        or runtime.resources.specs.get(WINESKIN) != value.spec
    ):
        raise ConflictError("Inventory Water receiver profile changed")
    # Project the actual full contents into canonical capacity/load validation
    # BEFORE casting dice. This includes all ancestor and owner capacities.
    runtime.resources.validate(
        fill(state.resources, receiver_id, item_id, actor_id, "inventory-water:capacity-preview")
    )


def declare(
    runtime: RulesContext, state: PlayState, command: DeclareInventoryWaterReceiver
) -> ResourceState:
    # deferred: common actor catalog resolution is outside the resource mass adapter.
    from wayfarer.engine.simulation.actors import catalog

    receiver = command.receiver
    if any(
        e.id.startswith(PREFIX + "observed:")
        and ReceiverObservation.model_validate_json(e.kind).receiver.id == receiver.id
        for e in state.resources.events
    ):
        raise ConflictError("Inventory Water receiver observations cannot be replaced")
    runtime.approved_build(state, receiver.actor_id)
    runtime.resources.validate(state.resources)
    item = next((i for i in state.resources.items if i.id == receiver.item_id), None)
    entry = next((e for e in catalog(runtime).entries if e.definition_id == WINESKIN), None)
    if (
        item is None
        or entry is None
        or item.definition_id != WINESKIN
        or item.quantity != 1
        or item.owner_id != receiver.actor_id
        or not is_carried(state.resources, item)
        or item.condition is not None
    ):
        raise ValidationError("Inventory Water requires an intact carried canonical wineskin")
    if (
        any(e.id == item.id for e in state.world.entities)
        or contents_mass(state.resources, item.id)
        or any(i.container_id == item.id for i in state.resources.items)
    ):
        raise ValidationError(
            "Inventory Water requires an empty unique Item carrier without a World alias"
        )
    spec = entry.inventory_spec()
    if (
        runtime.resources.specs.get(WINESKIN) != spec
        or spec.unit_weight != 250
        or spec.container_capacity != 8000
    ):
        raise ValidationError("Inventory Water requires the exact B288 wineskin specification")
    parents: list[Item] = []
    parent_id = item.container_id
    while parent_id is not None:
        parent = next(i for i in state.resources.items if i.id == parent_id)
        parents.append(parent)
        parent_id = parent.container_id
    value = ReceiverObservation(
        command_id=command.id,
        receiver=receiver,
        item=item,
        ancestors=tuple(parents),
        equipment_digest=digest(entry),
        spec=spec,
    )
    return state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=event_id("observed", command.id),
                    at=state.resources.game_time,
                    target_id=receiver.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def touching(
    runtime: RulesContext,
    state: PlayState,
    receiver_id: str,
    actor_id: str,
    item_id: str,
    gallons: int,
) -> bool:
    require_receiver(runtime, state, receiver_id, actor_id, item_id, gallons)
    return observation(state.resources, receiver_id).receiver.physically_touching
