"""Server-derived social effects of approved mundane traits.

A player command, template or scenario resolver never supplies a trait reaction
modifier. Values come from the pinned definition and the approved build, so a
trait that is not purchased, not implemented, or not bound to a runtime hook
contributes nothing and cannot reach a roll.
"""

from collections.abc import Mapping

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.rules.gurps_social import ReactionModifier
from wayfarer.rules.mundane_traits.runtime import (
    DEFAULT_AUDIENCE,
    REACTION_BINDINGS,
    Audience,
    Check,
)


def reaction_modifiers(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    check: Check,
    audience: Audience = DEFAULT_AUDIENCE,
) -> tuple[ReactionModifier, ...]:
    """Reaction/influence modifiers the approved purchases of this build imply.

    The definition pinned by the campaign decides: another package that reuses
    an identifier without the implemented status and runtime hook contributes
    nothing. Each modifier carries its definition ID as provenance.
    """
    modifiers = []
    for purchase in sorted(build.trait_purchases, key=lambda p: p.definition_id):
        binding = REACTION_BINDINGS.get(purchase.definition_id)
        definition = definitions.get(purchase.definition_id)
        if binding is None or definition is None or definition.trait_rules is None:
            continue
        if definition.status is not ImplementationStatus.IMPLEMENTED:
            continue
        if binding.hook not in definition.trait_rules.runtime_hooks:
            continue
        if not 1 <= purchase.amount <= definition.trait_rules.maximum_level:
            raise ValidationError("Approved trait level is outside its catalog bounds")
        if not binding.applies(check, audience):
            continue
        modifiers.append(
            ReactionModifier("trait", binding.per_level * purchase.amount, purchase.definition_id)
        )
    return tuple(modifiers)
