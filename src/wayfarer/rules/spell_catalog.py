"""Projectile skill for the opt-in spell combat adapter (B201)."""

from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.gurps_magic import SOURCE
from wayfarer.rules.skill_types import ControllingAttribute, Difficulty, SkillDefault, SkillSpec


def projectile_definition() -> RuleDefinition:
    return RuleDefinition(
        "skill:innate-attack-projectile",
        DefinitionKind.SKILL,
        "Innate Attack (Projectile)",
        SOURCE,
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill",),
        skill=SkillSpec(
            ControllingAttribute.DX,
            Difficulty.EASY,
            "B201",
            defaults=(SkillDefault("attribute:dx", -4),),
        ),
    )
