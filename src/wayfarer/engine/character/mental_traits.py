"""Compile selected mental traits from an approved, exactly pinned build."""

from collections.abc import Mapping
from typing import cast

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.mental_traits import MENTAL_BINDINGS, MentalTraits


def mental_traits(build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]) -> MentalTraits:
    values: dict[str, str | bool] = {}
    for purchase in build.trait_purchases:
        binding = MENTAL_BINDINGS.get(purchase.definition_id)
        definition = definitions.get(purchase.definition_id)
        if binding is None or definition is None or definition.trait_rules is None:
            continue
        hook, field, value = binding
        if (
            definition.status is ImplementationStatus.IMPLEMENTED
            and definition.trait_rules.profile_id == "gurps-basic-set-4e-2004"
            and hook in definition.trait_rules.runtime_hooks
        ):
            values[field] = cast(str | bool, value)
    return MentalTraits.model_validate(values)
