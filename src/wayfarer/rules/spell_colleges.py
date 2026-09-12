"""Shared immutable bindings for Basic Set spell-college packages."""

from __future__ import annotations

from dataclasses import dataclass

from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.rules.skill_types import ControllingAttribute, Difficulty, SkillSpec

PROFILE = "gurps-basic-set-4e-2004"
CHARACTERS_SOURCE = "sjg:basic-set-characters-4e-2004"
CAMPAIGNS_SOURCE = "sjg:basic-set-campaigns-4e-2004"


@dataclass(frozen=True, slots=True)
class CollegeSpellBinding:
    key: str
    name: str
    page: int
    college: str
    source_id: str = CHARACTERS_SOURCE

    @property
    def id(self) -> str:
        return "spell:" + self.key


def college_package(
    issue: int, college: str, bindings: tuple[CollegeSpellBinding, ...]
) -> RulesPackage:
    if not bindings or any(value.college != college for value in bindings):
        raise ValueError("Spell package needs one nonempty college")
    sources = tuple(
        SourceReference(
            source_id,
            (
                "GURPS Basic Set: Campaigns, 4e, fourth printing"
                if source_id == CAMPAIGNS_SOURCE
                else "GURPS Basic Set: Characters, 4e, third printing"
            ),
            "user-supplied-reference",
            "B479-482" if source_id == CAMPAIGNS_SOURCE else "B235-253",
        )
        for source_id in dict.fromkeys(value.source_id for value in bindings)
    )
    return RulesPackage(
        f"package:gurps-basic-spells-{college}",
        "1.0.0",
        "gurps-4e",
        sources,
        tuple(
            RuleDefinition(
                value.id,
                DefinitionKind.SKILL,
                value.name,
                value.source_id,
                1,
                ImplementationStatus.IMPLEMENTED,
                hooks=(
                    "character.gurps-skill",
                    "supernatural",
                    "magic.college-learning",
                    f"spell-college:{college}",
                ),
                skill=SkillSpec(
                    ControllingAttribute.IQ,
                    Difficulty.HARD,
                    f"B{value.page}",
                ),
            )
            for value in bindings
        ),
    )
