"""Trusted B482 physical classification of a current plain wearable item."""

import hashlib
from typing import Literal

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.haste_state import items as haste_items
from wayfarer.engine.simulation.magic.haste_state import record
from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "haste-wearable-construction:"


class WearableConstruction(Record):
    item_id: Id
    definition_id: Id
    form: Literal["clothing", "jewelry"]
    slot: Id
    spec_json: str
    profile_json: str


class DeclareHasteWearableConstruction(Command):
    construction: WearableConstruction


def identifier(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def constructions(resources: ResourceState) -> tuple[WearableConstruction, ...]:
    return tuple(
        WearableConstruction.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def validate(runtime: RulesContext, state: PlayState, value: WearableConstruction) -> None:
    item = next((i for i in state.resources.items if i.id == value.item_id), None)
    equipment = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
    profile = (
        next((p for p in equipment.entries if p.definition_id == value.definition_id), None)
        if equipment
        else None
    )
    spec = runtime.resources.specs.get(value.definition_id)
    if (
        item is None
        or item.definition_id != value.definition_id
        or item.quantity != 1
        or item.container_id is not None
        or item.ground is not None
        or item.world_ground_location_id is not None
        or (item.condition is not None and item.condition.disabled)
        or any(b.spell_id == "haste" for b in item.enchantments)
        or any(f.item_id == value.item_id for f in haste_items(state.resources))
        or profile is None
        or profile.armor is not None
        or profile.modes
        or profile.shield is not None
        or profile.ammunition
        or profile.container_capacity_millipounds is not None
        or spec is None
        or spec.ammunition
        or spec.container_capacity is not None
        or spec.slot != value.slot
        or profile.slot != value.slot
        or value.slot.startswith("hand")
        or spec.model_dump_json() != value.spec_json
        or profile.model_dump_json() != value.profile_json
    ):
        raise ValidationError("Wearable construction requires its current plain individual item")


def require(runtime: RulesContext, state: PlayState, item_id: str) -> WearableConstruction:
    value = next((c for c in constructions(state.resources) if c.item_id == item_id), None)
    if value is None:
        raise ValidationError("Plain Haste wearables require trusted physical construction")
    validate(runtime, state, value)
    return value


def declare(
    runtime: RulesContext, state: PlayState, command: DeclareHasteWearableConstruction
) -> ResourceState:
    if any(c.item_id == command.construction.item_id for c in constructions(state.resources)):
        raise ConflictError("A wearable's physical classification cannot be replaced")
    validate(runtime, state, command.construction)
    return record(
        state.resources, PREFIX, command.id, command.construction.item_id, command.construction
    )
