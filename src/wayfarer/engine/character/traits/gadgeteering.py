"""Compile the Gadgeteer purchase into an invention capability (Characters B56-57)."""

from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild

GADGETEER_TRAIT_ID = "trait:advantage:gadgeteer"
InventionMethod = Literal["ordinary", "gadgeteer", "quick-gadgeteer"]


@dataclass(frozen=True, slots=True)
class GadgeteeringCapability:
    method: Literal["gadgeteer", "quick-gadgeteer"]


def capability(build: ValidatedBuild) -> GadgeteeringCapability | None:
    """Derive capability only from the approved build; commands cannot assert it."""
    purchase = next(
        (item for item in build.purchases if item.definition_id == GADGETEER_TRAIT_ID), None
    )
    if purchase is None:
        return None
    return GadgeteeringCapability("quick-gadgeteer" if purchase.amount >= 2 else "gadgeteer")


def permits(build: ValidatedBuild, method: InventionMethod) -> bool:
    if method == "ordinary":
        return True
    selected = capability(build)
    return selected is not None and (selected.method == "quick-gadgeteer" or method == "gadgeteer")
