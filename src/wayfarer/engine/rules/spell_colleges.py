"""Shared immutable bindings for Basic Set spell-college packages."""

from __future__ import annotations

from dataclasses import dataclass

from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.gurps_magic import definitions as historic_magic_definitions
from wayfarer.engine.rules.skill_types import ControllingAttribute, Difficulty, SkillSpec

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
    historic_all = {definition.id: definition for definition in historic_magic_definitions(2)}
    historic = {
        key: definition for key, definition in historic_all.items() if key.startswith("spell:")
    }
    spell_definitions = tuple(
        historic.get(value.id)
        or RuleDefinition(
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
    )
    required = {ref for definition in spell_definitions for ref in definition.prerequisites}
    spell_ids = {definition.id for definition in spell_definitions}
    supporting = tuple(
        historic_all[key] for key in historic_all if key in required and key not in spell_ids
    )
    required.update(ref for definition in supporting for ref in definition.prerequisites)
    supporting = tuple(
        historic_all[key] for key in historic_all if key in required and key not in spell_ids
    )
    return RulesPackage(
        f"package:gurps-basic-spells-{college}",
        "1.0.0",
        "gurps-4e",
        sources,
        supporting + spell_definitions,
    )
