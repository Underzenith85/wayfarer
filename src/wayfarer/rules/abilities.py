"""Representative Basic Set ability runtime bindings, not full catalog support.

Numeric references: Characters third printing, B46, B48, B61, B69-70,
B106, B111, B115 and B257; Campaigns fourth printing B550. The historic
selected-printing source coverage remains uncertified. No package pin changes.
"""

from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.ability_types import AbilitySpec
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.traits import TraitModifier, TraitOptions, TraitRules, cost

PROFILE: Final = "gurps-basic-set-4e-2004"
MODIFIERS = (
    TraitModifier(
        "costs-fatigue-1", -5, exclusions=("costs-fatigue-2",), runtime_hook="ability:fatigue"
    ),
    TraitModifier(
        "costs-fatigue-2", -10, exclusions=("costs-fatigue-1",), runtime_hook="ability:fatigue"
    ),
    TraitModifier("malediction-1", 100, runtime_hook="ability:resistance"),
    TraitModifier("telepathic", -10, runtime_hook="ability:psi-suppression"),
    TraitModifier("precise", 100, exclusions=("vague",), runtime_hook="ability:precise"),
    TraitModifier("vague", -50, exclusions=("precise",), runtime_hook="ability:vague"),
)
KINDS = {
    "burning-malediction": (5, 100, {"malediction-1", "costs-fatigue-1", "costs-fatigue-2"}),
    "damage-resistance": (5, 100, {"costs-fatigue-1", "costs-fatigue-2"}),
    "detect": (5, 1, {"vague", "precise", "costs-fatigue-1", "costs-fatigue-2"}),
    "mind-reading": (30, 1, {"telepathic", "costs-fatigue-1", "costs-fatigue-2"}),
}


def metadata(spec: AbilitySpec) -> TraitRules:
    _, maximum, allowed = KINDS[spec.kind]
    return TraitRules(
        PROFILE,
        maximum_level=maximum,
        modifiers=tuple(m for m in MODIFIERS if m.id in allowed),
        runtime_hooks=("ability:" + spec.kind,),
    )


def validate_binding(spec: AbilitySpec, level: int, options: TraitOptions) -> int:
    """Cost validation and executable combinations are deliberately inseparable."""
    if set(options.modifiers) != set(spec.modifiers):
        raise ValidationError("Ability options differ from pinned runtime binding")
    selected = set(spec.modifiers)
    if {"costs-fatigue-1", "costs-fatigue-2"} <= selected:
        raise ValidationError("Duplicate fatigue limitation")
    if spec.kind == "burning-malediction" and "malediction-1" not in selected:
        raise ValidationError("Conventional innate attacks require ranged combat support")
    if spec.kind == "damage-resistance" and not fatigue_cost(spec):
        raise ValidationError("This activation binding requires Costs Fatigue DR")
    return cost(KINDS[spec.kind][0], level, options, metadata(spec))


def fatigue_cost(spec: AbilitySpec) -> int:
    return 2 if "costs-fatigue-2" in spec.modifiers else int("costs-fatigue-1" in spec.modifiers)


def definition(spec: AbilitySpec) -> RuleDefinition:
    """Explicit opt-in catalog entry; callers must pin a new package.

    Detect's representative category is rare (5 points), bound to authored
    channels. Broader categories need separate catalog entries and audit.
    """
    return RuleDefinition(
        spec.definition_id,
        DefinitionKind.TRAIT,
        spec.kind,
        "sjg:basic-set-characters-4e-2004",
        KINDS[spec.kind][0],
        ImplementationStatus.IMPLEMENTED,
        hooks=("supernatural",),
        trait_rules=metadata(spec),
    )


def validate_purchase(entry: RuleDefinition, level: int, options: TraitOptions) -> None:
    """Compiler gate: a discounted construction must have executable semantics."""
    if entry.trait_rules is None:
        raise ValidationError("Ability construction metadata required")
    kinds = [kind for kind in KINDS if "ability:" + kind in entry.trait_rules.runtime_hooks]
    if len(kinds) != 1:
        raise ValidationError("Unknown or ambiguous ability runtime")
    selected = AbilitySpec.model_validate(
        {"definition_id": entry.id, "kind": kinds[0], "modifiers": options.modifiers}
    )
    if entry.trait_rules != metadata(selected):
        raise ValidationError("Ability construction metadata differs from runtime")
    validate_binding(selected, level, options)
