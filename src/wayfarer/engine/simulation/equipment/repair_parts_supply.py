"""Shared current-stock admission for assessed and historical B485 starts."""

import hashlib
from fractions import Fraction
from math import ceil

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment import repair_parts
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.worksite import available_here
from wayfarer.engine.simulation.resources import Consume, Item, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def major_parts(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    command_id: str,
    preview: bool,
    assessment_only: bool,
) -> tuple[ResourceState, int | None, int]:
    resources = state.resources
    profile = entry.durability
    assert profile is not None and item.condition is not None
    part_entry = next(
        (e for e in catalog(runtime).entries if e.definition_id == profile.repair_parts_definition),
        None,
    )
    supplies = tuple(
        i
        for i in resources.items
        if i.owner_id == actor_id
        and not i.ground
        and available_here(state, actor_id, i)
        and not i.equipped
        and i.definition_id == profile.repair_parts_definition
    )
    if part_entry is None or part_entry.price <= 0 or (not supplies and not assessment_only):
        raise ValidationError("Major repair requires priced, owned spare parts")
    entry_price = Fraction(entry.price)
    part_price = Fraction(part_entry.price)
    assessment = repair_parts.latest(resources, item.id)
    if assessment_only:
        return resources, None, 0
    available_quantity = sum(supply.quantity for supply in supplies)
    if assessment is not None:
        repair_parts.require_current(
            assessment,
            actor_id=actor_id,
            definition_id=item.definition_id,
            condition=item.condition,
            entry=entry,
            part_entry=part_entry,
        )
        parts_die, quantity = assessment.die, assessment.quantity
        if available_quantity < quantity:
            raise ValidationError("Major repair requires the recorded rolled parts quantity")
    else:
        # Historical, unassessed starts retain their exact random stream.
        maximum = ceil(entry_price * 6 / (part_price * 10))
        if available_quantity < maximum:
            raise ValidationError("Major repair requires supplies covering the maximum parts cost")
        parts_die = 6 if preview else draw_dice(runtime.rng, 1)[0]
        quantity = ceil(entry_price * parts_die / (part_price * 10))
    remaining = quantity
    for index, supply in enumerate(supplies):
        if not remaining:
            break
        consumed = min(supply.quantity, remaining)
        resources = runtime.resources.apply(
            resources,
            Consume(
                id="repair-parts:"
                + hashlib.sha256(command_id.encode()).hexdigest()
                + (f":{index}" if index else ""),
                actor_id=actor_id,
                expected_revision=resources.revision,
                item_id=supply.id,
                quantity=consumed,
            ),
        )
        remaining -= consumed
    return resources, parts_die, quantity


def revalidate_assessed_parts(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    item: Item,
    entry: EquipmentProfile,
    parts_die: int | None,
) -> None:
    """A pending assessed repair cannot finish under a changed priced profile."""
    assessment = repair_parts.latest(state.resources, item.id)
    if assessment is None or parts_die is None:
        return
    profile = entry.durability
    assert profile is not None and item.condition is not None
    part_entry = next(
        (e for e in catalog(runtime).entries if e.definition_id == profile.repair_parts_definition),
        None,
    )
    if part_entry is None:
        raise ValidationError("Recorded repair parts profile is unavailable")
    repair_parts.require_current(
        assessment,
        actor_id=actor_id,
        definition_id=item.definition_id,
        condition=item.condition,
        entry=entry,
        part_entry=part_entry,
    )
