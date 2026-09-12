"""Approved-build projection for Basic Set attack and defense traits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.attack_defense_traits import BINDING_BY_ID, PROFILE, metadata
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.location_types import InjuryTolerance
from wayfarer.models import Record


class PurchasedAttackDefenseTrait(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class AttackDefenseTraits(Record):
    entries: tuple[PurchasedAttackDefenseTrait, ...] = ()

    def purchase(self, identifier: str) -> PurchasedAttackDefenseTrait | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        value = self.purchase(identifier)
        return 0 if value is None else value.levels

    def parameter(self, identifier: str, name: str) -> str | int | bool | None:
        value = self.purchase(identifier)
        return None if value is None else dict(value.parameters).get(name)

    def damage_resistance(self, *, eyes: bool = False) -> int:
        if eyes:
            return self.level("advantage:nictitating-membrane")
        return self.level("advantage:damage-resistance")

    def striking_st(self, base_st: int) -> int:
        return base_st + self.level("advantage:striking-st")

    def injury_tolerance(self) -> str | None:
        value = self.parameter("advantage:injury-tolerance", "kind")
        return None if value is None else str(value)

    def injury_tolerance_profile(self) -> InjuryTolerance | None:
        kind = self.injury_tolerance()
        if kind is None:
            return None
        structures: Mapping[str, Literal["unliving", "homogenous", "diffuse"]] = {
            "unliving": "unliving",
            "homogeneous": "homogenous",
            "diffuse": "diffuse",
        }
        return InjuryTolerance(
            structure=structures[kind],
            no_brain=True,
            no_vitals=True,
            no_neck=kind in {"homogeneous", "diffuse"},
        )

    def injury_multiplier(self, source_rarity: str) -> int:
        purchase = self.purchase("disadvantage:vulnerability")
        if purchase is None or dict(purchase.parameters).get("rarity") != source_rarity:
            return 1
        return int(dict(purchase.parameters)["multiplier"])

    def death_thresholds_ignored(self) -> int:
        if self.purchase("advantage:supernatural-durability") is not None:
            return 5
        return self.level("advantage:unkillable")

    def fragility(self) -> str | None:
        value = self.parameter("disadvantage:fragile", "kind")
        return None if value is None else str(value)

    def natural_attack(self, identifier: str) -> tuple[str, int] | None:
        purchase = self.purchase(identifier)
        if purchase is None:
            return None
        damage_type = dict(purchase.parameters).get("damage-type", "cr")
        return str(damage_type), purchase.levels

    def natural_damage_type(self, identifier: str) -> str | None:
        purchase = self.purchase(identifier)
        if purchase is None:
            return None
        parameters = dict(purchase.parameters)
        if "damage-type" in parameters:
            return str(parameters["damage-type"])
        if identifier == "advantage:claws":
            return "cr" if parameters["kind"] == "blunt" else "cut"
        if identifier == "advantage:teeth":
            return {"blunt": "cr", "sharp": "cut", "fangs": "imp"}[str(parameters["kind"])]
        return {
            "advantage:constriction-attack": "cr",
            "advantage:spines": "imp",
            "advantage:vampiric-bite": "imp",
        }.get(identifier, "cr")


NO_ATTACK_DEFENSE_TRAITS = AttackDefenseTraits()


def attack_defense_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> AttackDefenseTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_ATTACK_DEFENSE_TRAITS
    entries: list[PurchasedAttackDefenseTrait] = []
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
            PurchasedAttackDefenseTrait(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    return AttackDefenseTraits(entries=tuple(entries))
