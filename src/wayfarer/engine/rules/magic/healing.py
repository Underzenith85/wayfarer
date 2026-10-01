"""Basic Set healing spell inventory."""

from dataclasses import dataclass, replace
from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding, college_package, learning_spec
from wayfarer.engine.rules.types.skill import (
    Difficulty,
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillPrerequisite,
)

ISSUE: Final = 224
COLLEGE: Final = "healing"
# B248 dependencies needed to prove Major/Great Healing construction.
LEARNING: Final = {
    "awaken": learning_spec(248, spells=("lend-vitality",)),
    "lend-energy": replace(
        learning_spec(248),
        prerequisite_groups=(
            PrerequisiteGroup(
                (
                    SkillPrerequisite("trait:magery", 1, PrerequisiteKind.PURCHASED_DEFINITION),
                    SkillPrerequisite(
                        "advantage:empathy", 1, PrerequisiteKind.PURCHASED_DEFINITION
                    ),
                )
            ),
        ),
    ),
    "recover-energy": learning_spec(248, magery=1, spells=("lend-energy",)),
    "lend-vitality": learning_spec(248, spells=("lend-energy",)),
    "minor-healing": learning_spec(248, spells=("lend-vitality",)),
    "major-healing": learning_spec(
        248, difficulty=Difficulty.VERY_HARD, magery=1, spells=("minor-healing",)
    ),
    "great-healing": learning_spec(
        248, difficulty=Difficulty.VERY_HARD, magery=3, spells=("major-healing",)
    ),
}
BINDINGS: Final = tuple(
    CollegeSpellBinding(key, name, page, COLLEGE, learning=LEARNING.get(key))
    for key, name, page in (
        ("awaken", "Awaken", 248),
        ("great-healing", "Great Healing", 248),
        ("lend-energy", "Lend Energy", 248),
        ("lend-vitality", "Lend Vitality", 248),
        ("major-healing", "Major Healing", 248),
        ("minor-healing", "Minor Healing", 248),
        ("recover-energy", "Recover Energy", 248),
    )
)


def package() -> RulesPackage:
    return college_package(ISSUE, COLLEGE, BINDINGS)


@dataclass(frozen=True, slots=True)
class PassiveRecoverySpec:
    """Source metadata for a learned passive ability, never a cast command."""

    id: str = "spell:recover-energy"
    target: str = "caster"
    cost: int = 0
    casting_roll: bool = False
    duration: str = "permanent"
    minimum_skill: int = 15
    interval: int = 300
    improved_skill: int = 20
    improved_interval: int = 120
    mana: tuple[str, ...] = ("normal", "high", "very-high")
    reference: str = "B248"


RECOVER_ENERGY: Final = PassiveRecoverySpec()
