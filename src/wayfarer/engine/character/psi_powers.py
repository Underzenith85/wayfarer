"""Approved-build projection for Basic Set psionic power groupings."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.psi_powers import BINDING_BY_ID, BINDINGS, PROFILE
from wayfarer.engine.rules.traits import TraitRules
from wayfarer.errors import ValidationError
from wayfarer.models import Record

PsiVariant = Literal[
    "standard",
    "para-radar",
    "psionic-detect",
    "psionics",
    "disease-poison",
    "noxious-physical",
    "force-field",
    "air",
    "water",
    "telesend",
    "malediction-fatigue",
    "malediction-stunning",
    "malediction-incapacitation",
    "malediction-mental-disadvantage",
    "malediction-dx",
    "malediction-iq",
    "malediction-will",
]


class PsiAllocation(Record):
    power_id: str
    ability_id: str
    levels: int = Field(default=1, ge=1)
    power_modifier: int = Field(ge=-80, le=1000)
    variant: PsiVariant = "standard"


class PsiLoadout(Record):
    build_revision: str
    allocations: tuple[PsiAllocation, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> PsiLoadout:
        keys = tuple((value.power_id, value.ability_id) for value in self.allocations)
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate psi power allocation")
        return self


class PsiPowers(Record):
    talents: tuple[tuple[str, int], ...] = ()
    allocations: tuple[PsiAllocation, ...] = ()
    purchase_costs: tuple[tuple[str, int, int], ...] = ()

    def talent(self, power_id: str) -> int:
        return dict(self.talents).get(power_id, 0)

    def abilities(self, power_id: str) -> tuple[str, ...]:
        return tuple(value.ability_id for value in self.allocations if value.power_id == power_id)

    def has_power(self, power_id: str) -> bool:
        return self.talent(power_id) > 0 or bool(self.abilities(power_id))

    def can_learn(self, power_id: str, *, gm_permission: bool = False) -> bool:
        return power_id in BINDING_BY_ID and (self.has_power(power_id) or gm_permission)

    def activation_bonus(self, power_id: str, ability_id: str) -> int:
        if ability_id not in self.abilities(power_id):
            raise ValidationError("Ability is not allocated to that psi power")
        return self.talent(power_id)

    def adjusted_cost(self, power_id: str, ability_id: str) -> int:
        allocation = next(
            (
                value
                for value in self.allocations
                if value.power_id == power_id and value.ability_id == ability_id
            ),
            None,
        )
        if allocation is None:
            raise ValidationError("Ability is not allocated to that psi power")
        cost, purchased_levels = next(
            (cost, levels)
            for identifier, cost, levels in self.purchase_costs
            if identifier == ability_id
        )
        base = int(
            (Decimal(cost * allocation.levels) / Decimal(purchased_levels)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        return int(
            (
                Decimal(base) * Decimal(100 + allocation.power_modifier) / Decimal(100)
            ).to_integral_value(rounding=ROUND_CEILING)
        )

    def point_credit(self) -> int:
        """Return the conservative build-cost credit from approved power modifiers."""
        credits = 0
        for allocation in self.allocations:
            cost, purchased_levels = next(
                (cost, levels)
                for identifier, cost, levels in self.purchase_costs
                if identifier == allocation.ability_id
            )
            base = int(
                (Decimal(cost * allocation.levels) / Decimal(purchased_levels)).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            credits += base - self.adjusted_cost(allocation.power_id, allocation.ability_id)
        return credits


NO_PSI_POWERS = PsiPowers()


def _required_variants(power_id: str, ability_id: str) -> frozenset[PsiVariant]:
    requirements: dict[tuple[str, str], frozenset[PsiVariant]] = {
        ("power:antipsi", "advantage:obscure"): frozenset({"para-radar", "psionic-detect"}),
        ("power:antipsi", "advantage:resistant"): frozenset({"psionics"}),
        ("power:esp", "advantage:detect"): frozenset({"psionic-detect"}),
        ("power:esp", "advantage:scanning-sense"): frozenset({"para-radar"}),
        ("power:psychic-healing", "advantage:detect"): frozenset({"disease-poison"}),
        ("power:psychic-healing", "advantage:resistant"): frozenset({"noxious-physical"}),
        ("power:psychokinesis", "advantage:damage-resistance"): frozenset({"force-field"}),
        ("power:psychokinesis", "advantage:enhanced-move"): frozenset({"air", "water"}),
        ("power:telepathy", "advantage:telecommunication"): frozenset({"telesend"}),
        ("power:telepathy", "advantage:affliction"): frozenset(
            {
                "malediction-fatigue",
                "malediction-stunning",
                "malediction-incapacitation",
                "malediction-mental-disadvantage",
                "malediction-dx",
                "malediction-iq",
                "malediction-will",
            }
        ),
        ("power:telepathy", "advantage:innate-attack"): frozenset(
            {
                "malediction-fatigue",
                "malediction-stunning",
                "malediction-incapacitation",
                "malediction-mental-disadvantage",
                "malediction-dx",
                "malediction-iq",
                "malediction-will",
            }
        ),
    }
    return requirements.get((power_id, ability_id), frozenset({"standard"}))


def psi_powers(
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    loadout: PsiLoadout,
) -> PsiPowers:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_PSI_POWERS
    if loadout.build_revision != build.revision:
        raise ValidationError("Psi loadout does not match the approved build")
    purchases = {value.definition_id: value for value in build.purchases}
    talents: list[tuple[str, int]] = []
    for binding in BINDINGS:
        if binding.talent_id is None:
            continue
        purchase = purchases.get(binding.talent_id)
        definition = definitions.get(binding.talent_id)
        if purchase is None:
            continue
        if (
            definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.point_cost != 5
            or definition.trait_rules
            != TraitRules(PROFILE, maximum_level=4, runtime_hooks=(binding.hook,))
        ):
            raise ValidationError("Psi Talent differs from its pinned power binding")
        talents.append((binding.id, purchase.amount))
    allocated_levels: dict[str, int] = {}
    for allocation in loadout.allocations:
        allocated_binding = BINDING_BY_ID.get(allocation.power_id)
        purchase = purchases.get(allocation.ability_id)
        if (
            allocated_binding is None
            or purchase is None
            or allocation.ability_id not in allocated_binding.members
            or allocation.levels > purchase.amount
            or allocation.power_modifier != allocated_binding.power_modifier
            or allocation.variant
            not in _required_variants(allocation.power_id, allocation.ability_id)
        ):
            raise ValidationError("Psi allocation differs from its pinned power binding")
        allocated_levels[allocation.ability_id] = (
            allocated_levels.get(allocation.ability_id, 0) + allocation.levels
        )
        if allocated_levels[allocation.ability_id] > purchase.amount:
            raise ValidationError("Psi allocations exceed purchased ability levels")
    costs = tuple(
        (identifier, purchases[identifier].cost, purchases[identifier].amount)
        for identifier in sorted(allocated_levels)
    )
    return PsiPowers(
        talents=tuple(talents),
        allocations=loadout.allocations,
        purchase_costs=costs,
    )
