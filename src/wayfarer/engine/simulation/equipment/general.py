"""Replay-safe use and attachment of B288-289 general equipment."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    Command,
    EquipmentSpec,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "general-equipment:"


class UseGeneralEquipment(Command):
    kind: Literal["use-general-equipment"] = "use-general-equipment"
    item_id: Id
    feature_index: int = Field(default=0, ge=0)
    consumable_item_id: Id | None = None
    duration_seconds: int = Field(default=1, ge=1)


class AttachAccessory(Command):
    kind: Literal["attach-accessory"] = "attach-accessory"
    item_id: Id
    target_item_id: Id | None = None


class RetrieveLanyard(Command):
    kind: Literal["retrieve-lanyard"] = "retrieve-lanyard"
    item_id: Id
    target_item_id: Id


GeneralEquipmentCommand = Annotated[
    UseGeneralEquipment | AttachAccessory | RetrieveLanyard, Field(discriminator="kind")
]
GENERAL_EQUIPMENT_ADAPTER: TypeAdapter[GeneralEquipmentCommand] = TypeAdapter(
    GeneralEquipmentCommand
)


class GeneralEquipmentOutcome(Record):
    command_id: Id
    status: Literal["used", "attached", "retrieved"]
    item_id: Id
    target_item_id: Id | None = None
    feature: GeneralEquipmentFeature
    modifier: int = 0
    active_seconds: int = Field(default=0, ge=0)


def _digest(command: GeneralEquipmentCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def history(state: ResourceState) -> tuple[GeneralEquipmentOutcome, ...]:
    return tuple(
        GeneralEquipmentOutcome.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def _use(
    state: ResourceState,
    item: Item,
    spec: EquipmentSpec,
    command: UseGeneralEquipment,
) -> tuple[list[Item], GeneralEquipmentFeature, str | None, int, Literal["used"]]:
    items = list(state.items)
    try:
        feature = spec.general[command.feature_index]
    except IndexError as exc:
        raise ValidationError("Unknown equipment feature") from exc
    active = command.duration_seconds
    if feature.duration_seconds is not None and active > feature.duration_seconds:
        raise ValidationError("Equipment use exceeds its source-defined duration")
    if feature.consumable_definition_id is not None:
        fuel = next((i for i in items if i.id == command.consumable_item_id), None)
        if (
            fuel is None
            or fuel.owner_id != command.actor_id
            or fuel.definition_id != feature.consumable_definition_id
            or fuel.quantity < feature.consumable_units
        ):
            raise ValidationError("Equipment use requires its exact consumable")
        index = items.index(fuel)
        if feature.duration_seconds is not None:
            if fuel.quantity != 1 or fuel.charges is None or fuel.charges < active:
                raise ValidationError("Timed fuel requires one explicit source-pinned reservoir")
            if fuel.charges == active:
                items.pop(index)
            else:
                items[index] = fuel.model_copy(update={"charges": fuel.charges - active})
        elif fuel.quantity == feature.consumable_units:
            items.pop(index)
        else:
            items[index] = fuel.model_copy(
                update={"quantity": fuel.quantity - feature.consumable_units}
            )
    elif feature.duration_seconds is not None and item.charges is not None:
        if item.charges < active:
            raise ValidationError("Equipment has insufficient operating charge")
        index = items.index(item)
        items[index] = item.model_copy(update={"charges": item.charges - active})
    return items, feature, None, active, "used"


def _attach(
    engine: ResourceEngine,
    state: ResourceState,
    item: Item,
    spec: EquipmentSpec,
    command: AttachAccessory,
) -> tuple[list[Item], GeneralEquipmentFeature, str | None, int, Literal["attached"]]:
    feature = next((value for value in spec.general if value.kind == "accessory"), None)
    if feature is None:
        raise ValidationError("Item is not an accessory")
    target_id = command.target_item_id
    if feature.target == "weapon":
        target = next((value for value in state.items if value.id == target_id), None)
        target_profile = engine.specs.get(target.definition_id) if target else None
        if (
            target is None
            or target.owner_id != command.actor_id
            or target_profile is None
            or target_profile.slot != "hand"
        ):
            raise ValidationError("Weapon accessory requires an owned handheld weapon")
    elif target_id is not None:
        raise ValidationError("Actor accessory cannot name a weapon target")
    if any(
        event.status == "attached"
        and event.item_id == item.id
        and event.target_item_id == target_id
        for event in history(state)
    ):
        raise ConflictError("Accessory is already attached")
    return list(state.items), feature, target_id, 0, "attached"


def _retrieve(
    state: ResourceState,
    item: Item,
    spec: EquipmentSpec,
    command: RetrieveLanyard,
) -> tuple[list[Item], GeneralEquipmentFeature, str | None, int, Literal["retrieved"]]:
    feature = next(
        (value for value in spec.general if value.kind == "accessory" and "lanyard" in value.note),
        None,
    )
    target = next((value for value in state.items if value.id == command.target_item_id), None)
    if feature is None or target is None or target.owner_id != command.actor_id:
        raise ValidationError("Lanyard retrieval requires its owned dropped weapon")
    return list(state.items), feature, target.id, 0, "retrieved"


def apply_general_equipment(
    engine: ResourceEngine,
    state: ResourceState,
    command: GeneralEquipmentCommand,
    *,
    actor_technology_level: int,
) -> tuple[ResourceState, GeneralEquipmentOutcome]:
    """Apply one owned, accessible item procedure without inventing a bonus."""
    digest = _digest(command)
    prior = next((r for r in state.receipts if r.command_id == command.id), None)
    if prior is not None:
        if prior.digest != digest:
            raise ConflictError("General-equipment command ID reused")
        event = next(e for e in state.events if e.id == PREFIX + command.id)
        return state, GeneralEquipmentOutcome.model_validate_json(event.kind)
    if command.expected_revision != state.revision:
        raise ConflictError("General-equipment revision changed")
    item = next((i for i in state.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id or item.container_id is not None:
        raise ValidationError("General equipment must be owned and accessible")
    spec = engine.specs[item.definition_id]
    if not spec.general:
        raise ValidationError("Item has no general-equipment procedure")
    if actor_technology_level < spec.minimum_technology_level:
        raise ValidationError("Equipment is unavailable at the actor technology level")

    status: Literal["used", "attached", "retrieved"]
    if isinstance(command, UseGeneralEquipment):
        items, feature, target_id, active, status = _use(state, item, spec, command)
    elif isinstance(command, AttachAccessory):
        items, feature, target_id, active, status = _attach(engine, state, item, spec, command)
    else:
        items, feature, target_id, active, status = _retrieve(state, item, spec, command)

    outcome = GeneralEquipmentOutcome(
        command_id=command.id,
        status=status,
        item_id=item.id,
        target_item_id=target_id,
        feature=feature,
        modifier=feature.modifier,
        active_seconds=active,
    )
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "items": tuple(items),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + command.id,
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=outcome.model_dump_json(),
                ),
            ),
        }
    )
    engine.validate(updated)
    return updated, outcome
