"""Read the catalog-derived projection validated with each play checkpoint."""

from wayfarer.engine.rules.physical_traits import NO_PHYSICAL_TRAITS, PhysicalTraits
from wayfarer.engine.simulation.resources import ResourceState


def physical_traits(state: ResourceState, actor_id: str) -> PhysicalTraits:
    hp = next((p for p in state.pools if p.id == f"hp:{actor_id}"), None)
    return (
        hp.injury.physical_traits
        if hp is not None and hp.injury is not None
        else NO_PHYSICAL_TRAITS
    )
