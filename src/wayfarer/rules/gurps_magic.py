"""Pinned spell learning mechanics, separate from spell execution coverage.

Provisional numeric references: B66-67, B235, B246-247, B249-250.
The frozen first-printing source audit remains pending under #119/#171.
"""

from collections.abc import Mapping
from types import MappingProxyType

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.skill_types import (
    ControllingAttribute,
    Difficulty,
    SkillPrerequisite,
    SkillSpec,
)
from wayfarer.rules.traits import TraitRules

PROFILE = "gurps-basic-set-4e-2004"
SOURCE = "sjg:basic-set-characters-4e-2004"
MAGERY_ZERO = "trait:magery-0"
MAGERY = "trait:magery"
# Learning these prerequisites does not grant an executable spell command.
PREREQUISITES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "light": (),
        "foolishness": (),
        "daze": ("foolishness",),
        "ignite-fire": (),
        "create-fire": ("ignite-fire",),
        "shape-fire": ("ignite-fire",),
        "fireball": ("create-fire", "shape-fire"),
    }
)
REFERENCES = {
    "light": "B249",
    "foolishness": "B250",
    "daze": "B250",
    "ignite-fire": "B246",
    "create-fire": "B246",
    "shape-fire": "B246",
    "fireball": "B247",
}


def definitions(version: int = 1) -> tuple[RuleDefinition, ...]:
    """Version 1 preserves historical profile pins; version 2 follows B235."""
    if version not in (1, 2):
        raise ValidationError("Unknown magic learning revision")
    return (
        RuleDefinition(
            MAGERY_ZERO,
            DefinitionKind.TRAIT,
            "Magery 0",
            SOURCE,
            5,
            ImplementationStatus.IMPLEMENTED,
            hooks=("supernatural", "magic.learning"),
        ),
        RuleDefinition(
            MAGERY,
            DefinitionKind.TRAIT,
            "Magery",
            SOURCE,
            10,
            ImplementationStatus.IMPLEMENTED,
            prerequisites=(MAGERY_ZERO,),
            hooks=("supernatural", "magic.learning"),
            trait_rules=TraitRules(PROFILE, maximum_level=100),
        ),
    ) + tuple(
        RuleDefinition(
            "spell:" + name,
            DefinitionKind.SKILL,
            name.replace("-", " ").title(),
            SOURCE,
            1,
            ImplementationStatus.IMPLEMENTED,
            prerequisites=((MAGERY,) if name == "fireball" else ())
            + (tuple("spell:" + p for p in parents) if version == 2 else ()),
            hooks=("character.gurps-skill", "supernatural", "magic.learning"),
            skill=SkillSpec(
                ControllingAttribute.IQ,
                Difficulty.HARD,
                REFERENCES[name],
                prerequisites=tuple(SkillPrerequisite("spell:" + p, 12) for p in parents)
                if version == 1
                else (),
            ),
        )
        for name, parents in PREREQUISITES.items()
    )


def validate_definitions(profile_id: str | None, entries: Mapping[str, RuleDefinition]) -> None:
    versions = tuple({d.id: d for d in definitions(v)} for v in (1, 2))
    selected = {
        key: entry
        for key, entry in entries.items()
        if key in versions[0] or "magic.learning" in entry.hooks
    }
    if selected and (
        profile_id != PROFILE
        or not any(
            all(expected.get(key) == entry for key, entry in selected.items())
            for expected in versions
        )
    ):
        raise ValidationError("Magic learning requires its exact Basic Set catalog binding")


def magery_level(purchases: Mapping[str, int]) -> int:
    """-1 is nonmage; Magery 0 gives access but no skill bonus."""
    return purchases.get(MAGERY, 0) if MAGERY_ZERO in purchases else -1
