"""Bind B96 admission to an explicit new campaign package, never player prices."""

from dataclasses import replace

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RulesPackage
from wayfarer.engine.rules.types.background_admission import (
    BACKGROUND_ADMISSION_HOOK,
    UNUSUAL_BACKGROUND_ID,
    UnusualBackgroundDecision,
)
from wayfarer.errors import ValidationError


def bind_unusual_background(
    package: RulesPackage,
    decision: UnusualBackgroundDecision,
    *,
    package_id: str,
    version: str,
) -> RulesPackage:
    """Trusted host construction; choosing the returned pin is an explicit migration.

    One decision may admit several catalog traits for one fixed surcharge.
    Multiple backgrounds, conditional prices and new abilities are not inferred.
    """
    decision = UnusualBackgroundDecision.model_validate(decision)
    if (
        not package_id
        or not version
        or (package_id, version) == (package.id, package.version)
        or package.unusual_background is not None
    ):
        raise ValidationError("Bind a fresh baseline to an explicit new package identity/version")
    definitions = {definition.id: definition for definition in package.definitions}
    background = definitions.get(UNUSUAL_BACKGROUND_ID)
    if background is None or background.trait_rules is None:
        raise ValidationError("Campaign package lacks the canonical Unusual Background row")
    if any(UNUSUAL_BACKGROUND_ID in row.prerequisites for row in package.definitions):
        raise ValidationError("A fresh baseline cannot contain unbound background prerequisites")
    if any(
        identity not in definitions
        or definitions[identity].kind is not DefinitionKind.TRAIT
        or definitions[identity].status is not ImplementationStatus.IMPLEMENTED
        or UNUSUAL_BACKGROUND_ID in definitions[identity].prerequisites
        for identity in decision.benefits
    ):
        raise ValidationError("Background benefits require existing implemented trait rows")
    paid = decision.allowed and decision.point_cost > 0
    definitions[UNUSUAL_BACKGROUND_ID] = replace(
        background,
        point_cost=decision.point_cost,
        status=ImplementationStatus.IMPLEMENTED if paid else ImplementationStatus.UNSUPPORTED,
        parameters=(),
        hooks=background.hooks + (BACKGROUND_ADMISSION_HOOK,),
        trait_rules=replace(background.trait_rules, parameters=(), maximum_level=1),
    )
    for identity in decision.benefits:
        definition = definitions[identity]
        definitions[identity] = replace(
            definition,
            prerequisites=definition.prerequisites + ((UNUSUAL_BACKGROUND_ID,) if paid else ()),
            status=definition.status if decision.allowed else ImplementationStatus.UNSUPPORTED,
        )
    return replace(
        package,
        id=package_id,
        version=version,
        definitions=tuple(definitions[definition.id] for definition in package.definitions),
        unusual_background=decision,
    )
