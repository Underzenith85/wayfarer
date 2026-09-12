"""Approved-build projection for Basic Set world-travel traits."""

from __future__ import annotations

from collections.abc import Mapping

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.world_travel_traits import BINDING_BY_ID, PROFILE, metadata
from wayfarer.models import Record


class PurchasedWorldTravelTrait(Record):
    definition_id: str
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class WorldTravelTraits(Record):
    entries: tuple[PurchasedWorldTravelTrait, ...] = ()

    def purchase(self, identifier: str) -> PurchasedWorldTravelTrait | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        value = self.purchase(identifier)
        return None if value is None else dict(value.parameters).get(name)

    def jumper_kind(self) -> str | None:
        value = self.parameter("advantage:jumper", "kind")
        return None if value is None else str(value)

    def snatcher_weight(self) -> int:
        value = self.parameter("advantage:snatcher", "weight")
        return 0 if value is None else int(value)

    def warp_reliability(self) -> int:
        value = self.parameter("advantage:warp", "reliability")
        return 0 if value is None else int(value)


NO_WORLD_TRAVEL_TRAITS = WorldTravelTraits()


def world_travel_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> WorldTravelTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_WORLD_TRAVEL_TRAITS
    entries: list[PurchasedWorldTravelTrait] = []
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
            PurchasedWorldTravelTrait(
                definition_id=purchase.definition_id,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    return WorldTravelTraits(entries=tuple(entries))
