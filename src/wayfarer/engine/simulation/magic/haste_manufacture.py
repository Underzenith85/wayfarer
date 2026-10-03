"""B482 armor Haste manufacture, captured only by a new project producer."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.enchanting import EnchantmentRecipe
from wayfarer.engine.simulation.magic.haste_state import ITEM, HasteItem, record
from wayfarer.engine.simulation.magic.haste_state import items as haste_items
from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "haste-manufacture:"
OBSERVATION = PREFIX + "observation:"
PROJECT = PREFIX + "project:"


class ObserveHasteManufacture(Command):
    kind: Literal["observe-haste-manufacture"] = "observe-haste-manufacture"
    recipe_id: Id
    target_item_id: Id
    levels: int = Field(ge=1, le=3)


class HasteManufacture(Record):
    recipe_id: Id
    target_item_id: Id
    levels: int = Field(ge=1, le=3)
    recipe_json: str
    physical_hash: str
    project_id: Id | None = None


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def facts(resources: ResourceState) -> tuple[HasteManufacture, ...]:
    return tuple(
        HasteManufacture.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX)
    )


def recipe_for(runtime: RulesContext, recipe_id: str) -> EnchantmentRecipe:
    rules = runtime.rules.enchanting
    recipe = next((r for r in rules.recipes if r.id == recipe_id), None) if rules else None
    if recipe is None:
        raise ValidationError("Haste manufacture requires an existing canonical recipe")
    return EnchantmentRecipe.model_validate(recipe.model_dump())


def _physical(runtime: RulesContext, state: PlayState, target_id: str) -> str:
    item = next((i for i in state.resources.items if i.id == target_id), None)
    if (item is not None and any(b.spell_id == "haste" for b in item.enchantments)) or any(
        f.item_id == target_id for f in haste_items(state.resources)
    ):
        raise ValidationError("Haste manufacture target already has Haste")
    equipment = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
    profile = (
        next((e for e in equipment.entries if item and e.definition_id == item.definition_id), None)
        if equipment
        else None
    )
    spec = runtime.resources.specs.get(item.definition_id) if item else None
    if (
        item is None
        or item.quantity != 1
        or item.container_id is not None
        or item.ground is not None
        or item.world_ground_location_id is not None
        or (item.condition is not None and item.condition.disabled)
        or profile is None
        or profile.armor is None
        or profile.modes
        or profile.shield
        or profile.ammunition
        or profile.container_capacity_millipounds is not None
        or spec is None
        or spec.slot is None
        or spec.slot.startswith("hand")
    ):
        raise ValidationError("Haste manufacture requires current individual wearable armor")
    return _digest(
        {
            "definition": item.definition_id,
            "spec": spec.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
        }
    )


def validate(runtime: RulesContext, state: PlayState, fact: HasteManufacture) -> None:
    recipe = recipe_for(runtime, fact.recipe_id)
    if (
        recipe.spell_id != "spell:haste"
        or recipe.effect_id != "effect:haste"
        or recipe.energy_required != 250 * fact.levels
        or recipe.runtime_spell_id is not None
        or recipe.runtime_family is not None
        or recipe.activation != "cast"
        or recipe.requires_magery
        or recipe.power_reduction
        or recipe.maximum_charges is not None
        or recipe.maintenance_energy != 0
        or recipe.model_dump_json() != fact.recipe_json
        or _physical(runtime, state, fact.target_item_id) != fact.physical_hash
    ):
        raise ValidationError("Haste manufacture recipe or physical identity changed")


def observe(
    runtime: RulesContext, state: PlayState, command: ObserveHasteManufacture
) -> HasteManufacture:
    if any(
        f.target_item_id == command.target_item_id and f.recipe_id == command.recipe_id
        for f in facts(state.resources)
    ):
        raise ConflictError("Haste manufacture observation cannot be replaced")
    fact = HasteManufacture(
        recipe_id=command.recipe_id,
        target_item_id=command.target_item_id,
        levels=command.levels,
        recipe_json=recipe_for(runtime, command.recipe_id).model_dump_json(),
        physical_hash=_physical(runtime, state, command.target_item_id),
    )
    validate(runtime, state, fact)
    return fact


def capture(
    runtime: RulesContext, state: PlayState, project_id: str, recipe_id: str, target_id: str
) -> ResourceState:
    fact = next(
        (
            f
            for f in facts(state.resources)
            if f.project_id is None and f.recipe_id == recipe_id and f.target_item_id == target_id
        ),
        None,
    )
    if fact is None:
        return state.resources
    validate(runtime, state, fact)
    return record(
        state.resources,
        PROJECT,
        project_id,
        target_id,
        fact.model_copy(update={"project_id": project_id}),
    )


def context(
    runtime: RulesContext, state: PlayState, project_id: str, *, kind: str
) -> HasteManufacture | None:
    if kind not in {"begin", "advance", "settle"}:
        return None
    fact = next((f for f in facts(state.resources) if f.project_id == project_id), None)
    if fact is not None:
        validate(runtime, state, fact)
    return fact


def bind(state: PlayState, fact: HasteManufacture, binding_id: str, command_id: str) -> PlayState:
    target = next(i for i in state.resources.items if i.id == fact.target_item_id)
    binding = next(b for b in target.enchantments if b.id == binding_id)
    if binding.project_id != fact.project_id or binding.runtime_family is not None:
        raise ValidationError("Haste manufacture requires its freshly settled project binding")
    target = target.model_copy(
        update={
            "enchantments": tuple(
                b.model_copy(update={"runtime_family": "spell"}) if b.id == binding_id else b
                for b in target.enchantments
            )
        }
    )
    resources = state.resources.model_copy(
        update={"items": tuple(target if i.id == target.id else i for i in state.resources.items)}
    )
    resources = record(
        resources,
        ITEM,
        command_id,
        target.id,
        HasteItem(
            item_id=target.id,
            binding_id=binding_id,
            definition_id=target.definition_id,
            levels=fact.levels,
            form="clothing",
        ),
    )
    return state.model_copy(update={"resources": resources})


def capture_if(
    enabled: bool,
    runtime: RulesContext,
    state: PlayState,
    project_id: str,
    recipe_id: str,
    target_id: str,
) -> ResourceState:
    return capture(runtime, state, project_id, recipe_id, target_id) if enabled else state.resources


def complete(
    state: PlayState, project_id: str, binding_id: str | None, status: str, command_id: str
) -> PlayState:
    if status != "completed" or binding_id is None:
        return state
    fact = next((f for f in facts(state.resources) if f.project_id == project_id), None)
    return state if fact is None else bind(state, fact, binding_id, command_id)
