"""Pinned Basic Set magic-craft skills (Characters B174-225)."""

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
from wayfarer.rules.skill_types import (
    ControllingAttribute as A,
)
from wayfarer.rules.skill_types import (
    DefaultCondition,
    DefaultConditionKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
)
from wayfarer.rules.skill_types import (
    Difficulty as D,
)

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-magic-craft-skills"


@dataclass(frozen=True, slots=True)
class MagicCraftBinding:
    key: str
    name: str
    page: int
    difficulty: D
    technology_level_required: bool = False
    specialty_required: bool = False
    defaults: tuple[SkillDefault, ...] = ()
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    modes: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return "skill:" + self.key


BINDINGS: Final = (
    MagicCraftBinding(
        "alchemy",
        "Alchemy/TL",
        174,
        D.VERY_HARD,
        technology_level_required=True,
        modes=("analyze", "prepare"),
    ),
    MagicCraftBinding(
        "herb-lore",
        "Herb Lore/TL",
        199,
        D.VERY_HARD,
        technology_level_required=True,
        prerequisites=(SkillPrerequisite("skill:naturalist"),),
        modes=("locate", "prepare"),
    ),
    MagicCraftBinding(
        "ritual-magic",
        "Ritual Magic",
        218,
        D.VERY_HARD,
        specialty_required=True,
        defaults=(
            SkillDefault(
                "skill:religious-ritual",
                -6,
                (DefaultCondition(DefaultConditionKind.MATCHING_SPECIALTY),),
            ),
        ),
        modes=("identify", "invoke"),
    ),
    MagicCraftBinding(
        "symbol-drawing",
        "Symbol Drawing",
        224,
        D.HARD,
        specialty_required=True,
        modes=("inscribe-focus", "inscribe-potency"),
    ),
    MagicCraftBinding(
        "thaumatology",
        "Thaumatology",
        225,
        D.VERY_HARD,
        defaults=(
            SkillDefault(
                A.IQ,
                -7,
                (DefaultCondition(DefaultConditionKind.CAMPAIGN_SELECTED),),
            ),
        ),
        modes=("identify", "research"),
    ),
)
BINDING_BY_ID: Final = {binding.id: binding for binding in BINDINGS}


def package() -> RulesPackage:
    return RulesPackage(
        PACKAGE_ID,
        "1.0.0",
        "gurps-4e",
        (
            SourceReference(
                SOURCE_ID,
                "GURPS Basic Set: Characters, 4e, third printing",
                "user-supplied-reference",
                "B174-225",
            ),
        ),
        tuple(
            RuleDefinition(
                binding.id,
                DefinitionKind.SKILL,
                binding.name,
                SOURCE_ID,
                None,
                ImplementationStatus.IMPLEMENTED,
                hooks=("character.gurps-skill", "check.target", "supernatural", "magic-craft"),
                skill=SkillSpec(
                    A.IQ,
                    binding.difficulty,
                    f"B{binding.page}",
                    defaults=binding.defaults,
                    prerequisites=binding.prerequisites,
                    technology_level_required=binding.technology_level_required,
                ),
            )
            for binding in BINDINGS
        ),
    )
