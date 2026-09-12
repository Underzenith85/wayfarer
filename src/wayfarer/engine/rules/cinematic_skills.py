"""Pinned Basic Set cinematic skills (Characters B180-228)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.skill_types import (
    ControllingAttribute as A,
)
from wayfarer.engine.rules.skill_types import (
    Difficulty as D,
)
from wayfarer.engine.rules.skill_types import (
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillPrerequisite,
    SkillSpec,
)

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-cinematic-skills"


def _skill(name: str) -> str:
    return "skill:" + name


def _trained(name: str, minimum: int = 1) -> SkillPrerequisite:
    return SkillPrerequisite(_skill(name), minimum)


def _purchased(name: str, minimum: int = 1) -> SkillPrerequisite:
    return SkillPrerequisite(name, minimum, PrerequisiteKind.PURCHASED_DEFINITION)


MASTER = PrerequisiteGroup(
    (
        _purchased("advantage:trained-by-a-master"),
        _purchased("advantage:weapon-master"),
    )
)


@dataclass(frozen=True, slots=True)
class CinematicSkillBinding:
    key: str
    name: str
    attribute: A
    difficulty: D
    page: int
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    prerequisite_groups: tuple[PrerequisiteGroup, ...] = ()
    technology_level_required: bool = False

    @property
    def id(self) -> str:
        return _skill(self.key)


BINDINGS: Final = (
    CinematicSkillBinding(
        "blind-fighting", "Blind Fighting", A.PER, D.VERY_HARD, 180, prerequisite_groups=(MASTER,)
    ),
    CinematicSkillBinding(
        "body-control",
        "Body Control",
        A.HT,
        D.VERY_HARD,
        181,
        (
            _trained("breath-control"),
            _trained("meditation"),
            _purchased("advantage:trained-by-a-master"),
        ),
    ),
    CinematicSkillBinding(
        "breaking-blow", "Breaking Blow", A.IQ, D.HARD, 182, prerequisite_groups=(MASTER,)
    ),
    CinematicSkillBinding(
        "captivate", "Captivate", A.WILL, D.HARD, 191, (_trained("suggest", 12),)
    ),
    CinematicSkillBinding(
        "computer-hacking",
        "Computer Hacking/TL",
        A.IQ,
        D.VERY_HARD,
        184,
        technology_level_required=True,
    ),
    CinematicSkillBinding(
        "enthrallment",
        "Enthrallment",
        A.WILL,
        D.HARD,
        191,
        (_purchased("trait:charisma"), _trained("public-speaking", 12)),
    ),
    CinematicSkillBinding(
        "flying-leap",
        "Flying Leap",
        A.IQ,
        D.HARD,
        196,
        (_trained("jumping"), _trained("power-blow")),
        (MASTER,),
    ),
    CinematicSkillBinding(
        "immovable-stance",
        "Immovable Stance",
        A.DX,
        D.HARD,
        201,
        (_purchased("advantage:trained-by-a-master"),),
    ),
    CinematicSkillBinding(
        "invisibility-art",
        "Invisibility Art",
        A.IQ,
        D.VERY_HARD,
        202,
        prerequisite_groups=(MASTER,),
    ),
    CinematicSkillBinding("kiai", "Kiai", A.HT, D.HARD, 203, prerequisite_groups=(MASTER,)),
    CinematicSkillBinding(
        "light-walk",
        "Light Walk",
        A.DX,
        D.HARD,
        205,
        (
            _trained("acrobatics", 14),
            _trained("stealth", 14),
            _purchased("advantage:trained-by-a-master"),
        ),
    ),
    CinematicSkillBinding("mental-strength", "Mental Strength", A.WILL, D.EASY, 209),
    CinematicSkillBinding(
        "musical-influence",
        "Musical Influence",
        A.IQ,
        D.VERY_HARD,
        210,
        (_purchased("advantage:musical-ability"),),
        (PrerequisiteGroup((_trained("musical-instrument", 12), _trained("singing", 12))),),
    ),
    CinematicSkillBinding("persuade", "Persuade", A.WILL, D.HARD, 191),
    CinematicSkillBinding(
        "power-blow", "Power Blow", A.WILL, D.HARD, 215, prerequisite_groups=(MASTER,)
    ),
    CinematicSkillBinding(
        "pressure-points", "Pressure Points", A.IQ, D.HARD, 215, prerequisite_groups=(MASTER,)
    ),
    CinematicSkillBinding(
        "pressure-secrets",
        "Pressure Secrets",
        A.IQ,
        D.VERY_HARD,
        215,
        (_trained("pressure-points", 16), _purchased("advantage:trained-by-a-master")),
    ),
    CinematicSkillBinding("push", "Push", A.DX, D.HARD, 216, prerequisite_groups=(MASTER,)),
    CinematicSkillBinding("suggest", "Suggest", A.WILL, D.HARD, 191, (_trained("persuade", 12),)),
    CinematicSkillBinding(
        "sway-emotions", "Sway Emotions", A.WILL, D.HARD, 192, (_trained("persuade", 12),)
    ),
    CinematicSkillBinding(
        "throwing-art", "Throwing Art", A.DX, D.HARD, 226, prerequisite_groups=(MASTER,)
    ),
    CinematicSkillBinding("weird-science", "Weird Science", A.IQ, D.VERY_HARD, 228),
    CinematicSkillBinding(
        "zen-archery",
        "Zen Archery",
        A.IQ,
        D.VERY_HARD,
        228,
        (_trained("bow", 18), _trained("meditation")),
        (MASTER,),
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
                "B180-228",
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
                hooks=("character.gurps-skill", "check.target", "cinematic-skill"),
                skill=SkillSpec(
                    binding.attribute,
                    binding.difficulty,
                    f"B{binding.page}",
                    prerequisites=binding.prerequisites,
                    prerequisite_groups=binding.prerequisite_groups,
                    technology_level_required=binding.technology_level_required,
                ),
            )
            for binding in BINDINGS
        ),
    )
