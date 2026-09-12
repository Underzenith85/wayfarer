"""Approved-build projection for Basic Set mental and spirit capabilities."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.rules.mental_spirit_traits import BINDING_BY_ID, PROFILE, metadata


class PurchasedMentalSpiritTrait(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class MentalSpiritTraits(Record):
    entries: tuple[PurchasedMentalSpiritTrait, ...] = ()

    def purchase(self, identifier: str) -> PurchasedMentalSpiritTrait | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        value = self.purchase(identifier)
        return 0 if value is None else value.levels

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        value = self.purchase(identifier)
        return None if value is None else dict(value.parameters).get(name)

    def mind_shield_bonus(self) -> int:
        return self.level("advantage:mind-shield")

    def simultaneous_concentrations(self) -> int:
        return 1 + self.level("advantage:compartmentalized-mind")

    def higher_purpose_bonus(self, *, applies: bool) -> int:
        return self.level("advantage:higher-purpose") if applies else 0

    def terror_penalty(self) -> int:
        value = self.parameter("advantage:terror", "penalty")
        return 0 if value is None else -int(value)

    def can_contact(self, subject: str) -> bool:
        mapping = {
            "spirits": ("advantage:medium", "advantage:spirit-empathy"),
            "ancestors": ("advantage:racial-memory",),
            "past": ("advantage:psychometry",),
            "future": ("advantage:precognition", "advantage:oracle"),
        }
        if subject not in mapping:
            raise ValidationError("Unknown mental/spirit contact subject")
        return any(self.has(identifier) for identifier in mapping[subject])

    def resists(self, family: str) -> bool:
        return family == "psi" and self.has("advantage:psi-static")


NO_MENTAL_SPIRIT_TRAITS = MentalSpiritTraits()


def mental_spirit_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> MentalSpiritTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_MENTAL_SPIRIT_TRAITS
    entries: list[PurchasedMentalSpiritTrait] = []
    for purchase in build.trait_purchases:
        binding = BINDING_BY_ID.get(purchase.definition_id)
        definition = definitions.get(purchase.definition_id)
        if (
            binding is None
            or definition is None
            or definition.status is not ImplementationStatus.IMPLEMENTED
            or definition.trait_rules != metadata(binding)
            or binding.hook not in definition.trait_rules.runtime_hooks
        ):
            continue
        entries.append(
            PurchasedMentalSpiritTrait(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    return MentalSpiritTraits(entries=tuple(entries))
