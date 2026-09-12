"""Query combat equipment catalog entries and current usability."""

from wayfarer.engine.rules.types.object import residual_definition
from wayfarer.engine.simulation.actions import PlayState
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


def weapon_target(runtime: RulesContext, state: PlayState, item_id: str | None) -> bool:
    """B401 restricts defenses for weapon targets, including ranged weapons."""
    if item_id is None:
        return False

    item = next(i for i in state.resources.items if i.id == item_id)
    return any(e.definition_id == item.definition_id and e.modes for e in catalog(runtime).entries)
