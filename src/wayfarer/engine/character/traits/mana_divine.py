"""Approved-build projection for Basic Set mana and divine traits."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.rules.traits.mana_divine import BINDING_BY_ID, PROFILE, metadata
from wayfarer.models import Record

MANA_LEVELS: tuple[ManaLevel, ...] = ("none", "low", "normal", "high", "very-high")


class PurchasedManaDivineTrait(Record):
    definition_id: str
    levels: int = Field(ge=1)
    parameters: tuple[tuple[str, str | int | bool], ...] = ()
    modifiers: tuple[str, ...] = ()


class ManaDivineTraits(Record):
    entries: tuple[PurchasedManaDivineTrait, ...] = ()

    def purchase(self, identifier: str) -> PurchasedManaDivineTrait | None:
        return next((entry for entry in self.entries if entry.definition_id == identifier), None)

    def level(self, identifier: str) -> int:
        purchase = self.purchase(identifier)
        return 0 if purchase is None else purchase.levels

    def has(self, identifier: str) -> bool:
        return self.purchase(identifier) is not None

    def magery_level(self) -> int:
        purchase = self.purchase("advantage:magery")
        if purchase is None or dict(purchase.parameters)["zero-only"]:
            return 0
        return purchase.levels

    def has_magery(self) -> bool:
        return self.has("advantage:magery")

    def magery_bonus(
        self,
        *,
        college: str,
        daylight: bool = True,
        darkness: bool = False,
        can_dance: bool = True,
        can_sing: bool = True,
        can_play_instrument: bool = True,
        sapient_nearby: int = 0,
        sapient_touching: bool = False,
    ) -> int:
        purchase = self.purchase("advantage:magery")
        if purchase is None:
            return 0
        modifiers = set(purchase.modifiers)
        selected_college = str(dict(purchase.parameters)["college"])
        available = (
            ("dance" not in modifiers or can_dance)
            and ("dark-aspected" not in modifiers or darkness)
            and ("day-aspected" not in modifiers or daylight)
            and ("musical" not in modifiers or can_play_instrument)
            and ("night-aspected" not in modifiers or not daylight)
            and ("song" not in modifiers or can_sing)
            and ("one-college" not in modifiers or college in (selected_college, "recover-energy"))
        )
        if not available:
            return 0
        level = self.magery_level()
        if "solitary" in modifiers:
            level -= 6 if sapient_touching else 3 * sapient_nearby
        return level

    def magic_casting_modifier(self) -> int:
        """Modifier applied to hostile spells cast directly on this subject."""
        return self.level("disadvantage:magic-susceptibility") - self.level(
            "advantage:magic-resistance"
        )

    def magic_resistance_modifier(self) -> int:
        """Modifier applied to this subject's resistance roll against a spell."""
        return self.level("advantage:magic-resistance") - self.level(
            "disadvantage:magic-susceptibility"
        )

    def may_cast_magic(self) -> bool:
        resistance = self.purchase("advantage:magic-resistance")
        improved = resistance is not None and "improved" in resistance.modifiers
        return not self.has("advantage:mana-damper") and (resistance is None or improved)

    def power_investiture_bonus(self, *, deity_matches: bool, pact_kept: bool) -> int:
        if not deity_matches or not pact_kept:
            return 0
        return self.level("advantage:power-investiture")

    def mana_shift(self, *, damper_enabled: bool = True, enhancer_enabled: bool = True) -> int:
        return (self.level("advantage:mana-enhancer") if enhancer_enabled else 0) - (
            self.level("advantage:mana-damper") if damper_enabled else 0
        )

    def effective_mana(
        self,
        base: ManaLevel,
        *,
        damper_enabled: bool = True,
        enhancer_enabled: bool = True,
    ) -> ManaLevel:
        index = MANA_LEVELS.index(base)
        shifted = max(
            0,
            min(
                len(MANA_LEVELS) - 1,
                index
                + self.mana_shift(
                    damper_enabled=damper_enabled,
                    enhancer_enabled=enhancer_enabled,
                ),
            ),
        )
        return MANA_LEVELS[shifted]

    def visible_traits(self, *, viewer_is_mage: bool, targeted_by_spell: bool) -> tuple[str, ...]:
        if not viewer_is_mage and not targeted_by_spell:
            return ()
        visible = {
            "advantage:magery",
            "advantage:magic-resistance",
            "advantage:mana-enhancer",
            "disadvantage:magic-susceptibility",
        }
        return tuple(
            entry.definition_id for entry in self.entries if entry.definition_id in visible
        )


NO_MANA_DIVINE_TRAITS = ManaDivineTraits()


def mana_divine_traits(
    build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]
) -> ManaDivineTraits:
    if build.statistics is None or build.statistics.profile_id != PROFILE:
        return NO_MANA_DIVINE_TRAITS
    entries: list[PurchasedManaDivineTrait] = []
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
            PurchasedManaDivineTrait(
                definition_id=purchase.definition_id,
                levels=purchase.amount,
                parameters=() if purchase.trait is None else purchase.trait.parameters,
                modifiers=() if purchase.trait is None else purchase.trait.modifiers,
            )
        )
    return ManaDivineTraits(entries=tuple(entries))
