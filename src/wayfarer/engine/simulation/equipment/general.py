"""Replayable use and attachment procedures for B288-B289 equipment."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    EquipmentAttachment,
    EquipmentSpec,
    Item,
    Receipt,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class EquipmentContext(Record):
    campaign_technology_level: Annotated[int, Field(ge=0)]
    maximum_legality_class: Annotated[int, Field(ge=0, le=4)] = 4
    skill_technology_level: Annotated[int, Field(ge=0)] | None = None
    missing_important_items: Annotated[int, Field(ge=0)] = 0
    damage_penalty: Annotated[int, Field(ge=0, le=3)] = 0


class UseEquipment(Record):
    kind: Literal["use"] = "use"
    id: Id
    actor_id: Id
    expected_revision: Annotated[int, Field(ge=0)]
    item_id: Id
    use_id: Id
    duration_seconds: Annotated[int, Field(ge=1)] = 1
    consumable_item_id: Id | None = None


class AttachEquipment(Record):
    kind: Literal["attach"] = "attach"
    id: Id
    actor_id: Id
    expected_revision: Annotated[int, Field(ge=0)]
    accessory_item_id: Id
    target_item_id: Id | None = None


class DetachEquipment(Record):
    kind: Literal["detach"] = "detach"
    id: Id
    actor_id: Id
    expected_revision: Annotated[int, Field(ge=0)]
    accessory_item_id: Id


EquipmentCommand = Annotated[
    UseEquipment | AttachEquipment | DetachEquipment, Field(discriminator="kind")
]
COMMAND: TypeAdapter[EquipmentCommand] = TypeAdapter(EquipmentCommand)


class EquipmentOutcome(Record):
    command_id: Id
    status: Literal["used", "attached", "detached"]
    item_id: Id
    use_id: Id | None = None
    modifier: int = 0
    capacity: int | None = None
    range_yards: int | None = None
    protection: int | None = None
    consumed_item_id: Id | None = None
    consumed_quantity: int = 0
    duration_seconds: int = 0


class EquipmentEvent(Record):
    command: EquipmentCommand
    digest: str
    outcome: EquipmentOutcome


def equipment_modifier(
    quality: Literal["none", "improvised", "basic", "good", "fine", "best"],
    *,
    technological: bool,
    technology_level: int,
    missing_important_items: int = 0,
    damage_penalty: int = 0,
) -> int:
    """Campaigns B345 equipment quality, missing-parts, and damage modifiers."""
    if technology_level < 0 or missing_important_items < 0 or not 0 <= damage_penalty <= 3:
        raise ValidationError("Invalid equipment-modifier context")
    base = {
        "none": -10 if technological else -5,
        "improvised": -5 if technological else -2,
        "basic": 0,
        "good": 1,
        "fine": 2,
        "best": max(2, technology_level // 2),
    }[quality]
    if quality in ("none", "improvised") and (missing_important_items or damage_penalty):
        raise ValidationError("Missing-item and damage penalties require basic or better equipment")
    return base - missing_important_items - damage_penalty


def accessory_effects(
    engine: ResourceEngine,
    state: ResourceState,
    target_item_id: Id,
    *,
    aimed_seconds: int = 0,
    aiming_dot_visible_to_target: bool = False,
) -> dict[str, int | bool]:
    """Combine only attached B412 effects; incompatible stacking fails closed."""
    if aimed_seconds < 0:
        raise ValidationError("Aim duration cannot be negative")
    attached = [row for row in state.equipment_attachments if row.target_item_id == target_item_id]
    specs = []
    for row in attached:
        item = next(item for item in state.items if item.id == row.accessory_item_id)
        spec = engine.specs[item.definition_id].accessory
        if spec is None:
            raise ValidationError("Attachment no longer resolves to an accessory")
        specs.append(spec)
    scopes = [spec for spec in specs if spec.kind == "scope"]
    if len(scopes) > 1:
        raise ValidationError("Only one scope or telescopic aid may be used at a time")
    attack = sum(spec.attack_bonus for spec in specs)
    accuracy = sum(
        spec.accuracy_bonus for spec in scopes if aimed_seconds >= spec.minimum_aim_seconds
    )
    return {
        "attack_modifier": attack,
        "target_dodge_modifier": int(
            aiming_dot_visible_to_target
            and any(spec.dodge_bonus_to_visible_target for spec in specs)
        ),
        "accuracy_modifier": accuracy,
        "hearing_modifier": sum(spec.hearing_modifier for spec in specs),
        "damage_modifier_per_die": -sum(spec.kind == "silencer" for spec in specs),
        "infravision": any(spec.grants_infravision for spec in specs),
    }


def _compatible(
    accessory: Literal["actor", "bow", "pistol", "pistol-or-smg", "ranged-weapon"],
    target: Item | None,
    target_spec: EquipmentSpec,
) -> bool:
    accessory_spec = accessory
    if accessory_spec == "actor":
        return target is None
    if target is None:
        return False
    if accessory_spec == "ranged-weapon":
        return bool(target_spec.ranged_weapon)
    skills = set(target_spec.weapon_skill_ids)
    if accessory_spec == "pistol":
        return "skill:guns-pistol" in skills
    if accessory_spec == "pistol-or-smg":
        return bool(skills & {"skill:guns-pistol", "skill:guns-smg"})
    if accessory_spec == "bow":
        return bool(skills & {"skill:bow", "skill:crossbow"})


def _consume(items: tuple[Item, ...], item_id: str, quantity: int) -> tuple[Item, ...]:
    item = next((item for item in items if item.id == item_id), None)
    if item is None:
        raise ValidationError("Required consumable is unavailable")
    available = item.charges if item.charges is not None else item.quantity
    if available < quantity:
        raise ValidationError("Required consumable is exhausted")
    if item.charges is not None:
        remaining = item.charges - quantity
        return tuple(
            row.model_copy(update={"charges": remaining}) if row.id == item_id else row
            for row in items
        )
    if item.quantity == quantity:
        return tuple(row for row in items if row.id != item_id)
    return tuple(
        row.model_copy(update={"quantity": row.quantity - quantity}) if row.id == item_id else row
        for row in items
    )


def _prior_event(state: ResourceState, command_id: Id) -> EquipmentEvent | None:
    for row in state.equipment_events:
        candidate = EquipmentEvent.model_validate(row)
        if candidate.command.id == command_id:
            return candidate
    return None


def _available_item(
    engine: ResourceEngine,
    state: ResourceState,
    command: EquipmentCommand,
    context: EquipmentContext,
) -> tuple[Item, EquipmentSpec, int]:
    item_id = command.item_id if isinstance(command, UseEquipment) else command.accessory_item_id
    item = next((row for row in state.items if row.id == item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Equipment is unavailable to this actor")
    spec = engine.specs[item.definition_id]
    effective_tl = (
        context.skill_technology_level if spec.skill_relative_technology else spec.technology_level
    )
    if effective_tl is None or effective_tl > context.campaign_technology_level:
        raise ValidationError("Equipment is unavailable at this technology level")
    if spec.legality_class > context.maximum_legality_class:
        raise ValidationError("Equipment legality class is unavailable in this context")
    return item, spec, effective_tl


def _use_equipment(
    state: ResourceState,
    command: UseEquipment,
    context: EquipmentContext,
    item: Item,
    spec: EquipmentSpec,
    effective_tl: int,
) -> tuple[tuple[Item, ...], tuple[EquipmentAttachment, ...], EquipmentOutcome]:
    selected = next((row for row in spec.uses if row.id == command.use_id), None)
    if selected is None:
        raise ValidationError("Equipment does not support the requested use")
    if selected.minimum_technology_level is not None and effective_tl < selected.minimum_technology_level:
        raise ValidationError("Equipment use is unavailable at this technology level")
    items = state.items
    consumed_id = command.consumable_item_id
    consumed = 0
    supply_definition = selected.consumes_definition_id
    if spec.fuel is not None and spec.fuel.kind == "appliance":
        supply_definition = spec.fuel.fuel_id
    if supply_definition is not None:
        supply = next((row for row in state.items if row.id == consumed_id), None)
        if (
            supply is None
            or supply.owner_id != command.actor_id
            or supply.definition_id != supply_definition
        ):
            raise ValidationError("Required consumable does not match the equipment use")
        consumed = selected.consumes_quantity
        if spec.fuel is not None:
            consumed = (
                command.duration_seconds + spec.fuel.seconds_per_charge - 1
            ) // spec.fuel.seconds_per_charge
        items = _consume(items, supply.id, consumed)
        consumed_id = supply.id
    elif spec.fuel is not None:
        consumed_id = item.id
        consumed = (
            command.duration_seconds + spec.fuel.seconds_per_charge - 1
        ) // spec.fuel.seconds_per_charge
        items = _consume(items, item.id, consumed)
    outcome = EquipmentOutcome(
        command_id=command.id,
        status="used",
        item_id=item.id,
        use_id=selected.id,
        modifier=selected.modifier - context.missing_important_items - context.damage_penalty,
        capacity=selected.capacity,
        range_yards=selected.range_yards,
        protection=selected.protection,
        consumed_item_id=consumed_id,
        consumed_quantity=consumed,
        duration_seconds=command.duration_seconds,
    )
    return items, state.equipment_attachments, outcome


def _attach_equipment(
    engine: ResourceEngine,
    state: ResourceState,
    command: AttachEquipment,
    item: Item,
    spec: EquipmentSpec,
) -> tuple[tuple[Item, ...], tuple[EquipmentAttachment, ...], EquipmentOutcome]:
    accessory = spec.accessory
    if accessory is None:
        raise ValidationError("Item is not an attachable accessory")
    target = next((row for row in state.items if row.id == command.target_item_id), None)
    if command.target_item_id is not None and (target is None or target.owner_id != command.actor_id):
        raise ValidationError("Accessory target is unavailable")
    target_spec = engine.specs[target.definition_id] if target is not None else spec
    if not _compatible(accessory.compatible, target, target_spec):
        raise ValidationError("Accessory is incompatible with its target")
    if any(row.accessory_item_id == item.id for row in state.equipment_attachments):
        raise ConflictError("Accessory is already attached")
    items = state.items
    attachments = state.equipment_attachments
    if target is None:
        items = tuple(
            row.model_copy(update={"equipped": True}) if row.id == item.id else row
            for row in items
        )
    else:
        attachments = (
            *attachments,
            EquipmentAttachment(
                accessory_item_id=item.id,
                target_item_id=target.id,
                mounted_at=state.game_time,
            ),
        )
    return items, attachments, EquipmentOutcome(
        command_id=command.id, status="attached", item_id=item.id
    )


def _detach_equipment(
    state: ResourceState,
    command: DetachEquipment,
    item: Item,
) -> tuple[tuple[Item, ...], tuple[EquipmentAttachment, ...], EquipmentOutcome]:
    attached = next(
        (row for row in state.equipment_attachments if row.accessory_item_id == item.id), None
    )
    if attached is None and not item.equipped:
        raise ConflictError("Accessory is not attached")
    attachments = tuple(
        row for row in state.equipment_attachments if row.accessory_item_id != item.id
    )
    items = tuple(
        row.model_copy(update={"equipped": False}) if row.id == item.id else row
        for row in state.items
    )
    return items, attachments, EquipmentOutcome(
        command_id=command.id, status="detached", item_id=item.id
    )


def apply_equipment(
    engine: ResourceEngine,
    state: ResourceState,
    command: EquipmentCommand,
    context: EquipmentContext,
    *,
    system: bool = False,
) -> tuple[ResourceState, EquipmentOutcome]:
    """Execute one authorized deterministic equipment command with CAS and replay."""
    if not system:
        raise ValidationError("Equipment use requires engine authority")
    engine.validate(state)
    digest = hashlib.sha256(COMMAND.dump_json(command)).hexdigest()
    receipt = next((row for row in state.receipts if row.command_id == command.id), None)
    event = _prior_event(state, command.id)
    if receipt is not None:
        if receipt.digest != digest or event is None or event.digest != digest:
            raise ConflictError("Equipment command ID reused with different payload")
        return state, event.outcome
    if command.expected_revision != state.revision:
        raise ConflictError("Equipment resource revision changed")

    item, spec, effective_tl = _available_item(engine, state, command, context)
    if isinstance(command, UseEquipment):
        items, attachments, outcome = _use_equipment(
            state, command, context, item, spec, effective_tl
        )
    elif isinstance(command, AttachEquipment):
        items, attachments, outcome = _attach_equipment(engine, state, command, item, spec)
    else:
        items, attachments, outcome = _detach_equipment(state, command, item)

    event = EquipmentEvent(command=command, digest=digest, outcome=outcome)
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "items": items,
            "equipment_attachments": attachments,
            "receipts": (*state.receipts, Receipt(command_id=command.id, digest=digest)),
            "equipment_events": (*state.equipment_events, event.model_dump(mode="json")),
        }
    )
    engine.validate(updated)
    return updated, outcome


def starting_wealth(technology_level: int, wealth_multiplier: Decimal = Decimal(1)) -> Decimal:
    """Characters B27/B264 starting wealth table with an explicit wealth multiplier."""
    if technology_level not in range(13) or wealth_multiplier < 0:
        raise ValidationError("Invalid starting-wealth context")
    base = (
        250,
        500,
        750,
        1_000,
        2_000,
        5_000,
        10_000,
        15_000,
        20_000,
        30_000,
        50_000,
        75_000,
        100_000,
    )[technology_level]
    return Decimal(base) * wealth_multiplier
