"""Compile the physical runtime projection from exact pinned trait definitions."""

from collections.abc import Mapping

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.rules.physical_traits import PHYSICAL_BINDINGS, PhysicalTraits


def physical_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> PhysicalTraits:
    values: dict[str, int | bool] = {}
    for purchase in build.trait_purchases:
        binding = PHYSICAL_BINDINGS.get(purchase.definition_id)
        definition = definitions.get(purchase.definition_id)
        if binding is None or definition is None:
            continue
        hook, field, value = binding
        if (
            definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules is None
            or definition.trait_rules.profile_id != "gurps-basic-set-4e-2004"
            or hook not in definition.trait_rules.runtime_hooks
        ):
            continue
        values[field] = value if isinstance(value, bool) else value * purchase.amount
    return PhysicalTraits.model_validate(values)
