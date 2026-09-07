"""Trusted trait construction metadata; no executable model-supplied formulas.

Cost baseline: Basic Set: Characters, 4e first printing (2004), B120-121,
B101-102, with errata through 2007-01-26. Runtime execution is separately gated.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import profile


class TraitOptions(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    parameters: tuple[Annotated[tuple[str, str | int | bool], Field(strict=False)], ...] = Field(
        default=(), strict=False
    )
    self_control: Literal[6, 9, 12, 15] | None = None
    modifiers: tuple[str, ...] = Field(default=(), strict=False, max_length=100)


@dataclass(frozen=True, slots=True)
class TraitParameter:
    name: str
    kind: Literal["text", "integer", "boolean"]
    choices: tuple[str | int | bool, ...]


@dataclass(frozen=True, slots=True)
class TraitModifier:
    id: str
    percent: int
    exclusions: tuple[str, ...] = ()
    runtime_hook: str | None = None


@dataclass(frozen=True, slots=True)
class TraitRules:
    profile_id: str
    maximum_level: int = 1
    self_control: bool = False
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()
    runtime_hooks: tuple[str, ...] = ()


def validate_metadata(rules: TraitRules) -> None:
    selected = profile(rules.profile_id)
    if type(rules.maximum_level) is not int or not 1 <= rules.maximum_level <= 10000:
        raise ValidationError("Invalid trait level bound")
    names = {p.name for p in rules.parameters}
    identifiers = {m.id for m in rules.modifiers}
    if len(names) != len(rules.parameters) or len(identifiers) != len(rules.modifiers):
        raise ValidationError("Duplicate trait metadata identifier")
    if rules.modifiers and selected.id != "gurps-basic-set-4e-2004":
        raise ValidationError("Ability modifiers require the Basic Set profile")
    kinds = {"text": str, "integer": int, "boolean": bool}
    for parameter in rules.parameters:
        if (
            not parameter.name
            or parameter.kind not in kinds
            or not parameter.choices
            or any(type(v) is not kinds[parameter.kind] for v in parameter.choices)
        ):
            raise ValidationError("Invalid trait parameter catalog")
    for modifier in rules.modifiers:
        if (
            not modifier.id
            or type(modifier.percent) is not int
            or not set(modifier.exclusions) <= identifiers
        ):
            raise ValidationError("Invalid trait modifier catalog")


def cost(base: int, levels: int, options: TraitOptions, rules: TraitRules) -> int:
    """Multiply levels and self-control, sum modifiers, cap discount, round once.

    Positive infinity rounding also applies to negative point totals (B121).
    Modifier IDs select trusted percentages, never client arithmetic.
    """
    validate_metadata(rules)
    if type(base) is not int or not 1 <= levels <= rules.maximum_level:
        raise ValidationError("Trait level is outside catalog bounds")
    if rules.self_control != (options.self_control is not None):
        raise ValidationError("Self-control value required or not applicable")
    if rules.self_control and base >= 0:
        raise ValidationError("Self-control requires a disadvantage")
    supplied = dict(options.parameters)
    if len(supplied) != len(options.parameters) or set(supplied) != {
        p.name for p in rules.parameters
    }:
        raise ValidationError("Trait parameters do not match catalog")
    kinds = {"text": str, "integer": int, "boolean": bool}
    for parameter in rules.parameters:
        value = supplied[parameter.name]
        if type(value) is not kinds[parameter.kind] or value not in parameter.choices:
            raise ValidationError("Invalid typed trait parameter")
    modifiers = {m.id: m for m in rules.modifiers}
    selected = set(options.modifiers)
    if len(selected) != len(options.modifiers) or not selected <= modifiers.keys():
        raise ValidationError("Unknown or duplicate trait modifier")
    if selected and rules.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Ability modifiers require the Basic Set profile")
    if selected and base <= 0:
        raise ValidationError("Disadvantage modifiers are unavailable")
    if any(selected.intersection(modifiers[key].exclusions) for key in selected):
        raise ValidationError("Mutually exclusive trait modifiers")
    multiplier = {6: Decimal(2), 9: Decimal("1.5"), 12: Decimal(1), 15: Decimal("0.5")}
    total = Decimal(base * levels) * (
        Decimal(1) if options.self_control is None else multiplier[options.self_control]
    )
    percent = max(-80, sum(modifiers[key].percent for key in selected))
    return int((total * Decimal(100 + percent) / 100).to_integral_value(rounding=ROUND_CEILING))
