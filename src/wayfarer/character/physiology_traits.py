"""Approved-build projection for Basic Set physiology."""

from collections.abc import Mapping

from pydantic import Field

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.rules.physiology_traits import BINDING_BY_ID, PROFILE, metadata


class PurchasedPhysiology(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()


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
        return (
            None
            if self.has("advantage:doesnt-breathe")
            else 1 << self.level("advantage:breath-holding")
        )

    def lifespan_multiplier(self) -> int | None:
        if self.has("advantage:unaging"):
            return None
        return max(
            1,
            (1 << self.level("advantage:extended-lifespan"))
            // (1 << self.level("disadvantage:short-lifespan")),
        )

    def radiation_divisor(self) -> int:
        value = self.parameter("advantage:radiation-tolerance", "divisor")
        return 1 if value is None else int(value)

    def regeneration_interval(self) -> int | None:
        rate = self.parameter("advantage:regeneration", "rate")
        if rate is not None and not isinstance(rate, str):
            raise ValidationError("Invalid regeneration rate")
        return {None: None, "regular": 3600, "fast": 60, "very-fast": 1, "extreme": 1}[rate]

    def survival_requirements(self) -> frozenset[str]:
        requirements = {"air", "food", "water", "sleep"}
        if self.has("advantage:doesnt-breathe"):
            requirements.remove("air")
        if self.has("advantage:doesnt-eat-or-drink"):
            requirements -= {"food", "water"}
        if self.has("advantage:doesnt-sleep"):
            requirements.remove("sleep")
        return frozenset(requirements)

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
            )
        )
    return PhysiologyTraits(entries=tuple(entries))
