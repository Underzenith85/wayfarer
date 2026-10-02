"""Basic Set movement spell inventory."""

from dataclasses import replace
from typing import Final

from wayfarer.engine.rules.catalog import RulesPackage
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding, college_package, learning_spec
from wayfarer.engine.rules.types.skill import Difficulty, PrerequisiteKind, SkillPrerequisite

ISSUE: Final = 223
COLLEGE: Final = "movement"
# B251/B253: the two lock spells and Lockmaster's sole learned prerequisite.
# Apportation construction is available; this does not implement its effects.
LEARNING: Final = {
    "haste": learning_spec(251),
    "apportation": learning_spec(251, magery=1),
    "lockmaster": learning_spec(251, magery=2, spells=("apportation",)),
    "magelock": learning_spec(253, magery=1),
}
BINDINGS: Final = tuple(
    CollegeSpellBinding(
        key,
        name,
        page,
        COLLEGE,
        learning=LEARNING.get(key),
        learning_college="protection-warning" if key == "magelock" else None,
    )
    for key, name, page in (
        ("apportation", "Apportation", 251),
        ("armor", "Armor", 253),
        ("deflect-missile", "Deflect Missile", 251),
        ("great-haste", "Great Haste", 251),
        ("haste", "Haste", 251),
        ("lockmaster", "Lockmaster", 251),
        ("magelock", "Magelock", 253),
        ("shield", "Shield", 252),
    )
)


def package(*, great_haste: bool = False) -> RulesPackage:
    if not great_haste:
        return college_package(ISSUE, COLLEGE, BINDINGS)
    learned = learning_spec(251, difficulty=Difficulty.VERY_HARD, magery=1, spells=("haste",))
    learned = replace(
        learned,
        prerequisites=learned.prerequisites
        + (SkillPrerequisite("attribute:iq", 12, PrerequisiteKind.ATTRIBUTE_MINIMUM),),
    )
    bindings = tuple(
        replace(binding, learning=learned) if binding.key == "great-haste" else binding
        for binding in BINDINGS
    )
    return replace(college_package(ISSUE, COLLEGE, bindings), version="1.1.0")
