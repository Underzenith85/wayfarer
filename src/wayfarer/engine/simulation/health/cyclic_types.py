"""Trusted current-target facts for Cyclic deadlines; no state transitions."""

from collections.abc import Callable
from dataclasses import dataclass

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.rules.types.cyclic import CyclicAttack
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.resources import ResourceState


@dataclass(frozen=True)
class CyclicTargetContext:
    ht: int
    resistance: int
    vulnerability_multiplier: int
    immune_to_damage: bool = False
    resistance_modifiers: tuple[Modifier, ...] = ()
    held_item_ids: tuple[str, ...] = ()
    held_item_locations: tuple[tuple[str, HumanLocation], ...] = ()
    shield_item_ids: tuple[str, ...] = ()
    dx: int | None = None


CyclicContextResolver = Callable[[ResourceState, CyclicAttack, str], CyclicTargetContext]
