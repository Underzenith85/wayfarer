"""Audience-only B481 rating privacy; canonical spell results remain intact."""

from wayfarer.engine.simulation.magic.item_power_knowledge import PREFIX
from wayfarer.engine.simulation.magic.item_state import item_magic_lost
from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.resources import ResourceState


def item_result(
    resources: ResourceState,
    result: SpellResult,
    *,
    item_id: str | None,
    spell_id: str,
    binding_id: str | None = None,
) -> SpellResult:
    """Use accepted pre-command selection, never current ownership as discovery."""
    item = next((item for item in resources.items if item.id == item_id), None)
    if item is None:
        return result
    binding = next(
        (
            binding
            for binding in item.enchantments
            if binding.spell_id == spell_id
            and (binding_id is None or binding.id == binding_id)
            and not item_magic_lost(resources, item.id, binding.id)
        ),
        None,
    )
    if binding is None:
        return result
    unknown = any(
        event.id.startswith(PREFIX) and event.target_id == binding.project_id
        for event in resources.events
    )
    return result.model_copy(update={"checks": ()}) if unknown else result
