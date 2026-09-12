"""Approved-build projection for Basic Set sensory capabilities."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.sensory_traits import BINDING_BY_ID, PROFILE, metadata
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PurchasedSense(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class SensoryTraits(Record):
    entries: tuple[PurchasedSense, ...] = ()

    def purchase(self, identifier: str) -> PurchasedSense | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        value = self.purchase(identifier)
        return 0 if value is None else value.levels

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        value = self.purchase(identifier)
        return None if value is None else dict(value.parameters).get(name)

    def visual_darkness(self, penalty: int) -> int:
        if type(penalty) is not int or not -10 <= penalty <= 0:
            raise ValidationError("Darkness must be within -10..0")
        return 0 if self.has("advantage:dark-vision") else penalty

    def aimed_vision_bonus(self) -> int:
        return 2 * self.level("advantage:telescopic-vision")

    def microscopic_detail(self) -> int:
        return self.level("advantage:microscopic-vision")

    def hearing_range_multiplier(self) -> int:
        return 1 << self.level("advantage:parabolic-hearing")

    def camouflage_bonus(self, *, moving: bool) -> int:
        levels = self.level("advantage:chameleon")
        return levels if moving else 2 * levels

    def silence_penalty(self) -> int:
        return -self.level("advantage:silence")

    def protected(self, sense: str) -> bool:
        return self.parameter("advantage:protected-sense", "sense") == sense

    def can_communicate(self, medium: str) -> bool:
        return (
            medium == self.parameter("advantage:telecommunication", "kind")
            or medium == "underwater"
            and self.has("advantage:speak-underwater")
            or medium == "plants"
            and self.has("advantage:speak-with-plants")
            or medium == "animals"
            and self.has("advantage:speak-with-animals")
            or medium == "subsonic"
            and bool(self.parameter("advantage:subsonic-speech", "native") is not None)
            or medium == "ultrasonic"
            and bool(self.parameter("advantage:ultrasonic-speech", "native") is not None)
        )


NO_SENSORY_TRAITS = SensoryTraits()


def sensory_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> SensoryTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_SENSORY_TRAITS
    entries: list[PurchasedSense] = []
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
            PurchasedSense(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    return SensoryTraits(entries=tuple(entries))
