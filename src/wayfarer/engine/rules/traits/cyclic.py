"""Bounded Cyclic construction and runtime projection, Characters third printing B103-104."""

from decimal import ROUND_CEILING, Decimal

from wayfarer.engine.rules.traits.modifiers import (
    AttackProfile,
    ModifierApproval,
    ModifierSelection,
    apply_attack_modifiers,
    validate_selections,
)
from wayfarer.engine.rules.traits.symptoms import SYMPTOMS, symptom_percentage, symptom_spec
from wayfarer.errors import ValidationError

CYCLIC = "modifier:enhancement:cyclic"
RESISTIBLE = "modifier:limitation:resistible"
KINDS = {
    "burn": "burning",
    "cor": "corrosion",
    "fat": "fatigue",
    "tox": "toxic",
    "cr": "crushing",
    "cut": "cutting",
    "imp": "impaling",
    "pi-": "piercing",
    "pi": "piercing",
    "pi+": "piercing",
    "pi++": "piercing",
}


def approvals(selections: tuple[ModifierSelection, ...]) -> tuple[ModifierApproval, ...]:
    if {s.definition_id for s in selections} - {CYCLIC, RESISTIBLE, SYMPTOMS}:
        raise ValidationError("Cyclic execution supports only Cyclic and Resistible modifiers")
    cyclic = next((s for s in selections if s.definition_id == CYCLIC), None)
    symptoms = next((s for s in selections if s.definition_id == SYMPTOMS), None)
    extra = (
        ()
        if symptoms is None
        else (
            ModifierApproval(
                SYMPTOMS,
                symptoms.option or "",
                symptom_percentage(symptom_spec(symptoms)),
                frozenset({"innate-attack"}),
            ),
        )
    )
    if cyclic is None:
        if symptoms is None or any(s.definition_id == RESISTIBLE for s in selections):
            raise ValidationError("Modifier combination has no supported attack consumer")
        return extra
    if cyclic.parameters is None or cyclic.option is None:
        raise ValidationError(
            "Cyclic requires an approved option and parameterized stopping condition"
        )
    params = cyclic.parameters
    if params.stop_condition is None or params.cycles is None:
        raise ValidationError("Cyclic requires cycles and a named stopping condition")
    percent = {1: 100, 10: 50, 60: 40, 3600: 20, 86400: 10}.get(params.interval_seconds or 0)
    if percent is None:
        raise ValidationError("Unsupported Cyclic interval")
    return (
        ModifierApproval(
            CYCLIC,
            cyclic.option,
            percent * (params.cycles - 1)
            + {"none": 0, "mild": 20, "high": 50}[params.contagious or "none"],
            frozenset({"innate-attack"}),
        ),
    ) + extra


def cyclic_profile(selections: tuple[ModifierSelection, ...], damage_type: str) -> AttackProfile:
    if damage_type not in KINDS:
        raise ValidationError("Cyclic requires burning, corrosion, fatigue or toxic damage")
    kind = KINDS[damage_type]
    if any(
        s.parameters and s.parameters.damage_kind != kind
        for s in selections
        if s.definition_id in {CYCLIC, SYMPTOMS}
    ):
        raise ValidationError("Cyclic damage kind differs from the approved attack")
    profile = AttackProfile.model_validate({"damage_kind": kind})
    result = apply_attack_modifiers(
        profile, "innate-attack", selections, approvals(selections)
    ).modified
    symptoms = next((s for s in selections if s.definition_id == SYMPTOMS), None)
    return result.model_copy(
        update={"symptom_spec": None if symptoms is None else symptom_spec(symptoms)}
    )


def cyclic_cost(base: int, selections: tuple[ModifierSelection, ...]) -> int:
    components = validate_selections("innate-attack", selections, approvals(selections))
    resistible = any(s.definition_id == RESISTIBLE for s in selections)
    contagious = next(
        (s.parameters.contagious for s in selections if s.definition_id == CYCLIC and s.parameters),
        "none",
    )
    extra = {"none": 0, "mild": 20, "high": 50}[contagious or "none"]
    percent = sum(
        (c.percent - extra) // 2 + extra if c.definition_id == CYCLIC and resistible else c.percent
        for c in components
    )
    return int(
        (Decimal(base) * Decimal(100 + max(-80, percent)) / 100).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
