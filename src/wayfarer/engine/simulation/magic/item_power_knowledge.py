"""B481 observer reports of critical item Power, separate from canonical truth."""

from wayfarer.engine.simulation.magic.analyze_magic_state import apparent_power
from wayfarer.engine.simulation.resources import Item, ResourceState

PREFIX = "enchantment-power:"


def item_projection(
    state: ResourceState, item: Item, actor_ids: tuple[str, ...] = ()
) -> dict[str, object]:
    unknown_projects = frozenset(
        event.target_id for event in state.events if event.id.startswith(PREFIX)
    )
    projected: list[dict[str, object]] = []
    for binding in item.enchantments:
        if binding.project_id not in unknown_projects:
            projected.append(binding.model_dump(mode="json"))
            continue
        value = binding.model_dump(mode="json", exclude={"power"})
        reported = next(
            (
                power
                for actor_id in actor_ids
                if (power := apparent_power(state, actor_id, binding)) is not None
            ),
            None,
        )
        if reported is not None:
            value["power"] = reported
        projected.append(value)
    result = item.model_dump(mode="json")
    if "enchantments" in result or projected:
        result["enchantments"] = projected
    return result
