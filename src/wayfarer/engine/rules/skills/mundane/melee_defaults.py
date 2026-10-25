"""Explicit Basic Set B208 melee-family default declaration revisions."""

from dataclasses import replace
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.skills.mundane.source_defaults import recorded_spec
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillDefault
from wayfarer.errors import ValidationError

# Printed Melee Weapon members, not the broader runtime WEAPON_CLASSES mapping:
# Brawling and other unarmed/defense skills do not inherit an attribute default.
MELEE_WEAPON_SKILLS: Final = frozenset(
    {
        "skill:axe-mace",
        "skill:broadsword",
        "skill:flail",
        "skill:force-sword",
        "skill:force-whip",
        "skill:jitte-sai",
        "skill:knife",
        "skill:kusari",
        "skill:main-gauche",
        "skill:monowire-whip",
        "skill:polearm",
        "skill:rapier",
        "skill:saber",
        "skill:shortsword",
        "skill:smallsword",
        "skill:spear",
        "skill:staff",
        "skill:tonfa",
        "skill:two-handed-axe-mace",
        "skill:two-handed-flail",
        "skill:two-handed-sword",
        "skill:whip",
    }
)
PENALTIES: Final = {Difficulty.EASY: -4, Difficulty.AVERAGE: -5, Difficulty.HARD: -6}


def require_revision(revision: int) -> None:
    if type(revision) is not int or revision not in (1, 2):
        raise ValidationError("Unsupported melee default declaration revision")


def revise_definition(definition: RuleDefinition, revision: int) -> RuleDefinition:
    require_revision(revision)
    if revision == 1 or definition.id not in MELEE_WEAPON_SKILLS:
        return definition
    spec = definition.skill
    legacy = recorded_spec(definition.id)
    if (
        spec is None
        or legacy is None
        or spec.attribute is not ControllingAttribute.DX
        or spec.difficulty is not legacy.difficulty
        or spec.difficulty not in PENALTIES
    ):
        raise ValidationError("Melee weapon default requires its source-qualified DX difficulty")
    inherited = SkillDefault(ControllingAttribute.DX, PENALTIES[spec.difficulty])
    corrected = (inherited,) + legacy.defaults
    # Only the exact source-wide shape and the independently published core
    # Broadsword DX-only shape are recognized. A correct DX edge does not
    # authenticate additional author-supplied defaults or their conditions.
    core_broadsword = definition.id == "skill:broadsword" and spec.defaults == (inherited,)
    if spec.defaults == corrected or core_broadsword:
        return definition
    if spec.defaults != legacy.defaults:
        raise ValidationError(
            "Melee weapon has customized default permissions; explicit source review required"
        )
    # Acquisition prerequisites, status, hooks and all particular skill defaults
    # are retained; the new direct edge cannot make an unavailable skill available.
    return replace(definition, skill=replace(spec, defaults=corrected))
