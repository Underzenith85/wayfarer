"""B481 unknown critical enchantment Power, distinct from energy reduction.

No supported Analyze Magic consumer exists yet. Ownership, recipe knowledge,
world facts and ordinary item use do not certify discovery of the exact rating.
"""

from wayfarer.engine.simulation.resources import Item, ResourceState

PREFIX = "enchantment-power:"


def item_projection(state: ResourceState, item: Item) -> dict[str, object]:
    unknown_projects = frozenset(
        event.target_id for event in state.events if event.id.startswith(PREFIX)
    )
    return item.model_dump(
        mode="json",
        exclude={
            "enchantments": {
                index: {"power"}
                for index, binding in enumerate(item.enchantments)
                if binding.project_id in unknown_projects
            }
        },
    )
