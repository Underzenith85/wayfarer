"""Which trait family owns a purchase, and how that family prices it.

A family declares the runtime-hook prefix its traits carry and the check that
prices one purchase. The compiler asks the registry rather than testing every
family in turn, so adding a family is a row here instead of another branch in
the compiler.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.traits import (
    attack_defense,
    mana_divine,
    mental_spirit,
    movement_forms,
    physiology,
    sensory,
    world_travel,
)
from wayfarer.engine.rules.traits.base import TraitOptions


@dataclass(frozen=True, slots=True)
class TraitFamily:
    """One family of traits: what marks a trait as belonging to it, and its cost check."""

    hook_prefix: str
    validate_purchase: Callable[[RuleDefinition, int, TraitOptions], int]


FAMILIES: Final = (
    TraitFamily("movement-form:", movement_forms.validate_purchase),
    TraitFamily("physiology-trait:", physiology.validate_purchase),
    TraitFamily("sensory-trait:", sensory.validate_purchase),
    TraitFamily("mental-spirit-trait:", mental_spirit.validate_purchase),
    TraitFamily("attack-defense-trait:", attack_defense.validate_purchase),
    TraitFamily("world-travel-trait:", world_travel.validate_purchase),
    TraitFamily("mana-divine-trait:", mana_divine.validate_purchase),
)


def family(runtime_hooks: Iterable[str]) -> TraitFamily | None:
    """The family these hooks belong to, or None for a trait priced by the base rules."""
    hooks = tuple(runtime_hooks)
    for declared in FAMILIES:
        if any(hook.startswith(declared.hook_prefix) for hook in hooks):
            return declared
    return None
