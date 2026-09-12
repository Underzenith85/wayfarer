"""Representative numeric definitions; catalog completeness remains issue #112.

Baseline: Lite August 2004 Rev. 07/12/04, pp. 13-18; Basic Set:
Characters Fourth Edition, third printing (February 2008),
B168-173, B179, B187, B208, B220, B222, B224, B230-232.
"""

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute as A,
)
from wayfarer.engine.rules.types.skill import (
    Difficulty as D,
)
from wayfarer.engine.rules.types.skill import SkillDefault, SkillSpec, Specialty, Technique


def definitions(profile_id: str) -> tuple[RuleDefinition, ...]:
    provenance = source(profile_id)
    basic = profile_id == "gurps-basic-set-4e-2004"
    rows = [
        (
            "swimming",
            "Swimming",
            SkillSpec(A.HT, D.EASY, "B224; Lite 18", (SkillDefault(A.HT, -4),)),
        ),
        (
            "stealth",
            "Stealth",
            SkillSpec(
                A.DX, D.AVERAGE, "B222; Lite 18", (SkillDefault(A.DX, -5), SkillDefault(A.IQ, -5))
            ),
        ),
        (
            "diplomacy",
            "Diplomacy",
            SkillSpec(A.IQ, D.HARD, "B187; Lite 15", (SkillDefault(A.IQ, -6),)),
        ),
        (
            "broadsword",
            "Broadsword",
            SkillSpec(A.DX, D.AVERAGE, "B208; Lite 16", (SkillDefault(A.DX, -5),)),
        ),
    ]
    if basic:
        rows.extend(
            [
                (
                    "shortsword",
                    "Shortsword",
                    SkillSpec(
                        A.DX,
                        D.AVERAGE,
                        "B208",
                        (SkillDefault(A.DX, -5), SkillDefault("skill:broadsword", -2)),
                    ),
                ),
                (
                    "observation",
                    "Observation",
                    SkillSpec(A.PER, D.AVERAGE, "B211", (SkillDefault(A.PER, -5),)),
                ),
                (
                    "survival-woodlands",
                    "Survival (Woodlands)",
                    SkillSpec(
                        A.PER,
                        D.AVERAGE,
                        "B223",
                        (SkillDefault(A.PER, -5),),
                        specialty=Specialty("survival", "woodlands"),
                    ),
                ),
                (
                    "physics",
                    "Physics",
                    SkillSpec(A.IQ, D.VERY_HARD, "B213", (SkillDefault(A.IQ, -6),)),
                ),
                (
                    "physics-acoustics",
                    "Physics (Acoustics)",
                    SkillSpec(
                        A.IQ,
                        D.HARD,
                        "B169, B213",
                        specialty=Specialty("physics", "acoustics", "skill:physics"),
                    ),
                ),
                ("karate", "Karate", SkillSpec(A.DX, D.HARD, "B203")),
                ("judo", "Judo", SkillSpec(A.DX, D.HARD, "B203")),
                (
                    "kicking-karate",
                    "Kicking (Karate)",
                    SkillSpec(A.DX, D.HARD, "B232", technique=Technique("skill:karate", -2)),
                ),
                (
                    "arm-lock-judo",
                    "Arm Lock (Judo)",
                    SkillSpec(A.DX, D.AVERAGE, "B230", technique=Technique("skill:judo", 0, 4)),
                ),
            ]
        )
    return tuple(
        RuleDefinition(
            id=f"skill:{key}",
            kind=DefinitionKind.SKILL,
            name=name,
            source_id=provenance.id,
            point_cost=None,
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=spec,
        )
        for key, name, spec in rows
    )
