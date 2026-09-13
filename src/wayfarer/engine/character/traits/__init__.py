"""Trait construction: what a purchase costs and what it declares."""

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane.complete import MundaneTraitEffect
from wayfarer.engine.rules.traits.mundane.complete import effects as project_effects


def mundane_trait_effects(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> tuple[MundaneTraitEffect, ...]:
    """Derive typed, replay-stable family effects from an approved build."""

    purchases = []
    for purchase in build.trait_purchases:
        definition = definitions.get(purchase.definition_id)
        if (
            definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules is None
            or definition.trait_rules.profile_id != "gurps-basic-set-4e-2004"
            or not any(
                hook.startswith("mundane-trait:") for hook in definition.trait_rules.runtime_hooks
            )
        ):
            continue
        purchases.append(
            (purchase.definition_id, purchase.amount, purchase.trait or TraitOptions())
        )
    return project_effects(tuple(purchases))
