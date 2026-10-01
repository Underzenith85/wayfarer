"""Shared immutable bindings for Basic Set spell-college packages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.magic.gurps_magic import definitions as historic_magic_definitions
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute,
    Difficulty,
    PrerequisiteKind,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
)

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
    # Absent means source-specific construction has not been reviewed.
    # Inventory membership alone must never invent difficulty or prerequisites.
    learning: SkillSpec | None = None
    # Inventory packages may group several source colleges. Learned spells use
    # their printed college for distinct-college prerequisites (B235/B480).
    learning_college: str | None = None

    @property
    def id(self) -> str:
        return "spell:" + self.key


def college_package(
    issue: int, college: str, bindings: tuple[CollegeSpellBinding, ...]
) -> RulesPackage:
    if not bindings or any(value.college != college for value in bindings):
        raise ValueError("Spell package needs one nonempty college")
    source_ids = tuple(dict.fromkeys(value.source_id for value in bindings))
    if (
        any(value.learning is not None for value in bindings)
        and CHARACTERS_SOURCE not in source_ids
    ):
        source_ids += (CHARACTERS_SOURCE,)
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
        for source_id in source_ids
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
            ImplementationStatus.IMPLEMENTED
            if value.learning is not None
            else ImplementationStatus.MANUAL,
            hooks=(
                "character.gurps-skill",
                "supernatural",
                "magic.college-learning",
                f"spell-college:{value.learning_college or college}",
            ),
            skill=value.learning,
        )
        for value in bindings
    )
    required = {ref for definition in spell_definitions for ref in definition.prerequisites}
    if any(value.learning is not None for value in bindings):
        required.update(("trait:magery", "trait:magery-0"))
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


def learning_spec(
    page: int,
    *,
    difficulty: Difficulty = Difficulty.HARD,
    magery: int = 0,
    spells: tuple[str, ...] = (),
    colleges: int = 0,
    specialty: Specialty | None = None,
    excluded_colleges: tuple[str, ...] = (),
) -> SkillSpec:
    """Explicit selected-printing metadata; spell prerequisites require points (B235)."""
    return SkillSpec(
        ControllingAttribute.IQ,
        difficulty,
        f"B{page}",
        prerequisites=(
            (SkillPrerequisite("trait:magery", magery, PrerequisiteKind.PURCHASED_DEFINITION),)
            if magery
            else ()
        )
        + tuple(
            SkillPrerequisite("spell:" + name, 1, PrerequisiteKind.PURCHASED_DEFINITION)
            for name in spells
        ),
        specialty=specialty,
        minimum_spell_colleges=colleges,
        excluded_spell_colleges=excluded_colleges,
    )


def college_prerequisite_failures(
    definitions: Mapping[str, RuleDefinition], purchases: Mapping[str, int]
) -> tuple[str, ...]:
    """Count learned, implemented spells by their pinned college, never skill level."""
    return tuple(
        key
        for key in purchases
        if (definition := definitions.get(key)) is not None
        and (spec := definition.skill) is not None
        and spec.minimum_spell_colleges
        and len(
            {
                college
                for learned in purchases
                if learned != key
                if (entry := definitions.get(learned)) is not None
                and entry.status is ImplementationStatus.IMPLEMENTED
                and entry.skill is not None
                for college in spell_colleges(entry)
            }
            - set(spec.excluded_spell_colleges)
        )
        < spec.minimum_spell_colleges
    )


def spell_colleges(definition: RuleDefinition) -> frozenset[str]:
    """Historic pins retain their digest; their source college identity is known."""
    historic = {
        "spell:light": "light-darkness",
        "spell:foolishness": "mind-control",
        "spell:daze": "mind-control",
        "spell:ignite-fire": "fire",
        "spell:create-fire": "fire",
        "spell:shape-fire": "fire",
        "spell:fireball": "fire",
    }
    return frozenset(
        hook.removeprefix("spell-college:")
        for hook in definition.hooks
        if hook.startswith("spell-college:")
    ) | (frozenset((historic[definition.id],)) if definition.id in historic else frozenset())
