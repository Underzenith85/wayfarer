"""Approved-build projection for Basic Set physiology."""

from collections.abc import Mapping
from fractions import Fraction

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.traits.physiology import BINDING_BY_ID, PROFILE, metadata
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PurchasedPhysiology(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)


class PhysiologyTraits(Record):
    entries: tuple[PurchasedPhysiology, ...] = ()

    def purchase(self, identifier: str) -> PurchasedPhysiology | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        selected = self.purchase(identifier)
        return 0 if selected is None else selected.levels

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        selected = self.purchase(identifier)
        return None if selected is None else dict(selected.parameters).get(name)

    def breath_multiplier(self) -> int | None:
        purchase = self.purchase("advantage:doesnt-breathe")
        return (
            None
            if purchase is not None and not purchase.modifiers
            else 1 << self.level("advantage:breath-holding")
        )

    def lifespan_multiplier(self) -> Fraction | None:
        if self.has("advantage:unaging"):
            return None
        return Fraction(
            1 << self.level("advantage:extended-lifespan"),
            1 << self.level("disadvantage:short-lifespan"),
        )

    def radiation_divisor(self) -> int:
        value = self.parameter("advantage:radiation-tolerance", "divisor")
        return 1 if value is None else int(value)

    def regeneration_interval(self) -> int | None:
        rate = self.parameter("advantage:regeneration", "rate")
        if rate is not None and not isinstance(rate, str):
            raise ValidationError("Invalid regeneration rate")
        intervals = {"slow": 43200, "regular": 3600, "fast": 60, "very-fast": 1, "extreme": 1}
        if rate is None:
            return None
        if rate not in intervals:
            raise ValidationError("Unsupported regeneration rate")
        return intervals[rate]

    def recovery_interval_multiplier(self) -> int:
        level = self.level("trait:disadvantage:slow-healing")
        if level > 3:
            raise ValidationError("Slow Healing exceeds its source maximum of three levels")
        return (1, 2, 4, 8)[level]

    def regeneration_amount(self) -> int:
        rate = self.parameter("advantage:regeneration", "rate")
        self.regeneration_interval()  # Validate the approved variant before projecting HP.
        return 10 if rate == "extreme" else 1

    def survival_requirements(self) -> frozenset[str]:
        requirements = {"air", "food", "water", "sleep"}
        breathing = self.purchase("advantage:doesnt-breathe")
        if breathing is not None and not breathing.modifiers:
            requirements.remove("air")
        purchase = self.purchase("advantage:doesnt-eat-or-drink")
        if purchase is not None:
            if "drink-only" not in purchase.modifiers:
                requirements.discard("food")
            if "food-only" not in purchase.modifiers:
                requirements.discard("water")
        if self.has("advantage:doesnt-sleep"):
            requirements.remove("sleep")
        return frozenset(requirements)

    def consumption_fraction(self, resource: str) -> Fraction:
        purchase = self.purchase("advantage:reduced-consumption")
        if (
            purchase is None
            or (resource == "water" and "food-only" in purchase.modifiers)
            or (resource == "food" and "water-only" in purchase.modifiers)
        ):
            return Fraction(1)
        return (Fraction(1), Fraction(2, 3), Fraction(1, 3), Fraction(1, 20), Fraction(1, 100))[
            purchase.levels
        ]

    def consumption_period(self, resource: str) -> int:
        fraction = self.consumption_fraction(resource)
        if resource == "food":
            return {
                Fraction(1): 28800,
                Fraction(2, 3): 43200,
                Fraction(1, 3): 86400,
                Fraction(1, 20): 604800,
                Fraction(1, 100): 2592000,
            }[fraction]
        return {Fraction(1, 20): 604800, Fraction(1, 100): 2592000}.get(fraction, 86400)

    def environmental_protection(self, hazard: str) -> int:
        if hazard not in {"contaminant", "pressure", "temperature", "vacuum"}:
            raise ValidationError("Unknown physiology hazard")
        return {
            "contaminant": 10 * int(self.has("advantage:filter-lungs"))
            + 10 * int(self.has("advantage:sealed")),
            "pressure": 10 * self.level("advantage:pressure-support"),
            "temperature": self.level("advantage:temperature-control")
            + int(self.has("advantage:fur")),
            "vacuum": 10 * int(self.has("advantage:vacuum-support")),
        }[hazard]


NO_PHYSIOLOGY_TRAITS = PhysiologyTraits()


def physiology_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> PhysiologyTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_PHYSIOLOGY_TRAITS
    entries: list[PurchasedPhysiology] = []
    for purchase in build.trait_purchases:
        binding, definition = (
            BINDING_BY_ID.get(purchase.definition_id),
            definitions.get(purchase.definition_id),
        )
        if (
            binding is None
            or definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules != metadata(binding)
            or binding.hook not in definition.trait_rules.runtime_hooks
        ):
            continue
        entries.append(
            PurchasedPhysiology(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    entries.extend(
        PurchasedPhysiology(definition_id=effect.definition_id, levels=effect.levels)
        for effect in mundane_trait_effects(build, definitions)
        if effect.definition_id == "trait:disadvantage:slow-healing"
    )
    return PhysiologyTraits(entries=tuple(entries))
