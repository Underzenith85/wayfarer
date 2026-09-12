"""Resolve usable combat equipment from its catalog entry and current condition."""

from wayfarer.engine.rules.types.object import residual_definition
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.resources import Item
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def effective_entry(runtime: RulesContext, item: Item) -> EquipmentProfile:

    if item.firearm_failure is not None and item.firearm_failure.kind in (
        "destroyed",
        "explosion",
        "dud",
    ):
        raise ValidationError("Destroyed firearm has no usable weapon mode")
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    entry = entries[item.definition_id]
    residual = residual_definition(entry.durability, item.condition)
    if item.condition and item.condition.disabled and residual is None:
        raise ValidationError("Disabled equipment has no usable weapon mode")
    return entries[residual] if residual else entry
