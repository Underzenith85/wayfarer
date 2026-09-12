"""Pinned Basic Set psionic power groups and Talents (Characters B254-257)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.rules.traits import TraitRules

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-psi-powers"


@dataclass(frozen=True, slots=True)
class PsiPowerBinding:
    id: str
    name: str
    members: tuple[str, ...]
    talent_id: str | None
    talent_cost: int | None
    power_modifier: int

    @property
    def hook(self) -> str:
        return "psi-power:" + self.id.removeprefix("power:")


BINDINGS: Final = (
    PsiPowerBinding(
        "power:antipsi",
        "Antipsi",
        (
            "advantage:neutralize",
            "advantage:obscure",
            "advantage:psi-static",
            "advantage:resistant",
        ),
        None,
        None,
        0,
    ),
    PsiPowerBinding(
        "power:esp",
        "ESP",
        (
            "advantage:channeling",
            "advantage:clairsentience",
            "advantage:danger-sense",
            "advantage:detect",
            "advantage:medium",
            "advantage:oracle",
            "advantage:scanning-sense",
            "advantage:penetrating-vision",
            "advantage:precognition",
            "advantage:psychometry",
            "advantage:racial-memory",
            "advantage:see-invisible",
        ),
        "advantage:esp-talent",
        5,
        -10,
    ),
    PsiPowerBinding(
        "power:psychic-healing",
        "Psychic Healing",
        (
            "advantage:detect",
            "advantage:healing",
            "advantage:metabolism-control",
            "advantage:regeneration",
            "advantage:regrowth",
            "advantage:resistant",
        ),
        "advantage:psychic-healing-talent",
        5,
        -10,
    ),
    PsiPowerBinding(
        "power:psychokinesis",
        "Psychokinesis",
        (
            "advantage:binding",
            "advantage:damage-resistance",
            "advantage:enhanced-move",
            "advantage:flight",
            "advantage:innate-attack",
            "advantage:super-jump",
            "advantage:telekinesis",
            "advantage:temperature-control",
            "advantage:vibration-sense",
            "advantage:walk-on-air",
            "advantage:walk-on-liquid",
        ),
        "advantage:psychokinesis-talent",
        5,
        -10,
    ),
    PsiPowerBinding(
        "power:telepathy",
        "Telepathy",
        (
            "advantage:animal-empathy",
            "advantage:empathy",
            "advantage:invisibility",
            "advantage:mind-control",
            "advantage:mind-probe",
            "advantage:mind-reading",
            "advantage:mind-shield",
            "advantage:mindlink",
            "advantage:possession",
            "advantage:speak-with-animals",
            "advantage:special-rapport",
            "advantage:telecommunication",
            "advantage:terror",
            "advantage:affliction",
            "advantage:innate-attack",
        ),
        "advantage:telepathy-talent",
        5,
        -10,
    ),
    PsiPowerBinding(
        "power:teleportation",
        "Teleportation",
        ("advantage:jumper", "advantage:snatcher", "advantage:warp"),
        "advantage:teleportation-talent",
        5,
        -10,
    ),
)
BINDING_BY_ID: Final = {binding.id: binding for binding in BINDINGS}
RUNTIME_HOOKS: Final = frozenset(binding.hook for binding in BINDINGS)


def package() -> RulesPackage:
    talents = tuple(
        RuleDefinition(
            binding.talent_id,
            DefinitionKind.TRAIT,
            binding.name + " Talent",
            SOURCE_ID,
            binding.talent_cost,
            ImplementationStatus.IMPLEMENTED,
            hooks=("supernatural", "psi-talent"),
            trait_rules=TraitRules(
                PROFILE,
                maximum_level=4,
                runtime_hooks=(binding.hook,),
            ),
        )
        for binding in BINDINGS
        if binding.talent_id is not None and binding.talent_cost is not None
    )
    return RulesPackage(
        PACKAGE_ID,
        "1.0.0",
        "gurps-4e",
        (
            SourceReference(
                SOURCE_ID,
                "GURPS Basic Set: Characters, 4e, third printing",
                "user-supplied-reference",
                "B254-257",
            ),
        ),
        talents,
    )
