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
from wayfarer.rules.mundane_skills.social import VOICE, procedure
from wayfarer.rules.mundane_traits.runtime import (
    APPEARANCE_BINDINGS,
    DEFAULT_AUDIENCE,
    REACTION_BINDINGS,
    REPUTATION_BINDINGS,
    Audience,
    Check,
)
from wayfarer.rules.social_hooks import Reputation, Standing, validate_standing


def bind_standing(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    authored: Standing | None,
    modifiers: tuple[ReactionModifier, ...] = (),
) -> Standing | None:
    """Merge purchased standing without letting authored values duplicate it.

    Legacy actors without these purchases retain authored standing. A purchased
    appearance or reputation owns that entire source category; conflicting
    declarations reject instead of silently replacing or stacking modifiers.
    Recognition and reaction resolution remain in the existing social reducer.
    """
    appearance = None
    reputations = []
    for purchase in sorted(build.trait_purchases, key=lambda p: p.definition_id):
        identifier = purchase.definition_id
        if identifier in APPEARANCE_BINDINGS:
            hook = "trait.appearance"
        elif identifier in REPUTATION_BINDINGS:
            hook = "trait.reputation"
        else:
            continue
        definition = definitions.get(identifier)
        if (
            definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules is None
            or hook not in definition.trait_rules.runtime_hooks
        ):
            continue
        if not 1 <= purchase.amount <= definition.trait_rules.maximum_level:
            raise ValidationError("Approved trait level is outside its catalog bounds")
        if identifier in APPEARANCE_BINDINGS:
            if appearance is not None:
                raise ValidationError("Approved build has conflicting appearance traits")
            appearance = APPEARANCE_BINDINGS[identifier]
        else:
            reputations.append(
                Reputation(identifier, REPUTATION_BINDINGS[identifier] * purchase.amount)
            )
    if appearance is None and not reputations:
        return authored
    standing = validate_standing(authored or Standing())
    if appearance is not None and (
        standing.appearance != "average" or any(m.kind == "appearance" for m in modifiers)
    ):
        raise ValidationError("Purchased appearance cannot also be supplied by the resolver")
    if reputations and (standing.reputations or any(m.kind == "reputation" for m in modifiers)):
        raise ValidationError("Purchased reputation cannot also be supplied by the resolver")
    return Standing(
        appearance if appearance is not None else standing.appearance,
        tuple(reputations) if reputations else standing.reputations,
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


def skill_conditions(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    procedure_id: str,
    audience: Audience = DEFAULT_AUDIENCE,
) -> frozenset[str]:
    """Named conditions the initiator's approved build asserts for a social procedure.

    The build decides only whether a condition holds; the integer it is worth
    belongs to the procedure (B97 Voice, `rules.mundane_skills.social`). A trait
    that is not purchased, not implemented, not bound to its runtime hook, or not
    perceptible to this audience asserts nothing, and a procedure that declares no
    such modifier never receives the condition.
    """
    entry = procedure(procedure_id)
    declared = {modifier.condition for modifier in entry.modifiers}
    if VOICE.condition not in declared:
        return frozenset()
    binding = REACTION_BINDINGS["trait:voice"]
    purchase = next(
        (p for p in build.trait_purchases if p.definition_id == "trait:voice"),
        None,
    )
    definition = definitions.get("trait:voice")
    if purchase is None or definition is None or definition.trait_rules is None:
        return frozenset()
    if definition.status is not ImplementationStatus.IMPLEMENTED:
        return frozenset()
    if binding.hook not in definition.trait_rules.runtime_hooks:
        return frozenset()
    if not 1 <= purchase.amount <= definition.trait_rules.maximum_level:
        raise ValidationError("Approved trait level is outside its catalog bounds")
    return frozenset({VOICE.condition}) if audience.audible else frozenset()
